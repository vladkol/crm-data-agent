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

from urllib.parse import unquote, urlparse, parse_qs

from google.cloud import bigquery

from simple_salesforce import Salesforce  # type: ignore

_system_fields_description_formats = {
    "Id": "Id of %s",
    "OwnerId": "Id of the User who owns this %s",
    "IsArchived": "Is this %s archived",
    "IsDeleted": "Is this %s deleted",
    "Name": "Name of %s",
    "Description": "Description of %s",
    "CreatedById": "Id of the User who created this %s",
    "LastModifiedById": "Id of the last User who modified this %s",
}

_extra_descriptions_path = "sfdc_extra_descriptions.json"

from .sfdc_metadata import SFDCMetadata

class SFDCMetadataBuilder(SFDCMetadata):
    """Salesforce CRM metadata extractor"""

    def __init__(
        self,
        sfdc_auth_parameters: typing.Union[str, typing.Dict[str, str]],
        bq_client: bigquery.Client,
        project_id: str,
        dataset_name: str,
        metadata_file: typing.Optional[str] = None,
        table_to_object_mapping: typing.Optional[typing.Dict[str, str]] = None
    ) -> None:
        """
        Args:
            sfdc_auth_parameters (typing.Union[str, typing.Dict[str, str]]):
                May be string or a string dictionary.
                - If a string, it should be a Secret Manager secret version name
                (projects/PROJECT_NUMBER/secrets/SECRET_NAME/versions/latest)
                The secret value may be an Airflow connection string for Salesforce (salesforce://)
                or a json text. Json text will be converted to a dictionary
                (see dictionary details below).
                - If a dictionary, it must contain a valid combination of these parameters,
                    as described here https://github.com/simple-salesforce/simple-salesforce/blob/1d28fa18438d3840140900d4c00799798bad57b8/simple_salesforce/api.py#L65
                        * domain -- The domain to using for connecting to Salesforce. Use
                                        common domains, such as 'login' or 'test', or
                                        Salesforce My domain. If not used, will default to
                                        'login'.

                        -- Password Authentication:
                            * username -- the Salesforce username to use for authentication
                            * password -- the password for the username
                            * security_token -- the security token for the username

                        -- OAuth 2.0 Connected App Token Authentication:
                            * consumer_key -- the consumer key generated for the user
                            * consumer_secret -- the consumer secret generated for the user

                        -- OAuth 2.0 JWT Bearer Token Authentication:
                            * consumer_key -- the consumer key generated for the user

                        Then either
                            * privatekey_file -- the path to the private key file used
                                                for signing the JWT token
                            OR
                            * privatekey -- the private key to use
                                            for signing the JWT token

                        -- Direct Session and Instance Access:
                            * session_id -- Access token for this session

                        Then either
                            * instance -- Domain of your Salesforce instance, i.e.
                            `na1.salesforce.com`
                            OR
                            * instance_url -- Full URL of your instance i.e.
                            `https://na1.salesforce.com

            bq_client (bigquery.Client): BigQuery client
            project_id (str): GCP project id of BigQuery data.
            dataset_name (str): BigQuery dataset name.
            object_to_table_mapping: optional dictionary for mapping BigQuery table
                                    names to SFDC object names.
        """
        super().__init__(project_id, dataset_name, metadata_file)
        self.bq_client = bq_client
        self.table_to_object_mapping = table_to_object_mapping

        if isinstance(sfdc_auth_parameters, str):
            # sfdc_auth_parameters is a path to a Secret Manager secret
            # "projects/PROJECT_NUMBER/secrets/SECRET_NAME/versions/latest"
            from google.cloud import secretmanager # type: ignore
            sm_client = secretmanager.SecretManagerServiceClient()
            secret_response = sm_client.access_secret_version(
                name=sfdc_auth_parameters)
            secret_payload = secret_response.payload.data.decode("utf-8")
            if secret_payload.startswith("salesforce://"):
                # Airflow connections string
                secret_payload = unquote(
                    secret_payload.replace("salesforce://", ""))
                username = None
                password = ""
                url_parts = secret_payload.rsplit("@", 1)
                if len(url_parts) > 1:
                    parsed = urlparse(url_parts[1])
                    username, password = url_parts[0].split(":", 1)
                else:
                    parsed = urlparse(secret_payload)
                url_query_dict = parse_qs(parsed.query)
                auth_dict = {k: v[0] for k, v in url_query_dict.items()}
                if username:
                    auth_dict["username"] = username
                    auth_dict["password"] = password
                auth_dict["instance_url"] = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            else:
                # Just a json string
                auth_dict = json.loads(secret_payload)
        else:
            # This is already a dictionary
            auth_dict = sfdc_auth_parameters

        for k in list(auth_dict.keys()):
            if k != k.lower() and k != "organizationId":
                auth_dict[k.lower()] = auth_dict.pop(k)

        for k in ["consumer_key", "consumer_secret", "security_token",
                  "session_id", "instance_url", "client_id", "privatekey_file"]:
            no_underscore = k.replace("_", "")
            if no_underscore in auth_dict:
                auth_dict[k] = auth_dict.pop(no_underscore)

        if "domain" in auth_dict:
            if "." not in auth_dict["domain"]:
                auth_dict["domain"] += ".my"
            elif auth_dict["domain"].endswith(".salesforce.com"):
                auth_dict["domain"] = auth_dict["domain"].replace(
                    ".salesforce.com", "")

        # auth_dict["version"] = "61.0"
        self.sfdc_connection = Salesforce(**auth_dict)  # type: ignore
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
                    self._extract_metadata()
                    self._enhance_metadata()
                    metadata_path.write_text(json.dumps(
                        self._metadata, indent=2))
        return self._metadata

    def _enhance_metadata(self) -> bool:
        file_path = pathlib.Path(__file__).parent / pathlib.Path(_extra_descriptions_path)
        if not file_path.exists():
            return False
        extra_dict = json.loads(file_path.read_text(encoding="utf-8"))
        for k in self._metadata.keys():
            if k not in extra_dict:
                continue
            extra_cols = extra_dict[k]
            columns = self._metadata[k]["columns"]
            for fk in columns.keys():
                if fk in extra_cols:
                    columns[fk]["sfdc_description"] = extra_cols[fk]
        return True

    def _extract_metadata(self) -> bool:
        dataset = self.bq_client.get_dataset(self.dataset_name)
        tables = []
        tables_light = list(self.bq_client.list_tables(dataset))
        tables_names = [table.table_id for table in tables_light]
        tables_names_lower = [table.lower() for table in tables_names]

        table_metadatas = {}

        results = self.sfdc_connection.restful(f"sobjects")
        if not results or "sobjects" not in results:
            raise Exception(f"Invalid response from Salesforce: {results}")
        sobjects = results["sobjects"]
        for sobject in sobjects:
            singular = sobject["name"].lower()
            plural = sobject["labelPlural"].lower()
            if singular in tables_names_lower:
                index = tables_names_lower.index(singular)
            elif plural in tables_names_lower:
                index = tables_names_lower.index(plural)
            else:
                continue
            table_name = tables_names[index]
            table = self.bq_client.get_table(
                f"{dataset.project}.{dataset.dataset_id}.{table_name}")
            tables.append(table)

            results = self.sfdc_connection.restful(
                f"sobjects/{sobject['name']}/describe")
            if not results or "fields" not in results:
                raise Exception(
                    f"Invalid response from Salesforce: {results}")

            table_metadata = {}
            table_metadata["salesforce_name"] = results["name"]
            table_metadata["salesforce_label"] = results["label"]
            table_metadata["important_notes_and_rules"] = ""
            table_metadata["salesforce_fields"] = results["fields"]
            table_metadata["bq_table"] = table
            table_metadatas[table_name] = table_metadata

        for _, table_metadata in table_metadatas.items():
            table = table_metadata["bq_table"]
            schema = [f.to_api_repr() for f in table.schema]
            sfdc_fields = table_metadata["salesforce_fields"]
            sfdc_field_names = [f["name"] for f in sfdc_fields]
            table_metadata["columns"] = {}
            for index, f in enumerate(schema):
                bq_field_name = f["name"]
                field_complex_description = ""
                possible_values = []
                reference = {}
                if bq_field_name.endswith("_Type"):
                    # Handling polymorphic type description
                    reference_field_name = bq_field_name[:-len(
                        "_Type")]
                    id_reference_filed_name = f"{reference_field_name}Id"
                    field_full_description = (
                        "Type of object "
                        f"`{id_reference_filed_name}` column refers to.")
                    sfdc_label = f"Type of {reference_field_name}"
                else:
                    if bq_field_name not in sfdc_field_names:
                        continue
                    sfdc_field_index = sfdc_field_names.index(
                        bq_field_name)
                    sfdc_field = sfdc_fields[sfdc_field_index]
                    ref_to = sfdc_field.get("referenceTo", [])
                    if len(ref_to) > 0:
                        reference["refers_to"] = ref_to
                        if len(ref_to) > 1:
                            if sfdc_field["relationshipName"]:
                                type_field = (sfdc_field["relationshipName"] +
                                              "_Type")
                                field_complex_description = (
                                    "Id of an object of one of types: [" +
                                    ",".join(ref_to) +
                                    "]. Object type is stored in " +
                                    f"`{type_field}` column.")
                                reference["reference_type_column"] = type_field
                        else:
                            ref_to_name = ref_to[0]
                            if ref_to_name == table_metadata["salesforce_name"]:
                                field_complex_description = (
                                    f"Id of another {ref_to_name}.")
                            else:
                                field_complex_description = (
                                    f"Id of {ref_to_name}."
                                )
                    if "picklistValues" in sfdc_field and len(sfdc_field["picklistValues"]) > 0:
                        for v in sfdc_field["picklistValues"]:
                            possible_values.append({
                                "value": v['value'],
                                "value_label": v['label'] or v['value']
                            })
                    if sfdc_field["name"] in _system_fields_description_formats:
                        field_name_long = _system_fields_description_formats[
                            sfdc_field["name"]] % table_metadata["salesforce_name"]
                    else:
                        field_name_long = (
                            sfdc_field["inlineHelpText"] or sfdc_field["label"])
                    sfdc_label = sfdc_field["label"]
                    field_full_description = f"{sfdc_field['name']} ({field_name_long})"
                if field_complex_description:
                    field_full_description += f"\n{field_complex_description}"
                nullable = f.get("nillable", True)
                field_metadata = {}
                field_metadata = {
                    "field_name": bq_field_name,
                    "field_type": f["type"],
                    "field_label": sfdc_label,
                    "sfdc_description": field_full_description,
                    "is_nullable": nullable,
                }
                if len(reference) > 0:
                    field_metadata["reference"] = reference
                if len(possible_values) > 0:
                    field_metadata["possible_values"] = possible_values

                table_metadata["columns"][bq_field_name] = field_metadata
            table_metadata.pop("salesforce_fields")
            table_metadata.pop("bq_table")
        self._metadata = table_metadatas
        return True
