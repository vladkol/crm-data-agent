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
"""Agent web bootstrap script"""

import os
from pathlib import Path
import sys

import streamlit.web.bootstrap as bootstrap

sys.path.append(str(Path(__file__).parent.parent))
from shared.config_env import prepare_environment

flag_options = {
      "server.headless": True,
      "server.enableCORS": False,
      "server.enableXsrfProtection": False,
      "server.fileWatcherType": None,
      "server.port": int(os.getenv("PORT", 8080)),
      "server.enableWebsocketCompression": True,
      "browser.gatherUsageStats": False,
      "client.toolbarMode": "minimal",
      "global.developmentMode": False,
      "theme.font": "Inter, Verdana",
      "theme.base": "dark",
      "logger.level": "info",
}

if __name__ == "__main__":
    if len(sys.argv) >= 2:
        agent_dir = sys.argv[1]
    else:
        agent_dir = "."
    if len(sys.argv) >= 3:
        target_env = sys.argv[2]
    else:
        target_env = "local"
    print(f"Target runtime environment: {target_env}")

    agent_dir = os.path.abspath(agent_dir)
    print(f"Agent directory: {agent_dir}")
    os.environ["AGENT_DIR"] = agent_dir
    os.environ["RUNTIME_ENVIRONMENT"] = target_env
    prepare_environment()
    app_script_path = os.path.join(os.path.dirname(__file__), "web.py")
    bootstrap.load_config_options(flag_options)
    bootstrap.run(
        app_script_path,
        False,
        [],
        flag_options,
    )
