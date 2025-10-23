"""Databricks SDk related helper functions."""

import os
import logging
from pydantic import BaseModel
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import catalog
from databricks.sdk.service.sql import (
    Format,
    ExecuteStatementRequestOnWaitTimeout,
    Disposition,
    StatementState,
)
from google.cloud import storage
from google.api_core import exceptions as gcs_errors
from .validation_extension import generate_extension_schema
from .config import databricks_vars, gcs_vars
from .utilities import databricksify_inst_name, SchemaType
from typing import List, Any, Dict, IO, cast, Optional
from fastapi import HTTPException
import requests
import hashlib
import json
import gzip
from cachetools import TTLCache
import threading

try:
    import tomllib as _toml  # Py 3.11+
except ModuleNotFoundError:
    import tomli as _toml  # Py ≤ 3.10
import pandas as pd
import re

# Setting up logger
LOGGER = logging.getLogger(__name__)


# List of data medallion levels
MEDALLION_LEVELS = ["silver", "gold", "bronze"]

# The name of the deployed pipeline in Databricks. Must match directly.
PDP_INFERENCE_JOB_NAME = "edvise_github_sourced_pdp_inference_pipeline"


class DatabricksInferenceRunRequest(BaseModel):
    """Databricks parameters for an inference run."""

    inst_name: str
    # Note that the following should be the filepath.
    filepath_to_type: dict[str, list[SchemaType]]
    model_name: str
    model_type: str
    # The email where notifications will get sent.
    email: str
    gcp_external_bucket_name: str


class DatabricksInferenceRunResponse(BaseModel):
    """Databricks parameters for an inference run."""

    job_run_id: int


def get_filepath_of_filetype(
    file_dict: dict[str, list[SchemaType]], file_type: SchemaType
) -> str:
    """Helper functions to get a file of a given file_type.
    For both, we will return the first file that matches the schema."""
    for k, v in file_dict.items():
        if file_type in v:
            return k
    return ""


def check_types(dict_values: list[list[SchemaType]], file_type: SchemaType) -> bool:
    """Check the file type is in the dict dictionary."""
    for elem in dict_values:
        if file_type in elem:
            return True
    return False


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


L1_RESP_CACHE_TTL = int("600")  # seconds
L1_VER_CACHE_TTL = int("3600")  # seconds
L1_RESP_CACHE = TTLCache(maxsize=128, ttl=L1_RESP_CACHE_TTL)
L1_VER_CACHE = TTLCache(maxsize=256, ttl=L1_VER_CACHE_TTL)
_L1_LOCK = threading.RLock()


