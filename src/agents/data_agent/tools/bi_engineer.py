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

from datetime import date, datetime
from functools import cache
import io
import json
import os

from pydantic import BaseModel

from google.adk.tools import ToolContext
from google.genai.types import (
    GenerateContentConfig,
    Part,
    SafetySetting,
    ThinkingConfig
)
from google.cloud.bigquery import Client, QueryJobConfig
from google.cloud.exceptions import BadRequest, NotFound

import altair as alt
from altair.vegalite.schema import core as alt_core
import pandas as pd

from .utils import get_genai_client
from prompts.bi_engineer import prompt as bi_engineer_prompt
from tools.chart_evaluator import evaluate_chart


MAX_RESULT_ROWS_DISPLAY = 50
BI_ENGINEER_AGENT_MODEL_ID = "gemini-2.5-pro-preview-06-05" # "gemini-2.5-pro-preview-05-06"
BI_ENGINEER_FIX_AGENT_MODEL_ID = "gemini-2.5-pro-preview-06-05" # "gemini-2.5-pro-preview-05-06"


@cache
def _init_environment():
    global _bq_project_id, _data_project_id, _location, _dataset
    _bq_project_id = os.environ["BQ_PROJECT_ID"]
    _data_project_id = os.environ["SFDC_DATA_PROJECT_ID"]
    _location = os.environ["BQ_LOCATION"]
    _dataset = os.environ["SFDC_BQ_DATASET"]

class VegaResult(BaseModel):
    vega_lite_json: str


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


def _create_chat(model: str, history: list, max_thinking: bool = False):
    return get_genai_client().chats.create(
        model=model,
        config=GenerateContentConfig(
            temperature=0.1,
            top_p=0.0,
            seed=256,
            response_schema=VegaResult,
            response_mime_type="application/json",
            safety_settings=[
                SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                    threshold="BLOCK_ONLY_HIGH", # type: ignore
                ),
            ],
            thinking_config=(
                ThinkingConfig(thinking_budget=32768) if max_thinking
                else None),
        ),
        history=history
    )



def _fix_df_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Inspects a DataFrame and converts any 'object' dtype columns
    that contain date or datetime objects to datetime64[ns].

    Args:
        df (pd.DataFrame): The DataFrame to fix.

    Returns:
        pd.DataFrame: A new DataFrame with corrected date types.
    """
    df_fixed = df.copy()  # Work on a copy to avoid side effects
    for col in df_fixed.columns:
        # Check only for 'object' type columns to be efficient
        if df_fixed[col].dtype == 'object':
            try:
                # Attempt to get the first non-null value
                first_non_null = df_fixed[col].dropna().iloc[0]

                # Check if it's a date or datetime object
                if isinstance(first_non_null, (date, datetime)):
                    print(f"Converting column '{col}' to datetime64[ns]...")
                    df_fixed[col] = pd.to_datetime(df_fixed[col])
            except IndexError:
                # This happens if the column is all NaNs
                continue
            except Exception:
                # The column might be 'object' but not contain dates
                continue

    return df_fixed


async def bi_engineer_tool(original_business_question: str,
                     question_that_sql_result_can_answer: str,
                     sql_file_name: str,
                     notes: str,
                     tool_context: ToolContext) -> str:
    """Senior BI Engineer. Executes SQL code.

    Args:
        original_business_question (str): Original business question.
        question_that_sql_result_can_answer (str):
            Specific question or sub-question that SQL result can answer.
        sql_file_name (str): File name of BigQuery SQL code execute.
        notes (str): Important notes about the chart. Not empty only if the user stated something directly related to the chart.

    Returns:
        str: Chart image id and the result of executing the SQL code
             in CSV format (first 50 rows).
    """
    _init_environment()
    sql_code_part = await tool_context.load_artifact(sql_file_name)
    sql_code = sql_code_part.inline_data.data.decode("utf-8") # type: ignore
    client = Client(project=_bq_project_id, location=_location)
    try:
        dataset_location = client.get_dataset(
                                f"{_data_project_id}.{_dataset}").location
        job_config = QueryJobConfig(use_query_cache=False)
        df: pd.DataFrame = client.query(sql_code,
                     job_config=job_config,
                     location=dataset_location).result().to_dataframe()
        df = _fix_df_dates(df)
    except (BadRequest, NotFound) as ex:
        err_text = ex.args[0].strip()
        return f"BIGQUERY ERROR: {err_text}"

    if notes:
        notes_text = f"\n\n**Important notes about the chart:** \n{notes}\n\n"
    else:
        notes_text = ""

    vega_lite_spec = json.dumps(
        alt_core.load_schema(),
        indent=1,
        sort_keys=False
    )
    chart_prompt = bi_engineer_prompt.format(
        original_business_question=original_business_question,
        question_that_sql_result_can_answer=question_that_sql_result_can_answer,
        sql_code=sql_code,
        notes_text=notes_text,
        columns_string=df.dtypes.to_string(),
        dataframe_preview_len=min(10,len(df)),
        dataframe_len=len(df),
        dataframe_head=df.head(10).to_string(),
        vega_lite_spec=vega_lite_spec,
        vega_lite_schema_version=alt.SCHEMA_VERSION.split(".")[0]
    )

    vega_chart_json = ""
    vega_fix_chat = None
    while True:
        vega_chat = _create_chat(BI_ENGINEER_AGENT_MODEL_ID, [])
        chart_results = vega_chat.send_message(chart_prompt)
        chart_model = chart_results.parsed # type: ignore
        if chart_model:
            break
    chart_json = chart_model.vega_lite_json # type: ignore

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
                message = f"""
You made a mistake!
Fix the issues. Redesign the chart if it promises a better result.

ERROR {type(ex).__name__}: {str(ex)}
""".strip()
                error_reason = message
                print(message)
                if not vega_fix_chat:
                    vega_fix_chat = _create_chat(BI_ENGINEER_FIX_AGENT_MODEL_ID,
                                                 vega_chat.get_history(),
                                                 True)
                print("Fixing...")
                chart_json = vega_fix_chat.send_message(
                    message
                ).parsed.vega_lite_json # type: ignore

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
            Only output Vega-Lite json.

            ***Feedback on the chart below**
            {error_reason}


            ***CHART**

            ``json
            {vega_chart_json}
            ````
            """).parsed.vega_lite_json # type: ignore

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
