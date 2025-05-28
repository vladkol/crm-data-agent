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
"""Firestore Session Service implementation"""

import logging
from typing import Any
from typing import Optional
import uuid

from google.adk.events.event import Event
from google.adk.sessions.base_session_service import (BaseSessionService,
                                                      GetSessionConfig,
                                                      ListSessionsResponse,
                                                      Session,
                                                      State)
from google.api_core import exceptions
from google.cloud.firestore import (Client,
                                    CollectionReference,
                                    DocumentReference,
                                    Query,
                                    SERVER_TIMESTAMP)


logger = logging.getLogger(__name__)

class FirestoreSessionService(BaseSessionService):
    def __init__(self,
                 database: str,
                 sessions_collection: str = "/",
                 project_id: Optional[str] = None):

        self.client = Client(project_id, database=database)
        self.sessions_collection = sessions_collection

    @staticmethod
    def _clean_app_name(name: str) -> str:
        return name.rsplit("/", 1)[-1]


    def _get_session_path(self,
                          *,
                          app_name: str,
                          user_id: str,
                          session_id: str) -> str:
        return (f"{self.sessions_collection}"
                f"/agents/{FirestoreSessionService._clean_app_name(app_name)}"
                f"/users/{user_id}"
                f"/sessions/{session_id}").strip("/")

    def _get_session_doc(self,
                         *,
                         app_name: str,
                         user_id: str,
                         session_id: str) -> DocumentReference:
        sessions_collection = self._get_sessions_collection(
            app_name=FirestoreSessionService._clean_app_name(app_name),
            user_id=user_id
        )
        return sessions_collection.document(session_id)

    def _get_events_collection(self,
                         *,
                         app_name: str,
                         user_id: str,
                         session_id: str) -> CollectionReference:
        return self._get_session_doc(
            app_name=FirestoreSessionService._clean_app_name(app_name),
            user_id=user_id,
            session_id=session_id
        ).collection("events")

    def _get_sessions_collection(self,
                         *,
                         app_name: str,
                         user_id: str) -> CollectionReference:
        session_parent_path = self._get_session_path(
            app_name=FirestoreSessionService._clean_app_name(app_name),
            user_id=user_id,
            session_id=""
        ).strip("/")
        return self.client.collection(session_parent_path)

    def _delete_collection(
            self,
            coll_ref: CollectionReference,
            batch_size: int = 100,
    ):
        if batch_size < 1:
            batch_size = 1

        docs = coll_ref.list_documents(page_size=batch_size)
        deleted = 0

        for doc in docs:
            print(f"Deleting doc {doc.id} => {doc.get().to_dict()}")
            doc.delete()
            deleted = deleted + 1

        if deleted >= batch_size:
            return self._delete_collection(coll_ref, batch_size)


    async def create_session(
      self,
      *,
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]] = None,
      session_id: Optional[str] = None,
    ) -> Session:
        if not session_id:
           session_id = uuid.uuid4().hex
        app_name = FirestoreSessionService._clean_app_name(app_name)
        logger.info(f"Creating session {app_name}/{user_id}/{session_id}.")
        session = Session(id=session_id,
                          app_name=app_name,
                          user_id=user_id,
                          state=state or {},
                          events=[])
        doc = self._get_session_doc(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        session_dict = session.model_dump()
        session_dict.pop("events", None)
        session_dict["last_update_time"] = SERVER_TIMESTAMP
        session.last_update_time = doc.create(
            session_dict
        ).update_time.timestamp() # type: ignore
        return session

    async def get_session(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: str,
      config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        """Gets a session."""
        app_name = FirestoreSessionService._clean_app_name(app_name)
        logger.info(f"Loading session {app_name}/{user_id}/{session_id}.")
        session_doc = self._get_session_doc(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        doc_obj = session_doc.get()
        session_dict = doc_obj.to_dict() or {}
        if not session_dict or "id" not in session_dict:
            raise FileNotFoundError(
                f"Session {app_name}/{user_id}/{session_id} not found."
            )
        if "state" not in session_dict:
            session_dict["state"] = {}
        if "last_update_time" in session_dict:
            session_dict["last_update_time"] = session_dict[
                "last_update_time"
            ].timestamp()
        # Backwards compatibility
        if "__STATE_::RUNNING_QUERY" in session_dict["state"]:
            session_dict["state"]["RUNNING_QUERY"] = session_dict.pop(
                "__STATE_::RUNNING_QUERY"
        )
        session_dict = {
            k: v for k, v in session_dict.items()
            if not k.startswith("__STATE_::")
        }
        session = Session.model_validate(session_dict)
        session.events = []
        query = None
        events_collection = self._get_events_collection(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        if config and config.after_timestamp:
            query = events_collection.where(
                "timestamp",
                ">",
                config.after_timestamp
            ).order_by("timestamp")
        if config and config.num_recent_events:
            if not query:
                query = events_collection.order_by("timestamp")
            query = query.limit_to_last(config.num_recent_events)
        if not query:
            query = events_collection.order_by("timestamp")
        for doc in query.stream():
            session.events.append(
                Event.model_validate(
                    doc.to_dict(),
                    strict=False
                )
            )
        return session

    async def list_sessions(
      self, *, app_name: str, user_id: str
    ) -> ListSessionsResponse:
        sessions_result = []
        app_name = FirestoreSessionService._clean_app_name(app_name)
        sessions = self._get_sessions_collection(
            app_name=app_name,
            user_id=user_id,
        ).order_by("last_update_time", direction=Query.DESCENDING).stream()
        for doc in sessions:
            session = Session(id=doc.id,
                              app_name=app_name,
                              user_id=user_id,
                              state={},
                              events=[],
                              last_update_time=doc.update_time.timestamp()
            )
            sessions_result.append(session)
        return ListSessionsResponse(sessions=sessions_result)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        app_name = FirestoreSessionService._clean_app_name(app_name)
        logger.info(f"Deleting session {app_name}/{user_id}/{session_id}.")
        self._get_sessions_collection(
            app_name=app_name,
            user_id=user_id,
        ).document(session_id).delete()
        self._delete_collection(
            self._get_events_collection(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id
            )
        )

    async def close_session(self, *, session: Session):
        """Closes a session."""
        # No closed sessions supported.
        pass

    async def append_event(self, session: Session, event: Event) -> Event:
        """Appends an event to a session object."""
        if event.partial:
            return event
        await self.__update_session_state(session, event)
        session.events.append(event)
        return event

    async def __update_session_state(self, session: Session, event: Event):
        """Updates the session state based on the event."""
        collection = self._get_events_collection(
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id
        )
        collection.document(event.id).create(event.model_dump())
        if not event.actions or not event.actions.state_delta:
            return
        if not session.state:
            session.state = {}
        updated = False
        state_change_dict = {}
        for key, value in event.actions.state_delta.items():
            if key.startswith(State.TEMP_PREFIX):
                continue
            state_change_dict[f"state.{key}"] = value
            session.state[key] = value
            updated = True
        state_change_dict["last_update_time"] = SERVER_TIMESTAMP
        while updated: # Writing to Firestore only if updated
            try:
                session_doc = self._get_session_doc(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id
                )
                session.last_update_time = session_doc.update(
                    field_updates=state_change_dict
                ).update_time.timestamp() # type: ignore
                break
            except exceptions.FailedPrecondition:
                pass
