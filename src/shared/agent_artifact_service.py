# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""GcsArtifactService wrapper for various content types."""

from typing import Optional
from typing_extensions import override
from google.adk.artifacts import GcsArtifactService
from google.genai import types

_supported_text_types = {
    "txt": "text/plain",
    "json": "application/json",
    "md": "text/markdown",
    "csv": "text/csv"
}

class GcsPartArtifactService(GcsArtifactService):
    """GcsArtifactService wrapper for part content type."""

    def __init__(self, bucket_name: str, **kwargs):
        super().__init__(bucket_name, **kwargs)

    @override
    def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        version: Optional[int] = None,
    ) -> Optional[types.Part]:
        artifact = super().load_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            version=version)
        if not artifact or not artifact.inline_data:
            return artifact
        if artifact.inline_data.mime_type in _supported_text_types.values():
            text = artifact.inline_data.data.decode("utf-8") # type: ignore
            artifact.text = text
            artifact.inline_data = None
        return artifact


    @override
    def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        artifact: types.Part,
    ) -> int:
        artifact_repr = artifact
        if artifact_repr.text:
            artifact_repr = artifact_repr.model_copy()
            ext = filename.rsplit(".", 1)[-1].lower()
            data = artifact_repr.text.encode(encoding="utf-8") # type: ignore
            mime_type="text/plain"
            if ext in _supported_text_types:
                mime_type = _supported_text_types[ext]
            artifact_repr.inline_data = types.Blob(
                data=data,
                mime_type=mime_type
            )
        return super().save_artifact(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=filename,
                artifact=artifact_repr)