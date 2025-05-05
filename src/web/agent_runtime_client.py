from abc import ABC, abstractmethod
import base64
from typing import AsyncGenerator, Optional
from typing_extensions import override

from google.api.httpbody_pb2 import HttpBody
from google.adk.events import Event
from google.adk.sessions import Session
from google.genai.types import Content, Part

import vertexai

import vertexai.agent_engines
from websockets import State
from websockets.asyncio.client import connect


MAX_RUN_RETRIES = 10

class AgentRuntime(ABC):
    def __init__(self, session: Session):
        self.session = session

    @abstractmethod
    async def stream_query(self, message: str) -> AsyncGenerator[Event, None]:
        pass

    @abstractmethod
    def is_streaming(self) -> bool:
        pass


class AgentEngineRuntime(AgentRuntime):
    def __init__(self, session: Session, agent_engine_id: str):
        super().__init__(session)
        self.agent_engine_id = agent_engine_id
        self.streaming = False

    @override
    async def stream_query(self, message: str) -> AsyncGenerator[Event, None]:
        self.streaming = True
        try:
            agent_engine = vertexai.agent_engines.get(self.agent_engine_id)
            for event in agent_engine.stream_query( # type: ignore
                user_id=self.session.user_id,
                session_id=self.session.id,
                message=message,
            ):
                if not event:
                    continue
                if isinstance(event, Event):
                    yield event
                elif isinstance(event, dict):
                    yield Event.model_validate(event)
                elif isinstance(event, str):
                    yield Event.model_validate_json(event)
                elif isinstance(event, HttpBody):
                    if not event.data:
                        continue
                    text = event.data.decode("utf-8")
                    raise RuntimeError(text)
                else:
                    raise Exception(f"Unknown event type: {type(event)}")
        finally:
            self.streaming = False

    @override
    def is_streaming(self) -> bool:
        return self.streaming


class FastAPIEngineRuntime(AgentRuntime):
    def __init__(self,
                 session: Session,
                 server_url: Optional[str] = None ):
        super().__init__(session)
        if not server_url:
            server_url = ("ws://127.0.0.1:8000/ws/"
                          f"{self.session.user_id}/{self.session.id}")
        self.server_url = server_url
        self.streaming = False
        self.connection = None


    @override
    async def stream_query(self, message: str) -> AsyncGenerator[Event, None]:
        self.streaming = True
        try:
            content = Content(parts=[
                                Part.from_text(text=message)
                            ],
                            role="user"
            )
            if not self.connection or self.connection.state != State.OPEN:
                self.connection = await connect(
                    self.server_url,
                    open_timeout=60, # Open timeout of 1 minute
                    ping_interval=5, # Ping interval of 5 seconds
                    ping_timeout=60*15 # Ping timeout of 15 minutes
                )
            turn_complete = False
            await self.connection.send(content.model_dump_json(), True)
            while self.connection.state == State.OPEN and not turn_complete:
                message = await self.connection.recv(True)
                if message == "TURN_COMPLETE":
                    turn_complete = True
                    break
                event = Event.model_validate_json(message)
                yield event
            # Closing the connection until the next request
            if self.connection.state == State.OPEN:
                await self.connection.close()
        finally:
            self.streaming = False

    @override
    def is_streaming(self) -> bool:
        return self.streaming