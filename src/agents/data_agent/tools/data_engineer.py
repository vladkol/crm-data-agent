# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Data Engineer Agent"""

from functools import cache
import json
import os
from pathlib import Path
import uuid
from typing import Tuple

from pydantic import BaseModel

from google.cloud.exceptions import BadRequest, NotFound
from google.cloud.bigquery import Client, QueryJobConfig
from google.genai.types import (Content,
                                GenerateContentConfig,
                                Part,
                                SafetySetting)
from google.adk.tools import ToolContext

from .utils import get_genai_client
from prompts.data_engineer import (system_instruction
                                   as data_engineer_instruction,
                                   prompt as data_engineer_prompt)
from prompts.sql_correction import (instruction as sql_correction_instruction,
                                    prompt as sql_correction_prompt)


DATA_ENGINEER_AGENT_MODEL_ID = "gemini-2.5-pro-preview-05-06"
SQL_VALIDATOR_MODEL_ID =  "gemini-2.5-pro-preview-05-06"
_DEFAULT_METADATA_FILE = "sfdc_metadata.json"

@cache
def _init_environment():
    global _bq_project_id, _data_project_id, _location, _dataset
    global _sfdc_metadata, _sfdc_metadata_dict, _sfdc_metadata

    _bq_project_id = os.environ["BQ_PROJECT_ID"]
    _data_project_id = os.environ["SFDC_DATA_PROJECT_ID"]
    _location = os.environ["BQ_LOCATION"]
    _dataset = os.environ["SFDC_BQ_DATASET"]
    _sfdc_metadata_path = os.environ.get("SFDC_METADATA_FILE",
                                        _DEFAULT_METADATA_FILE)
    if not Path(_sfdc_metadata_path).exists():
        if "/" not in _sfdc_metadata_path:
            _sfdc_metadata_path = str(Path(__file__).parent.parent /
                                    _sfdc_metadata_path)

    _sfdc_metadata = Path(_sfdc_metadata_path).read_text(encoding="utf-8")
    _sfdc_metadata_dict = json.loads(_sfdc_metadata)

    # Only keep metadata for tables that exist in the dataset.
    _final_dict = {}
    client = Client(_bq_project_id, location=_location)
    for table in client.list_tables(f"{_data_project_id}.{_dataset}"):
        if table.table_id in _sfdc_metadata_dict:
            table_dict = _sfdc_metadata_dict[table.table_id]
            _final_dict[table.table_id] = table_dict
            table_obj = client.get_table(f"{_data_project_id}.{_dataset}."
                                        f"{table.table_id}")
            for f in table_obj.schema:
                if f.name in table_dict["columns"]:
                    table_dict["columns"][f.name]["field_type"] = f.field_type

    _sfdc_metadata = json.dumps(_final_dict, indent=2)
    _sfdc_metadata_dict = _final_dict


def _sql_validator(sql_code: str) -> Tuple[str, str]:
    """SQL Validator. Validates BigQuery SQL query using BigQuery client.
    May also change the query to correct known errors in-place.

        Args:
        sql_code (str): BigQuery SQL code to validate.

    Returns:
        tuple(str,str):
            str: "SUCCESS" if SQL is valid, error text otherwise.
            str: modified SQL code (always update your original query with it).
    """
    print("Running SQL validator.")
    sql_code_to_run = sql_code
    for k,v in _sfdc_metadata_dict.items():
        sfdc_name = v["salesforce_name"]
        full_name = f"`{_data_project_id}.{_dataset}.{sfdc_name}`"
        sql_code_to_run = sql_code_to_run.replace(
            full_name,
            f"`{_data_project_id}.{_dataset}.{k}`"
        )

    client = Client(project=_bq_project_id, location=_location)
    try:
        dataset_location = client.get_dataset(
                                f"{_data_project_id}.{_dataset}").location
        job_config = QueryJobConfig(dry_run=True, use_query_cache=False)
        client.query(sql_code,
                     job_config=job_config,
                     location=dataset_location).result()
    except (BadRequest, NotFound) as ex:
        err_text = ex.args[0].strip()
        return f"ERROR: {err_text}", sql_code_to_run
    return "SUCCESS", sql_code_to_run


class SQLResult(BaseModel):
    sql_code: str
    error: str = ""


######## AGENT ########
async def data_engineer(request: str, tool_context: ToolContext) -> SQLResult:
    """
    This is your Senior Data Engineer.
    They have extensive experience in working with CRM data.
    They write clean and efficient SQL in its BigQuery dialect.
    When given a question or a set of steps,
    they can understand whether the problem can be solved with the data you have.
    The result is a BigQuery SQL Query.
    """
    _init_environment()
    prompt = data_engineer_prompt.format(
        request=request,
        data_project_id=_data_project_id,
        dataset=_dataset,
        sfdc_metadata=_sfdc_metadata
    )

    sql_code_result = get_genai_client().models.generate_content(
        model=DATA_ENGINEER_AGENT_MODEL_ID,
        contents=Content(
            role="user",
            parts=[
                Part.from_text(text=prompt)
            ]
        ),
        config=GenerateContentConfig(
            response_schema=SQLResult,
            response_mime_type="application/json",
            system_instruction=data_engineer_instruction,
            temperature=0.0,
            top_p=0.0,
            seed=1,
            safety_settings=[
                SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                    threshold="BLOCK_ONLY_HIGH", # type: ignore
                ),
            ]
        )
    )
    sql_result: SQLResult = sql_code_result.parsed # type: ignore
    sql = sql_result.sql_code

    print(f"SQL Query candidate: {sql}")

    MAX_FIX_ATTEMPTS = 32
    validating_query = sql
    is_good = False

    for __ in range(MAX_FIX_ATTEMPTS):
        chat_session = None
        validator_result, validating_query = _sql_validator(validating_query)
        print(f"SQL Query candidate: {validating_query}")
        if validator_result == "SUCCESS":
            is_good = True
            break
        print(f"ERROR: {validator_result}")
        if not chat_session:
            chat_session = get_genai_client().chats.create(
                model=SQL_VALIDATOR_MODEL_ID,
                config=GenerateContentConfig(
                    response_schema=SQLResult,
                    response_mime_type="application/json",
                    system_instruction=sql_correction_instruction.format(
                        data_project_id=_data_project_id,
                        dataset=_dataset,
                        sfdc_metadata=_sfdc_metadata
                    ),
                    temperature=0.0,
                    top_p=0.000001,
                    seed=0,
                    safety_settings=[
                        SafetySetting(
                            category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                            threshold="BLOCK_ONLY_HIGH", # type: ignore
                        ),
                    ]
                )
            )
        correcting_prompt = sql_correction_prompt.format(
            validating_query=validating_query,
            validator_result=validator_result
        )
        corr_result = chat_session.send_message(correcting_prompt).parsed
        validating_query = corr_result.sql_code # type: ignore
    if is_good:
        print(f"Final result: {validating_query}")
        sql_markdown = f"```sql\n{validating_query}\n```"
        await tool_context.save_artifact(
            f"query_{uuid.uuid4().hex}.md",
            Part.from_bytes(
                mime_type="text/markdown",
                data=sql_markdown.encode("utf-8")
            )
        )
        return SQLResult(sql_code=validating_query)
    else:
        return SQLResult(
            sql_code="-- no query",
            error=f"## Could not create a valid query in {MAX_FIX_ATTEMPTS}"
                   " attempts.")