# Wrapping the usages in a class makes it easier to unit test via mocks.
class DatabricksControl(BaseModel):
    """Object to manage interfacing with GCS."""

    def setup_new_inst(self, inst_name: str) -> None:
        """Sets up Databricks resources for a new institution."""
        LOGGER.info("Setting up new institution.")
        try:
            w = WorkspaceClient(
                host=databricks_vars["DATABRICKS_HOST_URL"],
                google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
        except Exception as e:
            LOGGER.exception(
                "Failed to create Databricks WorkspaceClient with host: %s and service account: %s",
                databricks_vars["DATABRICKS_HOST_URL"],
                gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            raise ValueError(f"setup_new_inst(): Workspace client creation failed: {e}")

        db_inst_name = databricksify_inst_name(inst_name)
        cat_name = databricks_vars["CATALOG_NAME"]
        for medallion in MEDALLION_LEVELS:
            try:
                w.schemas.create(
                    name=f"{db_inst_name}_{medallion}", catalog_name=cat_name
                )
            except Exception as e:
                LOGGER.exception(
                    f"Failed to provision schemas in databricks for {db_inst_name}_{medallion}: {e}"
                )
                raise ValueError(
                    f"setup_new_inst(): Failed to provision schemas in databricks for {db_inst_name}_{medallion}: {e}"
                )
            LOGGER.info(
                f"Creating medallion level schemas for {db_inst_name} & {medallion}."
            )
        # Create a managed volume in the bronze schema for internal pipeline data.
        # update to include a managed volume for toml files
        try:
            created_volume_bronze = w.volumes.create(
                catalog_name=cat_name,
                schema_name=f"{db_inst_name}_bronze",
                name="bronze_volume",
                volume_type=catalog.VolumeType.MANAGED,
            )
            LOGGER.info(
                f"Created volume 'bronze_volume' in schema '{db_inst_name}_bronze'."
            )

            created_volume_silver = w.volumes.create(
                catalog_name=cat_name,
                schema_name=f"{db_inst_name}_silver",
                name="silver_volume",
                volume_type=catalog.VolumeType.MANAGED,
            )
            LOGGER.info(
                f"Created volume 'silver_volume' in schema '{db_inst_name}_silver'."
            )

            created_volume_gold = w.volumes.create(
                catalog_name=cat_name,
                schema_name=f"{db_inst_name}_gold",
                name="gold_volume",
                volume_type=catalog.VolumeType.MANAGED,
            )
            LOGGER.info(
                f"Created volume 'gold_volume' in schema '{db_inst_name}_gold'."
            )

        except Exception as e:
            LOGGER.exception("Failed to create one or more volumes.")
            raise ValueError(f"setup_new_inst(): Volume creation failed: {e}")

        if (
            created_volume_bronze is None
            or created_volume_silver is None
            or created_volume_gold is None
        ):
            raise ValueError("setup_new_inst() volume creation failed.")
        # Create directory on the volume
        os.makedirs(
            f"/Volumes/{cat_name}/{db_inst_name}_gold/gold_volume/configuration_files/",
            exist_ok=True,
        )
        # Create directory on the volume
        os.makedirs(
            f"/Volumes/{cat_name}/{db_inst_name}_bronze/bronze_volume/raw_files/",
            exist_ok=True,
        )

    # Note that for each unique PIPELINE, we'll need a new function, this is by nature of how unique pipelines
    # may have unique parameters and would have a unique name (i.e. the name field specified in w.jobs.list()). But any run of a given pipeline (even across institutions) can use the same function.
    # E.g. there is one PDP inference pipeline, so one PDP inference function here.

    def run_pdp_inference(
        self, req: DatabricksInferenceRunRequest
    ) -> DatabricksInferenceRunResponse:
        """Triggers PDP inference Databricks run."""
        LOGGER.info(f"Running PDP inference for institution: {req.inst_name}")
        if (
            not req.filepath_to_type
            or not check_types(list(req.filepath_to_type.values()), SchemaType.COURSE)
            or not check_types(list(req.filepath_to_type.values()), SchemaType.STUDENT)
        ):
            LOGGER.error("Missing required file types: COURSE and STUDENT")
            raise ValueError(
                "run_pdp_inference() requires COURSE and STUDENT type files to run."
            )
        try:
            w = WorkspaceClient(
                host=databricks_vars["DATABRICKS_HOST_URL"],
                google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            LOGGER.info("Successfully created Databricks WorkspaceClient.")
        except Exception as e:
            LOGGER.exception(
                "Failed to create Databricks WorkspaceClient with host: %s and service account: %s",
                databricks_vars["DATABRICKS_HOST_URL"],
                gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            raise ValueError(
                f"run_pdp_inference(): Workspace client initialization failed: {e}"
            )

        db_inst_name = databricksify_inst_name(req.inst_name)
        pipeline_type = PDP_INFERENCE_JOB_NAME

        try:
            job = next(w.jobs.list(name=pipeline_type), None)
            if not job or job.job_id is None:
                raise ValueError(
                    f"run_pdp_inference(): Job '{pipeline_type}' was not found or has no job_id for '{gcs_vars['GCP_SERVICE_ACCOUNT_EMAIL']}' and '{databricks_vars['DATABRICKS_HOST_URL']}'."
                )
            job_id = job.job_id
            LOGGER.info(f"Resolved job ID for '{pipeline_type}': {job_id}")
        except Exception as e:
            LOGGER.exception(f"Job lookup failed for '{pipeline_type}'.")
            raise ValueError(f"run_pdp_inference(): Failed to find job: {e}")

        try:
            run_job: Any = w.jobs.run_now(
                job_id,
                job_parameters={
                    "cohort_file_name": get_filepath_of_filetype(
                        req.filepath_to_type, SchemaType.STUDENT
                    ),
                    "course_file_name": get_filepath_of_filetype(
                        req.filepath_to_type, SchemaType.COURSE
                    ),
                    "databricks_institution_name": db_inst_name,
                    "DB_workspace": databricks_vars[
                        "DATABRICKS_WORKSPACE"
                    ],  # is this value the same PER environ? dev/staging/prod
                    "gcp_bucket_name": req.gcp_external_bucket_name,
                    "model_name": req.model_name,
                    "model_type": req.model_type,
                    "notification_email": req.email,
                },
            )
            LOGGER.info(
                f"Successfully triggered job run. Run ID: {run_job.response.run_id}"
            )
        except Exception as e:
            LOGGER.exception("Failed to run the PDP inference job.")
            raise ValueError(f"run_pdp_inference(): Job could not be run: {e}")

        if not run_job.response or run_job.response.run_id is None:
            raise ValueError("run_pdp_inference(): Job did not return a valid run_id.")

        run_id = run_job.response.run_id
        LOGGER.info(f"Successfully triggered job run. Run ID: {run_id}")

        return DatabricksInferenceRunResponse(job_run_id=run_id)

    def delete_inst(self, inst_name: str) -> None:
        """Cleanup tasks required on the Databricks side to delete an institution."""
        db_inst_name = databricksify_inst_name(inst_name)
        cat_name = databricks_vars["CATALOG_NAME"]

        LOGGER.info(f"Starting deletion of Databricks resources for: {db_inst_name}")

        try:
            w = WorkspaceClient(
                host=databricks_vars["DATABRICKS_HOST_URL"],
                # This should still be cloud run, since it's cloud run triggering the databricks
                # this account needs to exist on Databricks as well and needs to have permissions.
                google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
        except Exception as e:
            LOGGER.exception(
                "Failed to create Databricks WorkspaceClient with host: %s and service account: %s",
                databricks_vars["DATABRICKS_HOST_URL"],
                gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            raise ValueError(
                f"delete_inst(): Workspace client initialization failed: {e}"
            )

        # Delete managed volumes
        for medallion in MEDALLION_LEVELS:
            volume_name = f"{cat_name}.{db_inst_name}_{medallion}.{medallion}_volume"
            try:
                w.volumes.delete(name=volume_name)
                LOGGER.info(f"Deleted volume: {volume_name}")
            except Exception as e:
                LOGGER.exception(
                    f"Volume not found or could not be deleted: {volume_name} — {e}"
                )

        # TODO implement model deletion

        # Delete tables and schemas for each medallion level.
        for medallion in MEDALLION_LEVELS:
            try:
                all_tables = [
                    table.name
                    for table in w.tables.list(
                        catalog_name=cat_name,
                        schema_name=f"{db_inst_name}_{medallion}",
                    )
                ]
                for table in all_tables:
                    w.tables.delete(
                        full_name=f"{cat_name}.{db_inst_name}_{medallion}.{table}"
                    )
                w.schemas.delete(full_name=f"{cat_name}.{db_inst_name}_{medallion}")
            except Exception as e:
                LOGGER.exception(
                    f"Tables or schemas could not be deleted for {medallion}  — {e}"
                )

    def fetch_table_data(
        self,
        catalog_name: str,
        inst_name: str,
        table_name: str,
        warehouse_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Execute SELECT * via Databricks SQL Statement Execution API using EXTERNAL_LINKS.
        Blocks server-side for up to 30s; if not SUCCEEDED, raises. Downloads presigned
        URLs in-memory and returns rows as List[Dict[str, Any]].
        """
        w = WorkspaceClient(
            host=databricks_vars["DATABRICKS_HOST_URL"],
            google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
        )

        bucket_name = databricks_vars["GCP_CACHE_BUCKET"]
        schema = databricksify_inst_name(inst_name)
        table_fqn = f"`{catalog_name}`.`{schema}_silver`.`{table_name}`"
        sql = f"SELECT * FROM {table_fqn}"

        ver_cache_key = f"ver:{table_fqn}"
        with _L1_LOCK:
            table_version = L1_VER_CACHE.get(ver_cache_key)

        if table_version is None:
            ver_sql = f"DESCRIBE HISTORY {table_fqn} LIMIT 1"
            ver_resp = w.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=ver_sql,
                disposition=Disposition.INLINE,
                format=Format.JSON_ARRAY,
                wait_timeout="30s",
                on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
            )

            if not ver_resp.status or ver_resp.status.state != StatementState.SUCCEEDED:
                raise TimeoutError("DESCRIBE HISTORY did not finish within 30s")
            cols = [c.name for c in ver_resp.manifest.schema.columns]
            idx = {n: i for i, n in enumerate(cols)}
            rows = ver_resp.result.data_array or []
            if not rows or "version" not in idx:
                raise ValueError("DESCRIBE HISTORY returned no version")
            table_version = str(rows[0][idx["version"]])

            with _L1_LOCK:
                L1_VER_CACHE[ver_cache_key] = table_version

        sql_h = _sha256_json({"sql": sql})
        l1_key = f"v1:{warehouse_id}:{catalog_name}.{schema}.{table_name}:{sql_h}:{table_version}"

        with _L1_LOCK:
            cached_records = L1_RESP_CACHE.get(l1_key)
        if cached_records is not None:
            return cached_records

        try:
            object_name = f"{warehouse_id}/{catalog_name}.{schema}.{table_name}/{sql_h}/{table_version}.json.gz"
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            try:
                blob.reload()  # HEAD for metadata (ETag, etc.)
                body = blob.download_as_bytes(raw_download=False)
                data = json.loads(body)
                if isinstance(data, list):
                    with _L1_LOCK:
                        L1_RESP_CACHE[l1_key] = data
                    return data  # cache hit
            except gcs_errors.NotFound:
                pass
        except Exception:
            pass

        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=sql,
            disposition=Disposition.EXTERNAL_LINKS,
            format=Format.JSON_ARRAY,
            wait_timeout="30s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
        )

        stmt_id = resp.statement_id
        if stmt_id is None:
            raise ValueError("Databricks returned a null statement_id")

        # No client-side polling; require SUCCEEDED within 30s.
        if (resp.status is None) or (resp.status.state != StatementState.SUCCEEDED):
            state = resp.status.state if resp.status else "UNKNOWN"
            msg = (
                resp.status.error.message
                if (resp.status and resp.status.error)
                else "Query not finished within wait_timeout"
            )
            raise TimeoutError(
                f"Statement {stmt_id} not finished (state={state}): {msg}"
            )

        # Columns (ensure List[str] for type-checkers)
        if not (
            resp.manifest and resp.manifest.schema and resp.manifest.schema.columns
        ):
            raise ValueError("Schema/columns missing (EXTERNAL_LINKS).")
        cols: List[str] = []
        for c in resp.manifest.schema.columns:
            if c.name is None:
                raise ValueError("Encountered a column without a name.")
            cols.append(c.name)

        records: List[Dict[str, Any]] = []

        # Helper: consume one chunk-like object (first result or subsequent chunk)
        def _consume_chunk(chunk_obj: Any) -> int | None:
            links = getattr(chunk_obj, "external_links", None) or []
            for link_obj in links:
                url = getattr(link_obj, "external_link", None)
                if url is None and isinstance(link_obj, dict):
                    url = link_obj.get("external_link")
                if not url:
                    continue
                # IMPORTANT: do not send Databricks auth header to presigned URLs.
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list):
                    raise ValueError(
                        "Unexpected external link payload (expected JSON array)."
                    )
                for row in rows:
                    if not isinstance(row, list):
                        raise ValueError("Unexpected row shape (expected list).")
                    records.append(dict(zip(cols, row)))
            return getattr(chunk_obj, "next_chunk_index", None)

        # First batch is in resp.result
        if not resp.result:
            return records
        next_idx = _consume_chunk(resp.result)

        # Remaining batches by chunk index
        while next_idx is not None:
            chunk = w.statement_execution.get_statement_result_chunk_n(
                statement_id=stmt_id,
                chunk_index=next_idx,
            )
            next_idx = _consume_chunk(chunk)

        with _L1_LOCK:
            if records:
                L1_RESP_CACHE[l1_key] = records

        if bucket_name and object_name and records:
            try:
                raw = json.dumps(
                    records, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                gz = gzip.compress(raw, compresslevel=6)
                storage_client = storage.Client()
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(object_name)
                blob.content_encoding = "gzip"
                try:
                    blob.upload_from_string(
                        gz,
                        content_type="application/json",
                        if_generation_match=0,  # write-once; 412 if someone beat us
                    )
                except gcs_errors.PreconditionFailed:
                    # Another writer won; fine—object exists now.
                    pass
            except Exception:
                # Cache write failures must not impact the request
                pass
        return records

    def fetch_model_version(self, model_name: str):
        try:
            w = WorkspaceClient(
                host=databricks_vars["DATABRICKS_HOST_URL"],
                google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
        except Exception as e:
            LOGGER.exception(
                "Failed to create Databricks WorkspaceClient with host: %s and service account: %s",
                databricks_vars["DATABRICKS_HOST_URL"],
                gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            raise ValueError(f"setup_new_inst(): Workspace client creation failed: {e}")

        model_info = w.model_versions.list(
            full_name=model_name,
        )

        return model_info

    def get_key_for_file(
        self, mapping: Dict[str, Any], file_name: str
    ) -> Optional[str]:
        """
        Case-insensitive match of file_name against mapping values.
        Values may be:
        - str literal (e.g., "student.csv") → allow optional base suffixes before the ext.
        - str regex (e.g., r"^course_.*\.csv$") → re.IGNORECASE fullmatch.
        - compiled regex (re.Pattern) → fullmatch, adding IGNORECASE if missing.
        - list of any of the above.
        """
        # normalize filename (handles windows paths + stray whitespace)
        name = os.path.basename(file_name.replace("\\", "/")).strip()

        REGEX_META = re.compile(r"[()\[\]\{\}\|\?\+\*\\]")

        def looks_like_regex(s: str) -> bool:
            s = s.strip()
            return (
                s.startswith("^") or s.endswith("$") or REGEX_META.search(s) is not None
            )

        def matches_one(pat: Any) -> bool:
            # compiled regex
            if isinstance(pat, re.Pattern):
                # ensure case-insensitive
                flags = pat.flags | re.IGNORECASE
                return re.fullmatch(re.compile(pat.pattern, flags), name) is not None

            # string literal / regex
            if isinstance(pat, str):
                p = pat.strip()

                # exact literal (case-insensitive)
                if name.casefold() == p.casefold():
                    return True

                if looks_like_regex(p):
                    try:
                        return re.fullmatch(p, name, flags=re.IGNORECASE) is not None
                    except re.error:
                        return False

                # literal with suffix tolerance
                p_base, p_ext = os.path.splitext(p)
                if p_ext:
                    # ^base(?:[._-].+)?ext$
                    rx = re.compile(
                        rf"^{re.escape(p_base)}(?:[._-].+)?{re.escape(p_ext)}$",
                        re.IGNORECASE,
                    )
                else:
                    # ^literal(?:[._-].+)?(?:\..+)?$
                    rx = re.compile(
                        rf"^{re.escape(p)}(?:[._-].+)?(?:\..+)?$",
                        re.IGNORECASE,
                    )
                return rx.fullmatch(name) is not None

            # unsupported type
            return False

        for key, val in mapping.items():
            items = val if isinstance(val, list) else [val]
            for pat in items:
                if matches_one(pat):
                    return key

        return None

    def create_custom_schema_extension(
        self,
        bucket_name: str,
        inst_query: Any,
        file_name: str,
        base_schema: Dict[str, Any],  # pass base schema dict in
        extension_schema: Optional[dict] = None,  # existing extension or None
    ) -> Any:
        if (
            os.getenv("SST_SKIP_EXT_GEN") == "1"
        ):  # skip using workspace client for tests
            LOGGER.info("SST_SKIP_EXT_GEN=1; skipping Databricks extension generation.")
            return None

        # 1) Databricks client
        try:
            w = WorkspaceClient(
                host=databricks_vars["DATABRICKS_HOST_URL"],
                google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
            )
            LOGGER.info("Successfully created Databricks WorkspaceClient.")
        except Exception as e:
            LOGGER.exception("WorkspaceClient init failed")
            raise ValueError(f"Workspace client initialization failed: {e}")

        # 2) Fetch & parse config.toml to get validation_mapping
        try:
            inst_name = inst_query[0][0].name
            inst_id_raw = inst_query[0][0].id
            inst_id = str(inst_id_raw)  # be robust if id is not a string
            config_volume_path = (
                f"/Volumes/staging_sst_01/"
                f"{databricksify_inst_name(inst_name)}_bronze/bronze_volume/config.toml"
            )
            LOGGER.info("Attempting to download from %s", config_volume_path)
            response = w.files.download(config_volume_path)
            stream = cast(IO[bytes], response.contents)
            file_bytes = stream.read()
            LOGGER.info("Download successful, received %d bytes", len(file_bytes))
        except Exception as e:
            LOGGER.exception("Failed to fetch config.toml")
            raise HTTPException(500, detail=f"Failed to fetch config: {e}")

        try:
            cfg = _toml.loads(file_bytes.decode("utf-8"))
            mapping = cfg["webapp"]["validation_mapping"]
        except KeyError:
            raise HTTPException(
                404, detail="Missing [webapp].validation_mapping in config.toml"
            )
        except Exception as e:
            LOGGER.exception("Invalid TOML")
            raise HTTPException(400, detail=f"Invalid TOML in {file_name}: {e}")

        if not isinstance(mapping, dict):
            raise HTTPException(
                400, detail="validation_mapping must be a TOML table (dictionary)"
            )

        key = self.get_key_for_file(mapping, file_name)  # e.g., "student"
        if key is None:
            raise HTTPException(
                404, detail=f"{file_name} not found in {inst_name} validation_mapping"
            )

        key_lc = key.lower()

        # 4) If this model already exists in the provided extension for this institution, skip
        if extension_schema is not None:
            if not isinstance(extension_schema, dict):
                raise HTTPException(
                    400, detail="extension_schema must be a dict if provided"
                )

            inst_block = extension_schema.get("institutions", {}).get(inst_id, {})
            data_models = inst_block.get("data_models", {})
            existing_keys_lc = {str(k).lower() for k in data_models.keys()}

            if key_lc in existing_keys_lc:
                LOGGER.info(
                    "Model '%s' already present for institution '%s' — skipping (return None).",
                    key,
                    inst_id,
                )
                return None  # <-- sentinel: do not write

        # 5) Read the unvalidated CSV from GCS
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(f"unvalidated/{file_name}")
            with blob.open("r") as fh:
                df = pd.read_csv(fh)
        except Exception as e:
            LOGGER.exception("Failed to read %s from GCS", file_name)
            raise HTTPException(500, detail=f"Failed to read {file_name} from GCS: {e}")

        updated_extension = generate_extension_schema(
            df=df,
            models=key,  # exactly one model
            institution_id=inst_id,
            base_schema=base_schema,  # reference only, not mutated
            existing_extension=extension_schema,  # may be None
        )

        return updated_extension
