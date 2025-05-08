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

import asyncio
from contextlib import asynccontextmanager
import importlib
import inspect
import logging
import os
from pathlib import Path
import sys
import traceback
import typing
from typing import Any
from typing import List
from typing import Literal
from typing import Optional

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocket
from fastapi.websockets import WebSocketDisconnect
from google.genai import types
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import export
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel
from pydantic import ValidationError
from starlette.types import Lifespan

from google.adk.agents import RunConfig
from google.adk.agents.live_request_queue import LiveRequest
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.llm_agent import Agent
from google.adk.agents.run_config import StreamingMode
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.events.event import Event
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService


logger = logging.getLogger(__name__)

class ApiServerSpanExporter(export.SpanExporter):

    def __init__(self, trace_dict):
      self.trace_dict = trace_dict

    def export(
        self, spans: typing.Sequence[ReadableSpan]
    ) -> export.SpanExportResult:
        for span in spans:
            if (
                span.name == "call_llm"
                or span.name == "send_data"
                or span.name.startswith("tool_response")
            ):
                attributes = dict(span.attributes) # type: ignore
                attributes["trace_id"] = span.get_span_context().trace_id # type: ignore
                attributes["span_id"] = span.get_span_context().span_id # type: ignore
                if attributes.get("gcp.vertex.agent.event_id", None): # type: ignore
                  self.trace_dict[attributes["gcp.vertex.agent.event_id"]] = attributes # type: ignore
        return export.SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
      return True


class AgentRunRequest(BaseModel):
    app_name: str
    user_id: str
    session_id: str
    new_message: types.Content
    streaming: bool = False


