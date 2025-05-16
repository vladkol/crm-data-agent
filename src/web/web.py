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
"""Agent streamlit web app"""

import asyncio
import hashlib
from io import BytesIO
import json
import logging
import os
import subprocess
from time import time

import pandas as pd
import streamlit as st

from google.genai.types import Content, Part

from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from shared.firestore_session_store import (FirestoreSessionService
                                            as SessionService)

from PIL import Image

from google.adk.artifacts import GcsArtifactService
from agent_runtime_client import AgentEngineRuntime, FastAPIEngineRuntime

MAX_RUN_RETRIES = 10
DEFAULT_USER_ID = "user@ai"

logging.getLogger().setLevel(logging.INFO)

st.set_page_config(layout="wide",
                   page_icon=":material/bar_chart:",
                   page_title="📊 CRM Data Agent 🦄",
                   initial_sidebar_state="expanded")

st.title("📊 CRM Data Agent 🦄")
st.subheader("This Agent can perform Data Analytics tasks "
             "over Salesforce data in BigQuery.")
st.markdown("[github.com/vladkol/crm-data-agent]"
            "(https://github.com/vladkol/crm-data-agent)")
st.markdown("#### Examples of questions:")
st.markdown("""
* 🔝 Top 5 customers in every country.
* 📢 What are our best lead sources?
* 📈 Lead conversion trends in the US.
""".strip())

hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {
    display:none !important;
}
.block-container {
    padding-top: 2rem;
    padding-bottom: 5rem;
    padding-left: 2rem;
    padding-right: 2rem;
}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)


######################### Event rendering #########################
@st.fragment
def _process_function_calls(function_calls):
  title = f"⚡ {', '.join([fc.name for fc in function_calls])}"
  with st.expander(title):
    for fc in function_calls:
      title = f'**{fc.name}**'
      if fc.id:
        title += f' ({fc.id})'
      st.write(title)
      st.write(fc.args)


@st.fragment
def _process_function_responses(function_responses):
  title = f"✔️ {', '.join([fr.name for fr in function_responses])}"
  with st.expander(title):
    for fr in function_responses:
      title = f'**{fr.name}**'
      if fr.id:
        title += f' ({fr.id})'
      st.write(title)
      st.write(fr.response)


async def _process_event(event: Event) -> bool:
    if not event:
        return False
    session = st.session_state.adk_session
    artifact_service = st.session_state.artifact_service

    function_calls = []
    function_responses = []

    if event.content and event.content.parts:
        content = event.content
        for part in content.parts: # type: ignore
            if part.text and not part.text.strip():
                continue
            if part.thought and part.text:
                msg = '\n'.join('> %s' % f
                            for f in part.text.strip().split()) # type: ignore
            else:
                msg = part.text or ""
                msg = msg.strip()
            if part.function_call:
                function_calls.append(part.function_call)
            elif part.function_response:
                function_responses.append(part.function_response)
        if msg:
            if content.role == "model":
                msg_role = "ai"
            elif content.role == "user":
                msg_role = "human"
            else:
                msg_role = "assistant"
            with st.chat_message(msg_role):
                st.markdown(msg, unsafe_allow_html=True)

    if event.actions.artifact_delta:
        for filename, version in event.actions.artifact_delta.items():
            artifact = await artifact_service.load_artifact(
                app_name=session.app_name, user_id=session.user_id,
                session_id=session.id, filename=filename, version=version
            )
            if not artifact.inline_data or not artifact.inline_data.data:
                continue
            if (artifact.inline_data.mime_type.startswith('image/')):
                    # skip images with the invocation id filename
                    if filename.startswith(f"{event.invocation_id}."):
                        continue
                    with BytesIO(artifact.inline_data.data) as image_io:
                        with Image.open(image_io) as img:
                            st.image(img)
            elif (artifact.inline_data.mime_type ==
                        "application/vnd.vegalite.v4+json"
                  or filename.endswith(".vg")
                      and (artifact.inline_data.mime_type in
                            ["application/json", "text/plain"])
            ):
                # find a parquet file to supply the chart with data
                data_file_name = filename.rsplit(".", 1)[0] + ".parquet"
                parquet_file = await artifact_service.load_artifact(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                    filename=data_file_name,
                    version=version)
                if parquet_file and parquet_file.inline_data:
                    pq_bytes = parquet_file.inline_data.data # type: ignore
                else:
                    pq_bytes = None
                text = artifact.inline_data.data.decode("utf-8")
                chart_dict = json.loads(text)
                if pq_bytes:
                    with BytesIO(pq_bytes) as pq_file:
                        df = pd.read_parquet(pq_file)
                    st.dataframe(df)
                    chart_dict.pop("data", None)
                else:
                    df = None
                st.vega_lite_chart(data=df,
                                    spec=chart_dict,
                                    use_container_width=False)
            elif artifact.inline_data.mime_type == "application/json":
                st.json(artifact.inline_data.data.decode("utf-8"))
            elif artifact.inline_data.mime_type == "text/markdown":
                st.markdown(artifact.inline_data.data.decode("utf-8"),
                            unsafe_allow_html=True)
            elif artifact.inline_data.mime_type == "text/csv":
                st.markdown(
                    "```csv\n" +
                    artifact.inline_data.data.decode("utf-8") + "\n```",
                    unsafe_allow_html=True)
            elif artifact.inline_data.mime_type.startswith("text/"):
                st.text(artifact.inline_data.data.decode("utf-8"))

    if function_calls:
        _process_function_calls(function_calls)
    if function_responses:
        _process_function_responses(function_responses)
    return True


