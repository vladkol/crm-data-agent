import datetime
import logging
from typing import Any
from typing import Optional
import uuid

from google.adk.events.event import Event
from google.adk.sessions.base_session_service import (BaseSessionService,
                                                      GetSessionConfig,
                                                      ListEventsResponse,
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

_STATE_PREFIX = "__STATE_::"

class FirestoreSessionService(BaseSessionService):
    def __init__(self,
                 database: str,
                 sessions_collection: str = "/",
                 project_id: Optional[str] = None):

        self.client = Client(project_id, database=database)
        self.sessions_collection = sessions_collection

    def _get_session_path(self,
                          *,
                          app_name: str,
                          user_id: str,
                          session_id: str) -> str:
        return (f"{self.sessions_collection}"
                f"/agents/{app_name}"
                f"/users/{user_id}"
                f"/sessions/{session_id}").strip("/")

    def _get_session_doc(self,
                         *,
                         app_name: str,
                         user_id: str,
                         session_id: str) -> DocumentReference:
        sessions_collection = self._get_sessions_collection(
            app_name=app_name,
            user_id=user_id
        )
        return sessions_collection.document(session_id)

    def _get_events_collection(self,
                         *,
                         app_name: str,
                         user_id: str,
                         session_id: str) -> CollectionReference:
        return self._get_session_doc(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        ).collection("events")

    def _get_sessions_collection(self,
                         *,
                         app_name: str,
                         user_id: str) -> CollectionReference:
        session_parent_path = self._get_session_path(
            app_name=app_name,
            user_id=user_id,
            session_id=""
        ).strip("/")
        return self.client.collection(session_parent_path)


    def create_session(
      self,
      *,
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]] = None,
      session_id: Optional[str] = None,
    ) -> Session:
        if not session_id:
           session_id = uuid.uuid4().hex
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
        session_dict.pop("state", None)
        session_dict.pop("events", None)
        session_dict["last_update_time"] = SERVER_TIMESTAMP
        state_dict = session.state
        for k,v in state_dict.items():
            session_dict[f"{_STATE_PREFIX}{k}"] = v
        session.last_update_time = doc.create(
            session_dict
        ).update_time.timestamp() # type: ignore
        return session

    def get_session(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: str,
      config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        """Gets a session."""
        logger.info(f"Loading session {app_name}/{user_id}/{session_id}.")
        session_doc = self._get_session_doc(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        doc_obj = session_doc.get()
        session_dict = doc_obj.to_dict() or {}
        if "last_update_time" in session_dict:
            session_dict["last_update_time"] = session_dict[
                "last_update_time"
            ].timestamp()
        state_props = [
            k
            for k in session_dict
            if k.startswith(_STATE_PREFIX)
        ]
        session_state = {}
        for state_prop in state_props:
            state_key_without_prefix = state_prop[len(_STATE_PREFIX):]
            session_state[state_key_without_prefix] = session_dict.pop(
                state_prop, None
            )
        session = Session.model_validate(session_dict)
        session.state = session_state
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
            session.events.append(Event.model_validate(doc.to_dict()))
        return session

    def list_sessions(
      self, *, app_name: str, user_id: str
    ) -> ListSessionsResponse:
        sessions_result = []
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

    def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        logger.info(f"Deleting session {app_name}/{user_id}/{session_id}.")
        self._get_sessions_collection(
            app_name=app_name,
            user_id=user_id,
        ).document(session_id).delete()

    def list_events(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> ListEventsResponse:
        """Lists events in a session."""
        events_result = []
        collection = self._get_events_collection(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        for doc in collection.order_by("timestamp").stream():
            event_document = collection.document(doc.id).get().to_dict()
            events_result.append(Event.model_validate(event_document))
        return ListEventsResponse(events=events_result)

    def close_session(self, *, session: Session):
        """Closes a session."""
        # No closed sessions supported.
        pass

    def append_event(self, session: Session, event: Event) -> Event:
        """Appends an event to a session object."""
        if event.partial:
            return event
        self.__update_session_state(session, event)
        session.events.append(event)
        return event

    def __update_session_state(self, session: Session, event: Event):
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
        state_dict = {}
        state_object_dict = {}
        for key, value in event.actions.state_delta.items():
            if key.startswith(State.TEMP_PREFIX):
                continue
            state_dict[f"{_STATE_PREFIX}{key}"] = value
            state_object_dict[key] = value
            updated = True
        while updated: # Writing to Firestore only if updated
            try:
                session_doc = self._get_session_doc(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id
                )
                state_dict["last_update_time"] = SERVER_TIMESTAMP
                session.last_update_time = session_doc.update(
                    state_dict
                ).update_time.timestamp() # type: ignore
                session.state.update(state_object_dict)
                break
            except exceptions.FailedPrecondition:
                pass