def get_fast_api_app(
    *,
    agent_dir: str,
    session_db_url: str = "",
    allow_origins: Optional[list[str]] = None,
    trace_to_cloud: bool = False,
    lifespan: Optional[Lifespan[FastAPI]] = None,
    artifact_service: Optional[BaseArtifactService] = None
) -> FastAPI:
  # InMemory tracing dict.
  trace_dict: dict[str, Any] = {}

  # Set up tracing in the FastAPI server.
  provider = TracerProvider()
  provider.add_span_processor(
      export.SimpleSpanProcessor(ApiServerSpanExporter(trace_dict))
  )
  if trace_to_cloud:
    if project_id := os.environ.get("GOOGLE_CLOUD_PROJECT", None):
      processor = export.BatchSpanProcessor(
          CloudTraceSpanExporter(project_id=project_id)
      )
      provider.add_span_processor(processor)
    else:
      logging.warning(
          "GOOGLE_CLOUD_PROJECT environment variable is not set. Tracing will"
          " not be enabled."
      )

  trace.set_tracer_provider(provider)

  exit_stacks = []

  @asynccontextmanager
  async def internal_lifespan(app: FastAPI):
    if lifespan:
      async with lifespan(app) as lifespan_context:
        yield

        if exit_stacks:
          for stack in exit_stacks:
            await stack.aclose()
    else:
      yield

  # Run the FastAPI server.
  app = FastAPI(lifespan=internal_lifespan)

  if allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

  if agent_dir not in sys.path:
    sys.path.append(agent_dir)

  runner_dict = {}
  root_agent_dict = {}

  # Build the Artifact service
  artifact_service = artifact_service or InMemoryArtifactService()
  memory_service = InMemoryMemoryService()

  # Build the Session service
  agent_engine_id = ""
  if session_db_url:
      if session_db_url.startswith("agentengine://"):
        # Create vertex session service
        agent_engine_id = session_db_url.split("://")[1]
        if not agent_engine_id:
          raise ValueError("Agent engine id can not be empty.")
        session_service = VertexAiSessionService(
            os.environ["GOOGLE_CLOUD_PROJECT"],
            os.environ["GOOGLE_CLOUD_LOCATION"],
        )
      else:
          session_service = DatabaseSessionService(db_url=session_db_url)
  else:
      session_service = InMemorySessionService()


  @app.get("/debug/trace/{event_id}")
  def get_trace_dict(event_id: str) -> Any:
    event_dict = trace_dict.get(event_id, None)
    if event_dict is None:
      raise HTTPException(status_code=404, detail="Trace not found")
    return event_dict

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}",
      response_model_exclude_none=True,
  )
  def get_session(app_name: str, user_id: str, session_id: str) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    session = session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")
    return session

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions",
      response_model_exclude_none=True,
  )
  def list_sessions(app_name: str, user_id: str) -> list[Session]:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    return [
        session
        for session in session_service.list_sessions(
            app_name=app_name, user_id=user_id
        ).sessions
    ]

  @app.post(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}",
      response_model_exclude_none=True,
  )
  def create_session_with_id(
      app_name: str,
      user_id: str,
      session_id: str,
      state: Optional[dict[str, Any]] = None,
  ) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    if (
        session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        is not None
    ):
      logger.warning("Session already exists: %s", session_id)
      raise HTTPException(
          status_code=400, detail=f"Session already exists: {session_id}"
      )

    logger.info("New session created: %s", session_id)
    return session_service.create_session(
        app_name=app_name, user_id=user_id, state=state, session_id=session_id
    )

  @app.post(
      "/apps/{app_name}/users/{user_id}/sessions",
      response_model_exclude_none=True,
  )
  def create_session(
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]] = None,
  ) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name

    logger.info("New session created")
    return session_service.create_session(
        app_name=app_name, user_id=user_id, state=state
    )

  @app.delete("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
  def delete_session(app_name: str, user_id: str, session_id: str):
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    session_service.delete_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}",
      response_model_exclude_none=True,
  )
  def load_artifact(
      app_name: str,
      user_id: str,
      session_id: str,
      artifact_name: str,
      version: Optional[int] = Query(None),
  ) -> Optional[types.Part]:
    app_name = agent_engine_id if agent_engine_id else app_name
    artifact = artifact_service.load_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
        version=version,
    )
    if not artifact:
      raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}/versions/{version_id}",
      response_model_exclude_none=True,
  )
  def load_artifact_version(
      app_name: str,
      user_id: str,
      session_id: str,
      artifact_name: str,
      version_id: int,
  ) -> Optional[types.Part]:
    app_name = agent_engine_id if agent_engine_id else app_name
    artifact = artifact_service.load_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
        version=version_id,
    )
    if not artifact:
      raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts",
      response_model_exclude_none=True,
  )
  def list_artifact_names(
      app_name: str, user_id: str, session_id: str
  ) -> list[str]:
    app_name = agent_engine_id if agent_engine_id else app_name
    return artifact_service.list_artifact_keys(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}/versions",
      response_model_exclude_none=True,
  )
  def list_artifact_versions(
      app_name: str, user_id: str, session_id: str, artifact_name: str
  ) -> list[int]:
    app_name = agent_engine_id if agent_engine_id else app_name
    return artifact_service.list_versions(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
    )

  @app.delete(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}",
  )
  def delete_artifact(
      app_name: str, user_id: str, session_id: str, artifact_name: str
  ):
    app_name = agent_engine_id if agent_engine_id else app_name
    artifact_service.delete_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
    )

  @app.post("/run", response_model_exclude_none=True)
  async def agent_run(req: AgentRunRequest) -> list[Event]:
    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else req.app_name
    session = session_service.get_session(
        app_name=app_id, user_id=req.user_id, session_id=req.session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")
    runner = await _get_runner_async(req.app_name)
    events = [
        event
        async for event in runner.run_async(
            user_id=req.user_id,
            session_id=req.session_id,
            new_message=req.new_message,
        )
    ]
    logger.info("Generated %s events in agent run: %s", len(events), events)
    return events

  @app.post("/run_sse")
  async def agent_run_sse(req: AgentRunRequest) -> StreamingResponse:
    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else req.app_name
    # SSE endpoint
    session = session_service.get_session(
        app_name=app_id, user_id=req.user_id, session_id=req.session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")

    # Convert the events to properly formatted SSE
    async def event_generator():
      try:
        stream_mode = StreamingMode.SSE if req.streaming else StreamingMode.NONE
        runner = await _get_runner_async(req.app_name)
        async for event in runner.run_async(
            user_id=req.user_id,
            session_id=req.session_id,
            new_message=req.new_message,
            run_config=RunConfig(streaming_mode=stream_mode),
        ):
          # Format as SSE data
          sse_event = event.model_dump_json(exclude_none=True, by_alias=True)
          logger.info("Generated event in agent run streaming: %s", sse_event)
          yield f"data: {sse_event}\n\n"
      except Exception as e:
        logger.exception("Error in event_generator: %s", e)
        # You might want to yield an error event here
        yield f'data: {{"error": "{str(e)}"}}\n\n'

    # Returns a streaming response with the proper media type for SSE
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


  @app.websocket("/run_live")
  async def agent_live_run(
      websocket: WebSocket,
      app_name: str,
      user_id: str,
      session_id: str,
      modalities: List[Literal["TEXT", "AUDIO"]] = Query(
          default=["TEXT", "AUDIO"]
      ),  # Only allows "TEXT" or "AUDIO"
  ) -> None:
    await websocket.accept()

    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else app_name
    session = session_service.get_session(
        app_name=app_id, user_id=user_id, session_id=session_id
    )
    if not session:
      # Accept first so that the client is aware of connection establishment,
      # then close with a specific code.
      await websocket.close(code=1002, reason="Session not found")
      return

    live_request_queue = LiveRequestQueue()

    async def forward_events():
      runner = await _get_runner_async(app_name)
      async for event in runner.run_live(
          session=session, live_request_queue=live_request_queue
      ):
        await websocket.send_text(
            event.model_dump_json(exclude_none=True, by_alias=True)
        )

    async def process_messages():
      try:
        while True:
          data = await websocket.receive_text()
          # Validate and send the received message to the live queue.
          live_request_queue.send(LiveRequest.model_validate_json(data))
      except ValidationError as ve:
        logger.error("Validation error in process_messages: %s", ve)

    # Run both tasks concurrently and cancel all if one fails.
    tasks = [
        asyncio.create_task(forward_events()),
        asyncio.create_task(process_messages()),
    ]
    done, pending = await asyncio.wait(
        tasks, return_when=asyncio.FIRST_EXCEPTION
    )
    try:
      # This will re-raise any exception from the completed tasks.
      for task in done:
        task.result()
    except WebSocketDisconnect:
      logger.info("Client disconnected during process_messages.")
    except Exception as e:
      logger.exception("Error during live websocket communication: %s", e)
      traceback.print_exc()
      WEBSOCKET_INTERNAL_ERROR_CODE = 1011
      WEBSOCKET_MAX_BYTES_FOR_REASON = 123
      await websocket.close(
          code=WEBSOCKET_INTERNAL_ERROR_CODE,
          reason=str(e)[:WEBSOCKET_MAX_BYTES_FOR_REASON],
      )
    finally:
      for task in pending:
        task.cancel()

  async def _get_root_agent_async(app_name: str) -> Agent:
    """Returns the root agent for the given app."""
    if app_name in root_agent_dict:
      return root_agent_dict[app_name]
    agent_module = importlib.import_module(app_name)
    if getattr(agent_module.agent, "root_agent"):
      root_agent = agent_module.agent.root_agent
    else:
      raise ValueError(f'Unable to find "root_agent" from {app_name}.')

    # Handle an awaitable root agent and await for the actual agent.
    if inspect.isawaitable(root_agent):
      try:
        agent, exit_stack = await root_agent
        exit_stacks.append(exit_stack)
        root_agent = agent
      except Exception as e:
        raise RuntimeError(f"error getting root agent, {e}") from e

    root_agent_dict[app_name] = root_agent
    return root_agent

  async def _get_runner_async(app_name: str) -> Runner:
    """Returns the runner for the given app."""
    if app_name in runner_dict:
      return runner_dict[app_name]
    root_agent = await _get_root_agent_async(app_name)
    runner = Runner(
        app_name=agent_engine_id if agent_engine_id else app_name,
        agent=root_agent,
        artifact_service=artifact_service,
        session_service=session_service,
        memory_service=memory_service,
    )
    runner_dict[app_name] = runner
    return runner

  return app