async def _render_chat(events):
    for event in events:
        await _process_event(event)


######################### Configuration management #########################

def _get_user_id() -> str:
    """Retrieves user id (email) from the environment,
    and generate an MD5 hash of it for using with the session service

    Returns:
        str: user id for the session service
    """
    if "agent_user_id" in st.session_state:
        return st.session_state["agent_user_id"]

    user_id = st.context.headers.get(
            "X-Goog-Authenticated-User-Email", "").split(":", 1)[-1]
    if not user_id:
        try:
            user_id = (
                subprocess.check_output(
                    (
                        "gcloud config list account "
                        "--format \"value(core.account)\" "
                        f"--project {os.environ['GOOGLE_CLOUD_PROJECT']} "
                        "-q"
                    ),
                    shell=True,
                )
                .decode()
                .strip()
            )
        except subprocess.CalledProcessError:
            user_id = ""
    if not user_id:
            user_id = DEFAULT_USER_ID
    user_id_md5 = hashlib.md5(user_id.lower().encode()).hexdigest()
    st.session_state["agent_user_id"] = user_id_md5
    return user_id_md5


def _initialize_configuration():
    if "adk_configured" in st.session_state:
        return st.session_state.adk_configured
    agent_engine_id = os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"]
    vertex_ai_bucket = os.environ["AI_STORAGE_BUCKET"]

    # session_service = VertexAiSessionService(
    #     project=os.environ["GOOGLE_CLOUD_PROJECT"],
    #     location=os.environ["GOOGLE_CLOUD_LOCATION"],
    # )
    session_service = SessionService(
          database=os.environ["FIREBASE_SESSION_DATABASE"],
          sessions_collection=os.getenv("FIREBASE_SESSION_COLLECTION", "/")
    )
    artifact_service = GcsArtifactService(
        bucket_name=vertex_ai_bucket
    )
    memory_service = InMemoryMemoryService()
    st.session_state.artifact_service = artifact_service
    st.session_state.session_service = session_service
    st.session_state.app_name = agent_engine_id
    st.session_state.memory_service = memory_service
    st.session_state.adk_configured = True

    # Cleaning up empty sessions
    sessions_list = _get_all_sessions()
    deleted_sessions = []
    for index, s in enumerate(sessions_list):
        session = session_service.get_session(
            app_name=st.session_state.app_name,
            user_id=_get_user_id(),
            session_id=s.id)
        if "RUNNING_QUERY" not in session.state: # type: ignore
            # Deleting empty session.
            # Couldn't come with a better place
            # without increasing complexity.
            try:
                session_service.delete_session(
                    app_name=st.session_state.app_name,
                    user_id=_get_user_id(),
                    session_id=s.id
                )
            except Exception as ex:
                logging.warning("Couldn't delete a session." \
                                f"It may be deleted already:\n{ex}")
            deleted_sessions.append(index)
        else:
            # We have a session to select
            break
    while deleted_sessions:
        sessions_list.pop(deleted_sessions[-1])
        deleted_sessions.pop(-1)


######################### Session management #########################

def _create_session() -> Session:
    if "adk_session" not in st.session_state:
        session = st.session_state.session_service.create_session(
            app_name=st.session_state.app_name,
            user_id=_get_user_id()
        )
        st.session_state.adk_session = session
        st.session_state.all_adk_sessions = (st.session_state.all_adk_sessions
                                             or [])
        st.session_state.all_adk_sessions.insert(0, session)
    return st.session_state.adk_session


def _get_all_sessions() -> list[Session]:
    if "all_adk_sessions" in st.session_state:
        return st.session_state.all_adk_sessions
    sessions_response = st.session_state.session_service.list_sessions(
            app_name=st.session_state.app_name,
            user_id=_get_user_id())
    sessions = sessions_response.sessions
    st.session_state.all_adk_sessions = sessions or []
    return sessions


######################### Agent Request Handler #########################

