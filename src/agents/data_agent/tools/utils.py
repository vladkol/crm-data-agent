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
"""Utils for agents"""

from functools import cached_property
import os
from typing_extensions import override
from threading import Lock
from typing import AsyncGenerator

from google import genai
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

_lock = Lock()

_gemini = None
_llm_client = None

class _GlobalGemini(Gemini):
    @override
    async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if not llm_request.model:
            llm_request.model = "gemini-flash-2.0"
        if (
            llm_request.model.startswith("gemini-2")
            and "/" not in llm_request.model
        ):
            project = os.environ["GOOGLE_CLOUD_PROJECT"]
            llm_request.model = (f"projects/{project}/locations/global/"
                                 "publishers/google/"
                                 f"models/{llm_request.model}")
        async for response in super().generate_content_async(
            llm_request, stream
        ):
            yield response

    @cached_property
    def api_client(self) -> genai.Client:
        """Provides the api client.

        Returns:
        The api client.
        """
        original_client = super().api_client
        return genai.Client(
            vertexai=original_client.vertexai,
            location="global",
        )


def get_genai_client(model_id: str = "gemini-flash-2.0") -> genai.Client:
    global _gemini
    global _llm_client
    if _llm_client:
        return _llm_client
    with _lock:
        if _llm_client:
            return _llm_client
        _gemini = _GlobalGemini(model=model_id)
        _gemini.api_client._api_client.location = "global"
        _llm_client = _gemini.api_client
    return _llm_client

def get_gemini_model(model_id: str) -> Gemini:
    global _gemini
    get_genai_client()
    res = _gemini.model_copy() # type: ignore
    res.model = model_id
    return res

def get_shared_lock() -> Lock:
    return _lock