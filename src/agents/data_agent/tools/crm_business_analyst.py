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
"""Business Analyst Agent"""

from google.adk.agents import LlmAgent
from google.genai.types import GenerateContentConfig, SafetySetting

from prompts.crm_business_analyst import (system_instruction
                                          as crm_business_analyst_instruction)


BUSINESS_ANALYST_AGENT_MODEL_ID = "gemini-2.5-pro-preview-05-06"

crm_business_analyst_agent = LlmAgent(
    model=BUSINESS_ANALYST_AGENT_MODEL_ID,
    name="crm_business_analyst",
    description="""
        This is your Senior Business Analyst.

        They can analyze your questions about business
        no matter what form these questions are in.

        Questions may be different:
            - Directly linked to business data (e.g. "Revenue by country")
            - Open to interpretation (e.g. "Who are my best customers").

        They figure out what metrics, dimensions and KPIs
        could be used to answer the question.

        They may offer a few options.
        """,
    instruction=crm_business_analyst_instruction,
    generate_content_config=GenerateContentConfig(
        temperature=0.0,
        top_p=0.0,
        safety_settings=[
            SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                threshold="BLOCK_ONLY_HIGH", # type: ignore
            ),
        ])
)