async def ask_agent(question: str):
    start = time()
    session = st.session_state.adk_session
    content = Content(parts=[
        Part.from_text(text=question)
    ],role="user")

    user_event = Event(author="user", content=content)
    await _render_chat([user_event])

    runtime_name = os.environ["RUNTIME_ENVIRONMENT"].lower()
    if runtime_name == "local":
        runtime = FastAPIEngineRuntime(session)
    elif runtime_name == "agent_engine":
        runtime = AgentEngineRuntime(
            session,
            os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"])
    else:
        ValueError(f"`{runtime_name}` is not a valid runtime name.")

    model_events_cnt = 0 # Count valid model events in this run
    st.session_state.session_service.append_event(
        session=st.session_state.adk_session,
        event=Event(
            author="runtime",
            actions=EventActions(
                state_delta={
                    "RUNNING_QUERY": True
                }
            )
        )
    )
    for _ in range(MAX_RUN_RETRIES):
        if model_events_cnt > 0:
            break
        async for event in runtime.stream_query(question):
            # If no valid model events in this run, but got an func call error,
            # retry the run
            if (event.error_code
                    and event.error_code == "MALFORMED_FUNCTION_CALL"
                    and model_events_cnt == 0):
                print("Retrying the run")
                break
            if event.content and event.content.role == "model":
                model_events_cnt += 1
            await _render_chat([event])
    st.session_state.session_service.append_event(
        session=st.session_state.adk_session,
        event=Event(
            author="runtime",
            actions=EventActions(
                state_delta={
                    "RUNNING_QUERY": False
                }
            )
        )
    )
    # Re-retrieve the session
    st.session_state.adk_session = st.session_state.session_service.get_session(
        app_name=session.app_name,
        user_id=session.user_id,
        session_id=session.id
    )
    end = time()
    st.text(f"Flow duration: {end - start:.2f}s")


######################### Streamlit main flow #########################

async def app():
    top = st.container()

    if "adk_configured" not in st.session_state:
        with st.spinner("Initializing...", show_time=False):
            _initialize_configuration()
            sessions_list = _get_all_sessions()
    else:
        sessions_list = _get_all_sessions()
    session_ids = [s.id for s in sessions_list]
    session_service = st.session_state.session_service
    current_session = None
    current_index = 0
    if "session" in st.query_params:
        selected_session_id = st.query_params["session"]
        if selected_session_id in session_ids:
            selected_session_id = st.query_params["session"]
            if (
                "adk_session" in st.session_state
                and st.session_state.adk_session.id != selected_session_id
            ):
                st.session_state.pop("adk_session")
            current_index = session_ids.index(selected_session_id)
        else:
            st.query_params.pop("session")
            selected_session_id = -1
    else:
        selected_session_id = -1
    if "adk_session" in st.session_state:
        current_session = st.session_state.adk_session
    elif selected_session_id != -1:
        selected_session = sessions_list[current_index]
        with st.spinner("Loading...", show_time=False):
            current_session = session_service.get_session(
                app_name=selected_session.app_name,
                user_id=selected_session.user_id,
                session_id=selected_session.id
            )
            st.session_state.adk_session = current_session

    if not current_session:
        with st.spinner("Creating a new session...", show_time=False):
            current_session = _create_session()
        st.session_state.adk_session = current_session
        st.rerun()
    else:
        st.query_params["session"] = current_session.id

    with st.sidebar:
        st.markdown("### Sessions")
        if st.button("New Session"):
            with st.spinner("Creating a new session...", show_time=False):
                st.session_state.pop("adk_session", None)
                current_session = _create_session()
                st.session_state.adk_session = current_session
                st.query_params["session"] = current_session.id
            st.rerun()

        sessions_list = st.session_state.all_adk_sessions
        session_ids = [s.id for s in sessions_list]
        sessions = {s.id : s for s in sessions_list}
        selected_option = st.selectbox("Select a session:",
                                       session_ids,
                                       index=current_index)
        if selected_option and selected_option != current_session.id: # type: ignore
            selected_session = sessions[selected_option]
            with st.spinner("Loading...", show_time=False):
                selected_session = session_service.get_session(
                    app_name=selected_session.app_name,
                    user_id=selected_session.user_id,
                    session_id=selected_session.id
                )
            st.session_state.adk_session = selected_session
            st.query_params["session"] = selected_session.id
    with top:
        await _render_chat(st.session_state.adk_session.events) # type: ignore
    with st.spinner("Thinking...", show_time=False):
        question = st.chat_input("Ask a question about your data.")
        if "question" not in current_session.state:
            current_session.state["question"] = question
        with top:
            with st.spinner("Thinking...", show_time=True):
                if question:
                    await ask_agent(question)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app())
