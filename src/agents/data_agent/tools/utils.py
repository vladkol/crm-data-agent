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

from threading import Lock

from google import genai
from google.adk.models import Gemini


_lock = Lock()

_gemini = None
_llm_client = None


def get_genai_client() -> genai.Client:
    global _gemini
    global _llm_client
    if _llm_client:
        return _llm_client
    with _lock:
        if _llm_client:
            return _llm_client
        _gemini = Gemini()
        _llm_client = _gemini.api_client
        if hasattr(_llm_client, "_api_client"):
            _llm_client._api_client.location = "global"
    return _llm_client


def get_shared_lock() -> Lock:
    return _lock