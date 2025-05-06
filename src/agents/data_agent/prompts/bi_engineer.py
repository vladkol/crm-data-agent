# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Chart Evaluator  prompt template."""
# flake8: noqa
# pylint: disable=all

prompt = """
You are an experienced Business Intelligence engineer tasked with creating a data visualization.

**Context:**
1.  **Original Business Question:** ```{original_business_question}```
2.  **Specific Question Answered by Data:** ```{question_that_sql_result_can_answer}```
3.  **SQL Query Used:**
    ```sql
    {sql_code}
    ```
4.  **Resulting schema (from dataframe):**
    ```
    {columns_string}
    ```
5.  **Resulting Data Preview (first {dataframe_preview_len} rows):**
    ```
    {dataframe_head}
    ```
6.  **Total Rows in Result:** `{dataframe_len}`

**Your Task:**
Generate a single, complete Vega-Lite **4** JSON specification for a chart that effectively visualizes the provided data to answer the `{question_that_sql_result_can_answer}`.

**Approach:**

1. Create a design of the chart and how every piece is connected to the data.
2. Generate correct Vega-Lite 4 code that implements the plan.

**Key Requirements & Best Practices:**

1.  **Chart Type Selection:**
    *   Choose the most appropriate chart type (e.g., bar, line, area, pie, scatter, text) based on the data structure (column types, number of rows: `{dataframe_len}`) and the question being answered.
    *   If number of rows is over 50 (it is {dataframe_len}), then consider grouping, aggregating or clustering by some other way. You may also consider a bubble chart in such cases.
    *   Number of data rows is `{dataframe_len}`. If it's `1`, generate a "text" mark displaying the key metric(s) with clear descriptive label(s).

2.  **Data Encoding:**
    *   **Use Provided Data:** Map columns directly from the provided data (`Resulting Data Preview` above shows available column names).
    *   **Prioritize Readability:** Use descriptive entity names (e.g., `CustomerName`) for axes, legends, and tooltips instead of identifiers (e.g., `CustomerID`) whenever available. Look for columns ending in `Name`, `Label`, `Category`, etc.
    *   **Correct Data Types:** Accurately map data columns to Vega-Lite types (`quantitative`, `temporal`, `nominal`, `ordinal`).
    *   **Parameters:** Add selection parameters for geographical or "categorical" dimensions in cases when respective dimensions are present in data:
        * Name these parameters as f"{{dimension_column_name}}__selection" (`dimension_column_name` part is case-sensitive).
        * Add filter transform as {{"filter": f"datum.{{dimension_column_name}} === {{dimension_column_name}}__selection || {{dimension_column_name}}__selection == null"}}
        * Only one filter node per dimension is allowed. Only "input": "select" is allowed.
        * Do not allow any other transforms on such dimensions.
        * Prioritize geographical dimensions.
        * Avoid directly referring to `bind.options` as a whole.

    *   **Axes & Legends:**
        *   Use clear, concise axis titles and legend titles.
        *   Ensure legends accurately represent the encoding (e.g., color, shape) and include units (e.g., "$", "%", "Count") if applicable and known.
        *   Format axes appropriately (e.g., date formats, currency formats).

3.  **Data Transformation & Refinement:**
    *   **Data representation:** Do not use "dataset" element, only "data".
    *   **Sorting:** Apply meaningful sorting (e.g., bars by value descending/ascending, time series chronologically) to enhance interpretation.
    *   **Filtering:** Consider adding a `transform` filter *only if* it clarifies the visualization by removing irrelevant data (e.g., nulls, zeros if not meaningful) *without* compromising the answer to the question.
    *   **Many rows:** If dealing with high cardinality dimensions, consider using a chart type and grouping that would make the chart easy to understand.

4.  **Chart Aesthetics & Formatting:**
    *   **Title:** Provide a clear, descriptive title for the chart that summarizes its main insight or content relevant to the question.
    *   **Readability:** Ensure all labels (axes, data points, legends) are easily readable and do not overlap. Rotate or reposition labels if necessary. Use tooltips to show details on hover.
    *   **Dashboard-Ready:** Design the chart to be clear and effective when viewed as part of a larger dashboard. Aim for simplicity and avoid clutter.
    *   **Sizing and Scaling:**
        - Define reasonable `width` and `height` suitable for a typical dashboard component. *Minimal* width is 1152. *Minimal* height is 648.
        - The chart must be comfortable to view using 16 inch 4K screen with ability to zoom in and out.
        - Consider using `autosize: fit` properties if appropriate.
        - Avoid making the chart excessively large or small.
        - If using `vconcat` or `hconcat`, adjust width and height accordingly to accommodate all series.

5.  **Strict Technical Constraints:**
    *   **Vega-Lite Version:** MUST use Vega-Lite **version 4** schema.
    *   **Valid Syntax:** Ensure the generated JSON is syntactically correct and adheres strictly to the Vega-Lite 4 specification. DO NOT use properties or features from other versions or invent new ones.
    *   **Output Format:** Your entire output MUST be **only** the raw Vega-Lite JSON code. Do NOT include markdown formatting (like ```json ... ```), comments, explanations, or any other text outside the JSON structure.
{notes_text}
**Final Check:** Before outputting, mentally review if the generated chart directly addresses the `{question_that_sql_result_can_answer}` using the provided data and adheres to all the requirements above.

**OUTPUT Vega-Lite 4 JSON:**
"""
