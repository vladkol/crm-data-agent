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
"""Agent Runner App - FastAPI websocket service"""

import asyncio
import importlib.util
import logging
import os
from pathlib import Path
from queue import Queue
import sys

from google.genai.types import Content

from google.adk import Agent, Runner
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions import VertexAiSessionService

from fastapi import FastAPI, WebSocket

from agent_artifact_service import GcsPartArtifactService

sys.path.append(str(Path(__file__).parent.parent))
from agents.config_env import prepare_environment

_root_agent = None # Root Agent
_runner = None # Runner
_session_queues = {} # Session queues
api_app = FastAPI()

#################### Initialization ####################
logging.getLogger().setLevel(logging.INFO)
os.environ["AGENT_DIRECTORY"] = str(Path(__file__).parent.parent /
                                        "agents" /
                                        "data_agent")
prepare_environment()
########################################################

def _get_root_agent() -> Agent:
    global _root_agent
    if _root_agent:
        return _root_agent
    root_files = [
        "__init__.py",
        "__main__.py",
        "agent.py",
        "main.py"
    ]
    module_name = "adk_root_agent_module"
    agent_dir = os.environ.get("AGENT_DIRECTORY", os.path.abspath("."))
    for file_name in root_files:
        spec = importlib.util.spec_from_file_location(module_name,
                            f"{agent_dir}/{file_name}")
        if spec:
            break
    module = importlib.util.module_from_spec(spec) # type: ignore
    sys.modules[module_name] = module
    spec.loader.exec_module(module) # type: ignore
    _root_agent = getattr(module, "root_agent")
    return _root_agent # type: ignore


def _make_runner():
    global _runner
    if not _runner:
        agent_engine_id = os.environ["AGENT_ENGINE_ID"]
        vertex_ai_bucket = os.environ["AI_STORAGE_BUCKET"]

        session_service = VertexAiSessionService(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ["GOOGLE_CLOUD_LOCATION"],
        )
        artifact_service = GcsPartArtifactService(
            bucket_name=vertex_ai_bucket
        )
        memory_service = InMemoryMemoryService()
        _runner = Runner(app_name=agent_engine_id,
                        agent=_get_root_agent(),
                        artifact_service=artifact_service,
                        session_service=session_service,
                        memory_service=memory_service)


async def _process_requests(websocket: WebSocket,
                            user_id: str,
                            session_id: str,
                            event_queue: Queue,
                            ):
    """Client to agent communication"""
    global _runner
    if not _runner:
        return
    while True:
        try:
            text = await websocket.receive_text()
        except:
            logging.warning("Client disconnected.")
            break
        if not text:
            continue
        content = Content.model_validate_json(text)
        asyncio.create_task(
            _agent_requests_runner(content, user_id, session_id, event_queue)
        )


async def _agent_requests_runner(content: Content,
                                 user_id: str,
                                 session_id: str,
                                 event_queue: Queue):
    global _runner
    if not _runner:
        return
    events = _runner.run_async(user_id=user_id,
                          session_id=session_id,
                          new_message=content)
    async for event in events:
        event_queue.put(event)
    event_queue.put("TURN_COMPLETE")
    logging.info("Turn completed.")


async def _event_processor(websocket: WebSocket,
                           event_queue: Queue):
    while True:
        if event_queue.empty():
            await asyncio.sleep(0.5)
            continue
        event = event_queue.queue[0] # peeking
        try:
            if isinstance(event, str):
                await websocket.send_text(event)
            else:
                await websocket.send_text(event.model_dump_json())
            event_queue.get() # removing
        except:
            break
    logging.info("Event processing task completed.")


@api_app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(websocket: WebSocket,
                             user_id: str,
                             session_id: str):
    """Client websocket endpoint"""
    await websocket.accept()
    logging.info(f"User {user_id} with session #{session_id} connected.")
    _make_runner()
    session_ext_id = f"{user_id}/{session_id}"
    if session_ext_id in _session_queues:
        queue = _session_queues[session_ext_id]
    else:
        queue = Queue()
        _session_queues[session_ext_id] = queue

    event_processor_task = asyncio.create_task(
        _event_processor(websocket, queue)
    )
    client_to_agent_task = asyncio.create_task(
        _process_requests(websocket,
                          user_id,
                          session_id,
                          queue)
    )
    await asyncio.gather(client_to_agent_task, event_processor_task)
    print(f"Session {session_id} disconnected.")
