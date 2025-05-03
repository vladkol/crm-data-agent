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
"""Chart Evaluator Sub-tool"""

from google.adk.tools import ToolContext
from google.genai.types import Content, GenerateContentConfig, Part, SafetySetting

from pydantic import BaseModel

from .utils import get_genai_client
from prompts.chart_evaluator import prompt as chart_evaluator_prompt


CHART_EVALUATOR_MODEL_ID =  "gemini-2.0-flash-001"

class EvaluationResult(BaseModel):
    is_good: bool
    reason: str


def evaluate_chart(png_image: bytes,
                   chart_json_text: str,
                   question: str,
                   data_row_count: int,
                   tool_context: ToolContext) -> EvaluationResult:
    """
    This is an experienced Business Intelligence UX designer.
    They look at a chart or a dashboard, and can tell if it the right one for the question.

    Parameters:
    * png_image (str) - png image of the chart or a dashboard
    * question (str) - question this chart is supposed to answer

    """

    prompt = chart_evaluator_prompt.format(data_row_count=data_row_count,
                                           chart_json=chart_json_text,
                                            question=question)

    image_part = Part.from_bytes(mime_type="image/png", data=png_image)
    eval_result = get_genai_client().models.generate_content(model=CHART_EVALUATOR_MODEL_ID,
        contents=Content(
            role="user",
            parts=[
                image_part, # type: ignore
                Part.from_text(text=prompt)
            ]
        ),
        config=GenerateContentConfig(
            response_schema=EvaluationResult,
            response_mime_type="application/json",
            system_instruction=f"""
You are an experienced Business Intelligence UX designer.
You can look at a chart or a dashboard, and tell if it the right one for the question.
""".strip(),
            temperature=0.0,
            top_p=0.000001,
            seed=0,
            safety_settings=[
            SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                threshold="BLOCK_ONLY_HIGH", # type: ignore
            ),
        ])
    )

    return eval_result.parsed # type: ignore
