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

from pathlib import Path
import logging
import os
import sys

from dotenv import load_dotenv

from vertexai import init
from vertexai.agent_engines import create, list as list_engines

logging.basicConfig(level=logging.CRITICAL)

def _deploy_agent(agent_name: str)->str:
    init(project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        staging_bucket=f"gs://{os.environ['AI_STORAGE_BUCKET']}")
    agents = list(list_engines(filter=f'display_name="{agent_name}"'))
    if agents:
        return agents[0].resource_name
    else:
        return create(display_name=agent_name).resource_name

def get_agent_engine(agent_name: str) -> str:
    """Deploys an agent engine instance without code.
    Sets AGENT_ENGINE_ID environment variable and write it to .env file.
    If AGENT_ENGINE_ID environment variable is not empty,
    does nothing and returns it

    Args:
        agent_name (str): display name of the engine instance

    Returns:
        str: Agent Engine Id
    """
    dotenv_path = Path(__file__).parent.parent / "src" / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=True)
    if "AGENT_ENGINE_ID" not in os.environ or not os.environ["AGENT_ENGINE_ID"]:
        resource = _deploy_agent(agent_name)
        agent_id = resource.split("/")[-1]
        os.environ["AGENT_ENGINE_ID"] = agent_id
        with dotenv_path.open("a") as f:
            f.write(f"AGENT_ENGINE_ID=\"{agent_id}\"")
    return os.environ["AGENT_ENGINE_ID"]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        agent_name = "crm_data_agent"
    else:
        agent_name = sys.argv[1]
    print(get_agent_engine(agent_name))