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

import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

sys.path.append(".")
from src.agents.config_env import prepare_environment

git_cmd_line = "git clone --depth 1  --no-tags https://github.com/vladkol/sfdc-kittycorn ./data && rm -rf ./data/.git"
subprocess.run(git_cmd_line, shell=True)

prepare_environment()
_python_cmd_line = (f"\"{sys.executable}\" ./data/deploy_to_my_project.py "
                    f"--project {os.environ['SFDC_DATA_PROJECT_ID']} "
                    f"--dataset {os.environ['SFDC_BQ_DATASET']} "
                    f"--location {os.environ['BQ_LOCATION']}")
subprocess.run(_python_cmd_line, shell=True)
