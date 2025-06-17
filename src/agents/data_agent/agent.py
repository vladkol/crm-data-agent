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
"""Root agent"""

from pathlib import Path
import sys
from typing import Optional

from google.genai.types import (
                                Content,
                                GenerateContentConfig,
                                SafetySetting,
                                ThinkingConfig
                                )
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse, LlmRequest
from google.adk.planners import BuiltInPlanner
from google.adk.tools.agent_tool import AgentTool

sys.path.append(str(Path(__file__).parent.parent.parent))
from shared.config_env import prepare_environment

from prompts.root_agent import system_instruction as root_agent_instruction
from tools.bi_engineer import bi_engineer_tool
from tools.crm_business_analyst import crm_business_analyst_agent
from tools.data_engineer import data_engineer
from tools.utils import get_gemini_model


ROOT_AGENT_MODEL_ID = "gemini-2.5-pro" # "gemini-2.5-pro-preview-05-06"


async def before_model_callback(callback_context: CallbackContext,
                          llm_request: LlmRequest) -> LlmResponse | None:
    chart_image_name = callback_context.state.get("chart_image_name", None)
    if chart_image_name:
        callback_context.state["chart_image_name"] = ""
        llm_request.contents[0].parts.append( # type: ignore
            await callback_context.load_artifact(
                filename=chart_image_name)) # type: ignore
    return None


async def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    pass


async def after_model_callback(callback_context: CallbackContext,
                          llm_response: LlmResponse) -> LlmResponse | None:
    pass


########################### AGENT ###########################
prepare_environment()

root_agent = LlmAgent(
    model=get_gemini_model(ROOT_AGENT_MODEL_ID),
    name="data_agent",
    output_key="output",
    description="CRM Data Analytics Consultant",
    instruction=root_agent_instruction,
    before_model_callback=before_model_callback,
    after_model_callback=after_model_callback,
    before_agent_callback=before_agent_callback,
    tools=[
        AgentTool(crm_business_analyst_agent),
        data_engineer,
        bi_engineer_tool,
    ],
    planner=BuiltInPlanner(
        thinking_config=ThinkingConfig(thinking_budget=32768)
    ),
    generate_content_config=GenerateContentConfig(
        temperature = 0.0001,
        top_p = 0.0,
        seed=256,
        safety_settings=[
            SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT", # type: ignore
                threshold="BLOCK_ONLY_HIGH", # type: ignore
            ),
        ],
    )
)
