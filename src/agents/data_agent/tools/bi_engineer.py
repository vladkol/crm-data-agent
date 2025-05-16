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
"""BI Engineer Agent"""

from functools import cache
import io
import json
import jsonschema
from pathlib import Path
import os

from pydantic import BaseModel

from google.adk.tools import ToolContext
from google.genai.types import GenerateContentConfig, Part, SafetySetting
from google.cloud.bigquery import Client, QueryJobConfig
from google.cloud.exceptions import BadRequest, NotFound

import altair as alt
import pandas as pd

from .utils import get_genai_client
from prompts.bi_engineer import prompt as bi_engineer_prompt
from tools.chart_evaluator import evaluate_chart


MAX_RESULT_ROWS_DISPLAY = 50
BI_ENGINEER_AGENT_MODEL_ID = "gemini-2.5-pro-preview-05-06"
BI_ENGINEER_FIX_AGENT_MODEL_ID = "gemini-2.5-pro-preview-05-06"


@cache
def _init_environment():
    global _bq_project_id, _data_project_id, _location, _dataset
    _bq_project_id = os.environ["BQ_PROJECT_ID"]
    _data_project_id = os.environ["SFDC_DATA_PROJECT_ID"]
    _location = os.environ["BQ_LOCATION"]
    _dataset = os.environ["SFDC_BQ_DATASET"]

class VegaResult(BaseModel):
    vega_lite_4_json: str
    diagram_code_explanation: str = ""


def _enhance_parameters(vega_chart: dict, df: pd.DataFrame) -> dict:
    """
    Makes sure all chart parameters with "select" equal to "point"
    have the same option values as respective dimensions.

    Args:
        vega_chart_json (str): _description_
        df (pd.DataFrame): _description_

    Returns:
        str: _description_
    """
    if "params" not in vega_chart:
        return vega_chart
    if "params" not in vega_chart or "'transform':" not in str(vega_chart):
        print("Cannot enhance parameters because one or "
              "more of these are missing: "
              "[params, transform]")
        return vega_chart
    print("Enhancing parameters...")
    params_list = vega_chart["params"]
    params = { p["name"]: p for p in params_list }
    for p in params:
        if not p.endswith("__selection"):
            continue
        print(f"Parameter {p}")
        param_dict = params[p]
        column_name = p.split("__selection")[0]
        if column_name not in df.columns:
            print(f"Column {column_name} not found in dataframe.")
            continue
        field_values = df[column_name].unique().tolist()
        if None not in field_values:
            field_values.insert(0, None)
            none_index = 0
        else:
            none_index = field_values.index(None)
        param_dict["value"] = None
        param_dict["bind"] = {"input": "select"}
        param_dict["bind"]["options"] = field_values
        field_labels = field_values.copy()
        field_labels[none_index] = "[All]"
        param_dict["bind"]["labels"] = field_labels
        param_dict["bind"]["name"] = column_name
        print(f"Yay! We can filter by {column_name} now!")
    return vega_chart


def _create_chat(model: str, history: list):
    vega_lite_spec = (Path(__file__).parent /
                      "vega_lite4_schema.json").read_text()
    return get_genai_client().chats.create(
        model=model,
        config=GenerateContentConfig(
            system_instruction=f"""
You are an experienced Business Intelligence engineer,
proficient in building business charts and dashboards using Vega Lite.
You have good imagination, strong UX design skills, and you decent data engineering background.

You always write Vega Lite 4 code according to its JSON schema:

```json
{vega_lite_spec}
```
            """.strip(),
            temperature=0.1,
            top_p=0.0,
            top_k=1,
            seed=1,
            response_schema=VegaResult,
            response_mime_type="application/json",
            safety_settings=[
            SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                threshold="BLOCK_ONLY_HIGH", # type: ignore
            ),
        ]),
        history=history
    )


