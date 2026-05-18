"""Databricks SDk related helper functions."""

import os
import logging
from pydantic import BaseModel
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service import catalog
from databricks.sdk.service.sql import (
    Format,
    ExecuteStatementRequestOnWaitTimeout,
    Disposition,
    StatementState,
)
from google.cloud import storage
from google.api_core import exceptions as gcs_errors
from .config import databricks_vars, gcs_vars
from .utilities import databricksify_inst_name, SchemaType
from typing import List, Any, Dict, Optional
import requests
import hashlib
import json
import gzip
from cachetools import TTLCache
import threading
import re

# Setting up logger
LOGGER = logging.getLogger(__name__)


# List of data medallion levels
MEDALLION_LEVELS = ["silver", "gold", "bronze"]

# The name of the deployed pipeline in Databricks. Must match directly.
PDP_INFERENCE_JOB_NAME = "edvise_github_sourced_pdp_inference_pipeline"
# GCS validated/ → institution bronze_volume/gcs_uploads (edvise bundle job name).
VALIDATED_BRONZE_SYNC_JOB_NAME = "edvise_validated_gcs_to_bronze_sync"
# Optional: numeric Databricks job id. If unset, the job is resolved by name (must be unique).
DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV = "DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID"

# Must match edvise bundle job parameters (github_validated_bronze_sync.yml).
BRONZE_SYNC_GCS_SOURCE_PREFIX = "validated/"
BRONZE_SYNC_BRONZE_SUBDIR = "gcs_uploads"
BRONZE_SYNC_MAX_OBJECTS = "1000"
BRONZE_SYNC_REQUIRE_AT_LEAST_ONE_FILE = "true"
BRONZE_SYNC_STRICT_MODE = "auto"


def _create_databricks_workspace_client(operation: str) -> WorkspaceClient:
    """
    Create a Databricks WorkspaceClient using configured host and GCP service account.

    Args:
        operation: Label for error messages (e.g. ``run_validated_gcs_to_bronze_sync``).

    Returns:
        Initialized workspace client.

    Raises:
        ValueError: If client creation fails.
    """
    try:
        return WorkspaceClient(
            host=databricks_vars["DATABRICKS_HOST_URL"],
            google_service_account=gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
        )
    except (OSError, DatabricksError) as exc:
        LOGGER.exception(
            "Failed to create Databricks WorkspaceClient for %s: host=%s service_account=%s",
            operation,
            databricks_vars["DATABRICKS_HOST_URL"],
            gcs_vars["GCP_SERVICE_ACCOUNT_EMAIL"],
        )
        raise ValueError(f"{operation}(): Workspace client failed: {exc}") from exc


def _run_databricks_job_now(
    workspace: WorkspaceClient,
    job_id: int,
    job_parameters: dict[str, str],
    operation: str,
) -> int:
    """
    Start a Databricks job run and return the run id.

    Raises:
        ValueError: If the Jobs API does not return a run id.
    """
    try:
        run_job: Any = workspace.jobs.run_now(job_id, job_parameters=job_parameters)
    except DatabricksError as exc:
        LOGGER.exception(
            "Databricks job run failed for %s (job_id=%s).", operation, job_id
        )
        raise ValueError(f"{operation}(): Job could not be run: {exc}") from exc

    if not run_job.response or run_job.response.run_id is None:
        raise ValueError(f"{operation}(): No run_id returned.")

    return int(run_job.response.run_id)


def _resolve_validated_bronze_sync_job_id(w: WorkspaceClient) -> int:
    """
    Return the job id for the GCS→bronze sync job.

    Prefer ``DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID`` when set (stable across renames).
    Otherwise resolve by ``VALIDATED_BRONZE_SYNC_JOB_NAME``; multiple matches is an error.
    """
    raw = (os.environ.get(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV) or "").strip()
    if raw:
        if not raw.isdigit():
            raise ValueError(
                f"{DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV} must be a positive integer "
                f"(Databricks job id) if set; got {raw!r}."
            )
        job_id = int(raw)
        if job_id <= 0:
            raise ValueError(
                f"{DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV} must be positive; got {job_id}."
            )
        LOGGER.info(
            "Bronze sync job id from %s=%s",
            DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV,
            job_id,
        )
        return job_id

    jobs = list(w.jobs.list(name=VALIDATED_BRONZE_SYNC_JOB_NAME))
    if len(jobs) == 0:
        raise ValueError(
            f"Job named {VALIDATED_BRONZE_SYNC_JOB_NAME!r} not found. "
            f"Deploy the bundle job or set {DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV} "
            "to the numeric job id from the Databricks UI / API."
        )
    if len(jobs) > 1:
        ids = [j.job_id for j in jobs if j.job_id is not None]
        raise ValueError(
            f"Multiple ({len(jobs)}) jobs named {VALIDATED_BRONZE_SYNC_JOB_NAME!r}; "
            f"set {DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV} to the correct id. Found job_ids={ids}."
        )
    job = jobs[0]
    if job.job_id is None:
        raise ValueError(
            f"Job named {VALIDATED_BRONZE_SYNC_JOB_NAME!r} has no job_id in list response."
        )
    job_id = job.job_id
    LOGGER.info(
        "Resolved bronze sync job by name %r: job_id=%s",
        VALIDATED_BRONZE_SYNC_JOB_NAME,
        job_id,
    )
    return job_id


