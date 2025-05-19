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
"""Agent Runtime Client"""

from abc import ABC, abstractmethod
import json
import logging
from typing import AsyncGenerator, Union, Optional
from typing_extensions import override

import requests

from google.adk.events import Event
from google.adk.sessions import Session
from google.genai.types import Content, Part

from pydantic import ValidationError


MAX_RUN_RETRIES = 10

logger = logging.getLogger(__name__)

class AgentRuntime(ABC):
    def __init__(self, session: Session):
        self.session = session

    @abstractmethod
    async def stream_query(self, message: str) -> AsyncGenerator[Event, None]:
        pass

    @abstractmethod
    def is_streaming(self) -> bool:
        pass


async def sse_client(url, request, headers):
    """
    A very minimal SSE client using only the requests library.
    Yields the data content from SSE messages.
    Handles multi-line 'data:' fields for a single event.
    """
    if not headers:
        headers = {}
    headers["Accept"] = "text/event-stream"
    headers["Cache-Control"] = "no-cache"
    try:
        # stream=True is essential for SSE
        # timeout=None can be used for very long-lived connections,
        # but be aware of potential indefinite blocking if server misbehaves.
        # A specific timeout (e.g., (3.05, 60)) for connect and read can be safer.
        with requests.post(url, json=request, stream=True, headers=headers, timeout=(60, 60*60*24*7)) as response:
            response.raise_for_status()  # Raise an exception for HTTP error codes (4xx or 5xx)
            logger.info(f"Connected to SSE stream at {url}")

            current_event_data_lines = []
            for line_bytes in response.iter_lines(): # iter_lines gives bytes
                if not line_bytes: # An empty line signifies the end of an event
                    if current_event_data_lines:
                        # Join accumulated data lines for the event
                        full_data_string = "\n".join(current_event_data_lines)
                        yield full_data_string
                        current_event_data_lines = [] # Reset for the next event
                    continue # Skip further processing for this empty line

                # Decode bytes to string (SSE is typically UTF-8)
                line = line_bytes.decode('utf-8')

                if line.startswith(':'): # Comment line, ignore
                    continue

                # We are only interested in 'data:' lines for this minimal client
                if line.startswith('data:'):
                    # Strip "data:" prefix and any leading/trailing whitespace from the value part
                    data_value = line[len('data:'):].lstrip()
                    current_event_data_lines.append(data_value)

                # Other SSE fields like 'event:', 'id:', 'retry:' are ignored here.

            # If the stream ends and there's pending data (no final empty line)
            if current_event_data_lines:
                full_data_string = "\n".join(current_event_data_lines)
                yield full_data_string

    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting or streaming SSE: {e}")
    except KeyboardInterrupt:
        logging.warning("SSE stream manually interrupted.")
    finally:
        logging.info("SSE client finished.")

class FastAPIEngineRuntime(AgentRuntime):
    def __init__(self,
                 session: Session,
                 server_url: Optional[str] = None ):
        super().__init__(session)
        if not server_url:
            server_url = "http://127.0.0.1:8000"
        self.server_url = server_url
        self.streaming = False
        self.connection = None


    @override
    async def stream_query(
        self,
        message: Union[str, Content]
    ) -> AsyncGenerator[Event, None]:
        self.streaming = True
        try:
            if not message:
                content = None
            if message and isinstance(message, str):
                content = Content(
                    parts=[
                        Part.from_text(text=message)
                    ],
                    role="user"
                )
            else:
                content = message
            if content:
                content_dict = content.model_dump()
            else:
                content_dict = None
            request = {
                "app_name": self.session.app_name,
                "user_id": self.session.user_id,
                "session_id": self.session.id,
                "new_message": content_dict,
                "streaming": False
            }

            async for event_str in sse_client(f"{self.server_url}/run_sse",
                                          request=request,
                                          headers=None):
                try:
                    yield Event.model_validate_json(event_str)
                except ValidationError as e:
                    try:
                        # trying to parse as if it was a json with "error" field.
                        err_json = json.loads(event_str)
                        if "error" in err_json:
                            print(f"#### RUNTIME ERROR: {err_json['error']}")
                            continue
                    except json.JSONDecodeError:
                        print(f"VALIDATION ERROR: {e}")
                        print("### DATA ###:\n" + event_str)
                        print("\n\n######################################\n\n")
                        pass
        finally:
            self.streaming = False

    @override
    def is_streaming(self) -> bool:
        return self.streaming