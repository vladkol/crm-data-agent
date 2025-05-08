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

import logging
import os
from pathlib import Path
import sys

from google.adk.artifacts import GcsArtifactService
from fast_api_app import get_fast_api_app

sys.path.append(str(Path(__file__).parent.parent))
from shared.config_env import prepare_environment

#################### Initialization ####################
logging.getLogger().setLevel(logging.INFO)
os.environ["AGENT_DIR"] = str(Path(__file__).parent.parent /
                                        "agents" /
                                        "data_agent")
prepare_environment()
########################################################

api_app = get_fast_api_app(
    agent_dir=os.environ["AGENT_DIR"],
    trace_to_cloud=True,
    session_db_url=f"agentengine://{os.environ['AGENT_ENGINE_ID']}",
    artifact_service=GcsArtifactService(
        bucket_name=os.environ["AI_STORAGE_BUCKET"]
    )
)