class DatabricksInferenceRunRequest(BaseModel):
    """Databricks parameters for an inference run."""

    inst_name: str
    # Note that the following should be the filepath.
    filepath_to_type: dict[str, list[SchemaType]]
    model_name: str
    # The email where notifications will get sent.
    email: str
    gcp_external_bucket_name: str


class DatabricksInferenceRunResponse(BaseModel):
    """Databricks parameters for an inference run."""

    job_run_id: int


class DatabricksBronzeSyncRequest(BaseModel):
    """Parameters to copy validated GCS objects into the institution bronze volume."""

    inst_name: str
    gcp_bucket_name: str
    # Full object paths in the bucket, e.g. ["validated/file.csv"].
    validated_blob_paths: list[str]


class DatabricksBronzeSyncResponse(BaseModel):
    """Result of triggering the bronze sync Databricks job."""

    job_run_id: int


def _build_validated_bronze_sync_job_parameters(
    req: DatabricksBronzeSyncRequest,
    databricks_institution_name: str,
) -> dict[str, str]:
    """Build job_parameters dict for the GCS→bronze sync Databricks job."""
    include_json = json.dumps(req.validated_blob_paths, separators=(",", ":"))
    return {
        "gcp_bucket_name": req.gcp_bucket_name,
        "databricks_institution_name": databricks_institution_name,
        "DB_workspace": databricks_vars["DATABRICKS_WORKSPACE"],
        "sync_run_id": "",
        "gcs_source_prefix": BRONZE_SYNC_GCS_SOURCE_PREFIX,
        "bronze_subdir": BRONZE_SYNC_BRONZE_SUBDIR,
        "max_objects": BRONZE_SYNC_MAX_OBJECTS,
        "require_at_least_one_file": BRONZE_SYNC_REQUIRE_AT_LEAST_ONE_FILE,
        "strict_mode": BRONZE_SYNC_STRICT_MODE,
        "include_blob_paths_json": include_json,
    }


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
L1_RESP_CACHE: Any = TTLCache(maxsize=128, ttl=L1_RESP_CACHE_TTL)
L1_VER_CACHE: Any = TTLCache(maxsize=256, ttl=L1_VER_CACHE_TTL)
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

    def run_validated_gcs_to_bronze_sync(
        self, req: DatabricksBronzeSyncRequest
    ) -> DatabricksBronzeSyncResponse:
        """
        Trigger the job that copies validated/ objects from GCS into bronze_volume/gcs_uploads.

        Args:
            req: Institution name, bucket, and full GCS object paths under validated/.

        Returns:
            Response containing the Databricks job run id (run started, not completed).

        Raises:
            ValueError: If paths are empty, configuration is invalid, or the job cannot start.
        """
        operation = "run_validated_gcs_to_bronze_sync"
        if not req.validated_blob_paths:
            raise ValueError(f"{operation}: validated_blob_paths must be non-empty.")

        LOGGER.info(
            "Triggering GCS→bronze sync for institution: %s (%s objects)",
            req.inst_name,
            len(req.validated_blob_paths),
        )

        workspace = _create_databricks_workspace_client(operation)
        try:
            job_id = _resolve_validated_bronze_sync_job_id(workspace)
        except ValueError as exc:
            LOGGER.exception("Job resolution failed for GCS→bronze sync.")
            raise ValueError(f"{operation}(): Failed to resolve job: {exc}") from exc

        db_inst_name = databricksify_inst_name(req.inst_name)
        job_parameters = _build_validated_bronze_sync_job_parameters(req, db_inst_name)
        run_id = _run_databricks_job_now(workspace, job_id, job_parameters, operation)
        LOGGER.info("GCS→bronze sync job started. Run ID: %s", run_id)
        return DatabricksBronzeSyncResponse(job_run_id=run_id)

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
    ) -> Any:
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
            cols = [c.name for c in ver_resp.manifest.schema.columns]  # type: ignore
            idx = {n: i for i, n in enumerate(cols)}
            rows = ver_resp.result.data_array or []  # type: ignore
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
        cols: List[str] = []  # type: ignore
        for c in resp.manifest.schema.columns:
            if c.name is None:
                raise ValueError("Encountered a column without a name.")
            cols.append(c.name)

        records: Any = []

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

    def fetch_model_version(
        self, catalog_name: str, inst_name: str, model_name: str
    ) -> Any:
        schema = databricksify_inst_name(inst_name)
        model_name_path = f"{catalog_name}.{schema}_gold.{model_name}"

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

        model_versions: Any = list(
            w.model_versions.list(
                full_name=model_name_path,
            )
        )

        if not model_versions:
            raise ValueError(f"No versions found for model: {model_name_path}")

        latest_version = max(model_versions, key=lambda v: int(v.version))

        return latest_version

    def delete_model(self, catalog_name: str, inst_name: str, model_name: str) -> None:
        schema = databricksify_inst_name(inst_name)
        model_name_path = f"{catalog_name}.{schema}_gold.{model_name}"

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

        try:
            w.registered_models.delete(full_name=model_name_path)
            LOGGER.info("Deleted registration model: %s", model_name_path)
        except Exception:
            LOGGER.exception("Failed to delete registered model: %s", model_name_path)
            raise

    def get_key_for_file(
        self, mapping: Dict[str, Any], file_name: str
    ) -> Optional[str]:
        """
        Case-insensitive match of file_name against mapping values.
        Values may be:
        - str literal (e.g., "student.csv") → allow optional base suffixes before the ext.
        - str regex (e.g., r"^course_.*\\.csv$") → re.IGNORECASE fullmatch.
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
