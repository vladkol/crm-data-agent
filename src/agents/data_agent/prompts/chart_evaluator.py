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
**Instructions**:

The image is a BI chart or a dashboard that shows data supporting an answer to a question below.

Number of rows in the data source is: {data_row_count}.
Make sure labels and values are readable.

After looking at a chart, decide if it's good or not good (nothing in between).
If not good, provide a reason with a longer explanation of what needs to be worked on.

The chart must be comfortable to read on a 2K screen of 16 inch size with ability to zoom in and out.
Do not make comments about choice of dimensions, metrics, grouping or data cardinality.
You can only criticize readability, composition, color choices, font size, etc.

**Exceptions:**
* The chart may require interaction (selection parameters). Default selection may make rendered chart lack data. Be tolerant to that.
* The chart may be hard to read due to the density of elements. If the density problem can be solved by selecting a parameter value, then then assume it is ok, and let it slide.

**QUESTION:**
```
{question}
```

This is chart json code in Vega-Lite (data removed):

```json
{chart_json}
```

"""