async def bi_engineer_tool(original_business_question: str,
                     question_that_sql_result_can_answer: str,
                     sql_code: str,
                     notes: str,
                     tool_context: ToolContext) -> str:
    """Senior BI Engineer. Executes SQL code.

    Args:
        original_business_question (str): Original business question.
        question_that_sql_result_can_answer (str):
            Specific question or sub-question that SQL result can answer.
        sql_code (str): BigQuery SQL code execute.
        notes (str): Important notes about the chart. Not empty only if the user stated something directly related to the chart.

    Returns:
        str: Chart image id and the result of executing the SQL code
             in CSV format (first 50 rows).
    """
    _init_environment()
    client = Client(project=_bq_project_id, location=_location)
    try:
        dataset_location = client.get_dataset(
                                f"{_data_project_id}.{_dataset}").location
        job_config = QueryJobConfig(use_query_cache=False)
        df: pd.DataFrame = client.query(sql_code,
                     job_config=job_config,
                     location=dataset_location).result().to_dataframe()
    except (BadRequest, NotFound) as ex:
        err_text = ex.args[0].strip()
        return f"BIGQUERY ERROR: {err_text}"

    if notes:
        notes_text = f"\n\n**Important notes about the chart:** \n{notes}\n\n"
    else:
        notes_text = ""

    chart_prompt = bi_engineer_prompt.format(
        original_business_question=original_business_question,
        question_that_sql_result_can_answer=question_that_sql_result_can_answer,
        sql_code=sql_code,
        notes_text=notes_text,
        columns_string=df.dtypes.to_string(),
        dataframe_preview_len=min(10,len(df)),
        dataframe_len=len(df),
        dataframe_head=df.head(10).to_string(),
    )

    vega_fix_chat = None
    while True:
        vega_chat = _create_chat(BI_ENGINEER_AGENT_MODEL_ID, [])
        chart_results = vega_chat.send_message(chart_prompt)
        chart_model = chart_results.parsed # type: ignore
        if chart_model:
            break
    chart_json = chart_model.vega_lite_4_json # type: ignore

    for _ in range(5): # 5 tries to make a good chart
        for _ in range(10):
            try:
                vega_dict = json.loads(chart_json) # type: ignore
                vega_dict["data"] = {"values": []}
                vega_dict.pop("datasets", None)
                vega_chart = alt.Chart.from_dict(vega_dict)
                with io.BytesIO() as tmp:
                    vega_chart.save(tmp, "png")
                vega_dict = _enhance_parameters(vega_dict, df)
                vega_chart_json = json.dumps(vega_dict, indent=1)
                vega_chart = alt.Chart.from_dict(vega_dict)
                vega_chart.data = df
                with io.BytesIO() as file:
                    vega_chart.save(file, "png")
                error_reason = ""
                break
            except Exception as ex:
                message = f"ERROR {type(ex).__name__}: " + ex.message if ex is jsonschema.ValidationError else str(ex)
                error_reason = message
                print(message)
                if not vega_fix_chat:
                    vega_fix_chat = _create_chat(BI_ENGINEER_FIX_AGENT_MODEL_ID,
                                                 vega_chat.get_history())
                print("Fixing...")
                chart_json = vega_fix_chat.send_message(
                    message
                ).parsed.vega_lite_4_json # type: ignore

        if not error_reason:
            with io.BytesIO() as file:
                vega_chart.data = df
                vega_chart.save(file, "png")
                file.seek(0)
                png_data = file.getvalue()
                evaluate_chart_result = evaluate_chart(
                                            png_data,
                                            vega_chart_json,
                                            question_that_sql_result_can_answer,
                                            len(df),
                                            tool_context)
            if not evaluate_chart_result or evaluate_chart_result.is_good:
                break
            error_reason = evaluate_chart_result.reason

        if not error_reason:
            break

        print(f"Feedback:\n{error_reason}.\n\nWorking on another version...")
        history = (vega_fix_chat.get_history()
                   if vega_fix_chat
                   else vega_chat.get_history())
        vega_chat = _create_chat(BI_ENGINEER_AGENT_MODEL_ID, history)
        chart_json = vega_chat.send_message(f"""
            Fix the chart based on the feedback.
            Only output Vega 4 Lite json.

            ***Feedback on the chart below**
            {error_reason}


            ***CHART**

            ``json
            {vega_chart_json}
            ````
            """).parsed.vega_lite_4_json # type: ignore

    print(f"Done working on a chart.")
    if error_reason:
        print(f"Chart is still not good: {error_reason}")
    else:
        print("And the chart seem good to me.")
    data_file_name = f"{tool_context.invocation_id}.parquet"
    parquet_bytes = df.to_parquet()
    await tool_context.save_artifact(filename=data_file_name,
                               artifact=Part.from_bytes(
                                   data=parquet_bytes,
                                   mime_type="application/parquet"))
    file_name = f"{tool_context.invocation_id}.vg"
    await tool_context.save_artifact(filename=file_name,
                               artifact=Part.from_bytes(
                                    mime_type="application/json",
                                    data=vega_chart_json.encode("utf-8")))
    with io.BytesIO() as file:
        vega_chart.save(file, "png", ppi=72)
        file.seek(0)
        data = file.getvalue()
        new_image_name = f"{tool_context.invocation_id}.png"
        await tool_context.save_artifact(filename=new_image_name,
                                   artifact=Part.from_bytes(
                                        mime_type="image/png",
                                        data=data))
        tool_context.state["chart_image_name"] = new_image_name

    csv = df.head(MAX_RESULT_ROWS_DISPLAY).to_csv(index=False)
    if len(df) > MAX_RESULT_ROWS_DISPLAY:
        csv_message = f"**FIRST {MAX_RESULT_ROWS_DISPLAY} OF {len(df)} ROWS OF DATA**:"
    else:
        csv_message = "**DATA**:"

    return f"chart_image_id: `{new_image_name}`\n\n{csv_message}\n\n```csv\n{csv}\n```\n"
