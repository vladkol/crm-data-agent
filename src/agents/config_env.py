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
"""Configuration Environment Variables Loader"""

import os
from dotenv import load_dotenv
from pathlib import Path
import logging
import sys


_env_requirements = {
    "GOOGLE_CLOUD_PROJECT": None, # None value means it has to be non-empty
    "GOOGLE_CLOUD_LOCATION": None,

    # `$`` at the beginning refers to another variable
    "BQ_PROJECT_ID": "$GOOGLE_CLOUD_PROJECT",
    "SFDC_DATA_PROJECT_ID": "$BQ_PROJECT_ID",
    "SFDC_BQ_DATASET": None,
    "BQ_LOCATION": "US",
    "SFDC_METADATA_FILE": "sfdc_metadata.json", # default value
    "AI_STORAGE_BUCKET": None,
}
_prepared = False

def prepare_environment():
    global _prepared
    if _prepared:
        return
    _prepared = True
    dontenv_path = Path(__file__).parent.parent / ".env"
    if not dontenv_path.exists():
        logging.critical(f"{dontenv_path} not found.")
        sys.exit(1)
    load_dotenv(dotenv_path=dontenv_path, override=True)
    for name, val in _env_requirements.items():
        if name in os.environ and len(os.environ[name].strip()) > 0:
            continue
        if val is None:
            logging.error(f"{name} variable must be set.")
            sys.exit(1)
        elif val.startswith("$"):
            ref_name = val[1:]
            os.environ[name] = os.environ[ref_name]
        else:
            os.environ[name] = val
