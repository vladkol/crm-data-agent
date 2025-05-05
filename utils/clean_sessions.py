#!/usr/bin/env python
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

import hashlib
from pathlib import Path
import logging
import os
import subprocess
import sys

from vertexai import init
from vertexai.agent_engines import list as list_engines
from google.adk.sessions import VertexAiSessionService

sys.path.append(str(Path(__file__).parent.parent))
from src.shared.config_env import prepare_environment

prepare_environment()

logging.basicConfig(level=logging.INFO)

def _clean_agent(agent_name: str, user_id: str):
    init(project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        staging_bucket=f"gs://{os.environ['AI_STORAGE_BUCKET']}")
    agents = list(list_engines(filter=f'display_name="{agent_name}"'))
    if not agents:
        logging.info(f"Agent `{agent_name}` not found.")
        return
    logging.info(f"Cleaning up sessions of user {user_id} in agent `{agent_name}`.")
    agent = agents[0]
    service = VertexAiSessionService()
    real_user_id = hashlib.md5(user_id.encode()).hexdigest()
    for s in service.list_sessions(
            app_name=agent.resource_name, user_id=real_user_id).sessions:
        logging.info(f"Deleting session `{s.id}` "
                     f"of user `{real_user_id}` ({user_id}).")
        service.delete_session(app_name=agent.resource_name,
                               user_id=real_user_id,
                               session_id=s.id)


if __name__ == "__main__":
    if "AGENT_ENGINE_ID" not in os.environ or not os.environ["AGENT_ENGINE_ID"]:
        logging.error("Configuration variable AGENT_ENGINE_ID is not set.")
        sys.exit(1)
    agent_name = os.environ["AGENT_NAME"]
    if len(sys.argv) < 2:
        try:
            user_name = (
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
            user_name = "user@ai"
    else:
        user_name = sys.argv[1]
    _clean_agent(agent_name, user_name)