# Copyright 2024 Google LLC
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
"""Salesforce CRM metadata extractor"""

import json
import pathlib
import threading
import typing


class SFDCMetadata:
    """Salesforce CRM metadata client"""

    def __init__(
        self,
        project_id: str,
        dataset_name: str,
        metadata_file: typing.Optional[str] = None
    ) -> None:
        """
        Args:
            project_id (str): GCP project id of BigQuery data.
            dataset_name (str): BigQuery dataset name.
            metadata_file (str, Optional): path to metadata file.
                If not provided,
                it will be "{.project_id}__{dataset_name}.json"
                in the current directory.
        """
        self.project_id = project_id
        self.dataset_name = dataset_name
        if metadata_file:
            self._metadata_file_name = metadata_file
        else:
            self._metadata_file_name = (f"{self.project_id}__"
                                        "{self.dataset_name}.json")
        self._metadata = {}
        self._lock = threading.Lock()

    def get_metadata(self) -> typing.Dict[str, typing.Any]:
        """Extract metadata from Salesforce CRM"""
        if len(self._metadata) > 0:
            return self._metadata

        with self._lock:
            if len(self._metadata) == 0:
                metadata_path = pathlib.Path(self._metadata_file_name)
                if metadata_path.exists():
                    self._metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8"))
                else:
                    raise FileNotFoundError(self._metadata_file_name)

        return self._metadata

