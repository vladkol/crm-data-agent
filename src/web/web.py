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
import importlib.util
from io import BytesIO
import json
import logging
import os
import subprocess
import sys
from time import time
from typing import Optional

import pandas as pd
import streamlit as st

from google.genai.types import Content, Part

from google.adk import Agent, Runner
from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

from PIL import Image

_root_agent: Optional[Agent] = None # Root Agent

logging.getLogger().setLevel(logging.INFO)

st.set_page_config(layout="wide",
                   page_icon=":material/bar_chart:",
                   page_title="📊 CRM Data Agent 🦄",
                   initial_sidebar_state="collapsed")

st.title("📊 CRM Data Agent 🦄")
st.subheader("This Agent can perform Data Analytics tasks "
             "over Salesforce data in BigQuery.")
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


def _process_event(event: Event):
    if not event:
        return
    session = get_session()
    artifact_service = st.session_state["artifact_service"]

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
            artifact = artifact_service.load_artifact(
                app_name=session.app_name, user_id=session.user_id,
                session_id=session.id, filename=filename, version=version
            )
            if (artifact.inline_data
                and not filename.startswith(f"{event.invocation_id}.")
                and artifact.inline_data.mime_type.startswith('image/')):
                    with BytesIO(artifact.inline_data.data) as image_io:
                        with Image.open(image_io) as img:
                            st.image(img)
            elif artifact.text:
                if filename.endswith(".json"):
                    st.json(artifact.text)
                elif filename.endswith(".vg"):
                    data_file_name = filename.rsplit(".", 1)[0] + ".parquet"
                    pq_bytes = artifact_service.load_artifact(
                        app_name=session.app_name,
                        user_id=session.user_id,
                        session_id=session.id,
                        filename=data_file_name,
                        version=version).inline_data.data # type: ignore
                    chart_dict = json.loads(artifact.text)
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
                else:
                    st.markdown(artifact.text, unsafe_allow_html=True)

    if function_calls:
        _process_function_calls(function_calls)
    if function_responses:
        _process_function_responses(function_responses)


def _render_chat(events):
    if events is None:
        if "event_history" not in st.session_state:
            return
        events = st.session_state.event_history
    for event in events:
        _process_event(event)


async def ask_agent(question: str):
    start = time()
    session = get_session()
    content = Content(parts=[
        Part.from_text(text=question)
    ],role="user")

    user_event = Event(author="user", content=content)
    st.session_state["event_history"].append(user_event)
    _render_chat([user_event])

    events = get_agent_runner().run_async(user_id=session.user_id,
                                          session_id=session.id,
                                          new_message=content)
    async for event in events:
        # st.session_state.event_history.append(event)
        _render_chat([event])
    end = time()
    st.text(f"Flow duration: {end - start:.2f}s")


def get_session() -> Session:
    default_email = "user@ai"
    runner = get_agent_runner()
    if "adk_session" not in st.session_state:
        agent_engine_id = os.environ.get("AGENT_ENGINE_ID", None)
        if agent_engine_id:
            try:
                real_user_email = (
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
                real_user_email = hashlib.md5(real_user_email.encode()).hexdigest()
            except subprocess.CalledProcessError:
                real_user_email = ""
            if not real_user_email:
                real_user_email = default_email
            session = runner.session_service.create_session(
                app_name=agent_engine_id,
                user_id=real_user_email
            )
        else:
            session = runner.session_service.create_session(
                                      app_name=runner.app_name,
                                      user_id=default_email)
        st.session_state.event_history = []
        st.session_state.adk_session = session
    return st.session_state.adk_session


def get_root_agent() -> Agent:
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


def get_agent_runner()-> Runner:
    if "adk_runner" not in st.session_state:
        agent_engine_id = os.environ.get("AGENT_ENGINE_ID", None)
        vertex_ai_bucket = os.environ.get("AI_STORAGE_BUCKET", None)
        if agent_engine_id and vertex_ai_bucket:
            from google.adk.sessions import VertexAiSessionService
            from agent_artifact_service import GcsPartArtifactService

            session_service = VertexAiSessionService(
                project=os.environ["GOOGLE_CLOUD_PROJECT"],
                location=os.environ["GOOGLE_CLOUD_LOCATION"],
            )
            artifact_service = GcsPartArtifactService(
                bucket_name=vertex_ai_bucket
            )
        else:
            agent_engine_id = "crm_data_agent"
            session_service = InMemorySessionService()
            artifact_service = InMemoryArtifactService()
        memory_service = InMemoryMemoryService()
        st.session_state["artifact_service"] = artifact_service
        runner = Runner(app_name=agent_engine_id,
                        agent=get_root_agent(),
                        artifact_service=artifact_service,
                        session_service=session_service,
                        memory_service=memory_service)
        st.session_state["adk_runner"] = runner
    return st.session_state["adk_runner"]


def get_all_sessions() -> list[Session]:
    if "adk_sessions" in st.session_state:
        return st.session_state["adk_sessions"]
    runner = get_agent_runner()
    current_session = get_session()
    sessions_response = runner.session_service.list_sessions(
            app_name=current_session.app_name,
            user_id=current_session.user_id)
    sessions = sessions_response.sessions
    ids = [s.id for s in sessions]
    if current_session.id not in ids:
        sessions.insert(0, current_session)
        st.session_state.adk_sessions = sessions
    return sessions_response.sessions


async def app():
    top = st.container()

    with st.spinner("Initializing...", show_time=False):
        current_session = get_session()
        sessions_list = get_all_sessions()
    with st.sidebar:
        session_ids = [s.id for s in sessions_list]
        sessions = {s.id : s for s in sessions_list}
        selected_option = st.selectbox("Select a session:",
                                       session_ids,
                                       index=0)
        if selected_option and selected_option != current_session.id:
            selected_session = sessions[selected_option]
            selected_session = get_agent_runner(
            ).session_service.get_session(
                app_name=selected_session.app_name,
                user_id=selected_session.user_id,
                session_id=selected_session.id
            )
            st.session_state.adk_session = selected_session
            with top:
                _render_chat(selected_session.events) # type: ignore
    with st.spinner("Thinking...", show_time=False):
        question = st.chat_input("Ask a question about your data!")
        if "question" not in current_session.state:
            current_session.state["question"] = question
        with top:
            with st.spinner("Thinking...", show_time=True):
                _render_chat(None)
                if question:
                    await ask_agent(question)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app())

