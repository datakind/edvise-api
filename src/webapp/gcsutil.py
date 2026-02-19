"""Cloud storage related helper functions."""

import datetime
import io
import logging
from typing import Any, Dict, List, Optional, IO

import pandas as pd
from pydantic import BaseModel
from google.cloud import storage
import google.auth
from google.auth.transport import requests

from .config import gcs_vars, databricks_vars
from .validation import validate_file_reader, HardValidationError

# Set the logging
logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

SIGNED_URL_EXPIRY_MIN = 30


def rename_file(
    bucket_name: str,
    file_name: str,
    new_file_name: str,
) -> None:
    """Moves a blob from one bucket to another with a new name."""
    storage_client = storage.Client()
    source_bucket = storage_client.bucket(bucket_name)
    source_blob = source_bucket.blob(file_name)

    # Optional: set a generation-match precondition to avoid potential race conditions
    # and data corruptions. The request is aborted if the object's
    # generation number does not match your precondition. For a destination
    # object that does not yet exist, set the if_generation_match precondition to 0.
    # If the destination object already exists in your bucket, set instead a
    # generation-match precondition using its generation number.
    # There is also an `if_source_generation_match` parameter, which is not used in this example.
    destination_generation_match_precondition = 0

    source_bucket.copy_blob(
        source_blob,
        new_file_name,
        if_generation_match=destination_generation_match_precondition,
    )
    source_bucket.delete_blob(file_name)


# Wrapping the usages in a class makes it easier to unit test via mocks.
class StorageControl(BaseModel):
    """Object to manage interfacing with GCS."""

    _credentials = None
    _project_id = None

    def credentials(self):
        """Retrieve GCS creds."""
        if self._credentials is None or self._project_id is None:
            self._credentials, self._project_id = google.auth.default()
        return self._credentials

    def generate_upload_signed_url(self, bucket_name: str, file_name: str) -> Any:
        """Generates a v4 signed URL for uploading a blob using HTTP PUT."""
        r = requests.Request()
        self.credentials().refresh(r)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")
        for prefix in ("unvalidated/", "validated/"):
            blob_name = prefix + file_name
            blob = bucket.blob(blob_name)
            if blob.exists():
                raise ValueError("File already exists.")
        # All files uploaded directly are considered unvalidated.
        blob_name = "unvalidated/" + file_name
        blob = bucket.blob(blob_name)

        service_account_email = ""
        if hasattr(self.credentials(), "service_account_email"):
            service_account_email = self.credentials().service_account_email
        url = blob.generate_signed_url(
            version="v4",
            service_account_email=service_account_email,
            access_token=self.credentials().token,
            # How long the url is usable for.
            expiration=datetime.timedelta(minutes=SIGNED_URL_EXPIRY_MIN),
            # Allow PUT requests using this URL.
            method="PUT",
            content_type="text/csv",
        )

        return url

    def generate_download_signed_url(self, bucket_name: str, blob_name: str) -> Any:
        """Generates a v4 signed URL for downloading a blob using HTTP GET."""
        r = requests.Request()
        self.credentials().refresh(r)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")
        blob = bucket.blob(blob_name)
        if not blob.exists():
            raise ValueError(blob_name + ": File not found.")
        service_account_email = ""
        if hasattr(self.credentials(), "service_account_email"):
            service_account_email = self.credentials().service_account_email
        url = blob.generate_signed_url(
            version="v4",
            service_account_email=service_account_email,
            access_token=self.credentials().token,
            # How long the url is usable for.
            expiration=datetime.timedelta(minutes=SIGNED_URL_EXPIRY_MIN),
            # Allow GET requests using this URL.
            method="GET",
        )
        return url

    def upload_unvalidated_csv_from_file(
        self, bucket_name: str, file_name: str, file_obj: IO[bytes]
    ) -> None:
        """Upload a CSV into unvalidated/ while enforcing no-overwrite semantics."""
        if not file_name or not file_name.strip():
            raise ValueError("file_name is required and must be non-empty.")
        if "/" in file_name:
            raise ValueError("file_name must not contain '/'.")

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")

        for prefix in ("unvalidated/", "validated/"):
            blob = bucket.blob(prefix + file_name)
            if blob.exists():
                raise ValueError("File already exists.")

        blob = bucket.blob("unvalidated/" + file_name)
        blob.upload_from_file(file_obj, content_type="text/csv")

    def delete_bucket(self, bucket_name: str) -> None:
        """Delete a given bucket."""
        storage_client = storage.Client()
        # Delete the GCS bucket.  Force=True handles non-empty buckets.
        print("[debugging_crystal]: in delete_bucket()1")

        bucket = storage_client.get_bucket(bucket_name)
        print("[debugging_crystal]: in delete_bucket()2:" + str(bucket))
        bucket.delete(force=True)
        print("[debugging_crystal]: in delete_bucket()3")

    def create_bucket(self, bucket_name: str) -> None:
        """
        Create a new bucket in the US region with the standard storage
        class.
        """
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        if bucket.exists():
            raise ValueError(bucket_name + " already exists. Creation failed.")
        # Update with URL?
        # fmt: off
        bucket.cors = [
            {
                "origin": ["*"],
                "responseHeader": ["*"],
                "method": ["GET", "OPTIONS", "PUT", "POST"],
                "maxAgeSeconds": 3600
            }
        ]
        # fmt: on
        # Apply TTL to unvalidated files. This may occur if an API caller uploads but doesn't call validate.
        # fmt: off
        bucket.lifecycle_rules = [
            {
            "action": {"type": "Delete"},
            "condition": {"age": 1, "matchesPrefix": ["unvalidated/"]}
            }
        ]
        # fmt: on
        bucket.storage_class = "STANDARD"
        # Grant object admin access to the specified service account.
        new_bucket = storage_client.create_bucket(
            bucket, location=gcs_vars["GCP_REGION"]
        )
        policy = new_bucket.get_iam_policy(requested_policy_version=3)
        policy.bindings.append(
            {
                "role": "roles/storage.objectAdmin",
                # The account triggering the job is not the same as the account reading the buckets content INSIDE the job. This is the account reading the buckets from within Databricks accounts.
                "members": {
                    "serviceAccount:"
                    + databricks_vars["DATABRICKS_SERVICE_ACCOUNT_EMAIL"]
                },
            }
        )
        new_bucket.set_iam_policy(policy)

    def list_blobs_in_folder(
        self, bucket_name: str, prefix: str, delimiter: Any = None
    ) -> list[str]:
        """Lists all the blobs in the bucket that begin with the prefix.

        This can be used to list all blobs in a "folder", e.g. "public/".

        The delimiter argument can be used to restrict the results to only the
        "files" in the given "folder". Without the delimiter, the entire tree under
        the prefix is returned. For example, given these blobs:

            a/1.txt
            a/b/2.txt

        If you specify prefix ='a/', without a delimiter, you'll get back:

            a/1.txt
            a/b/2.txt

        However, if you specify prefix='a/' and delimiter='/', you'll get back
        only the file directly under 'a/':

            a/1.txt

        As part of the response, you'll also get back a blobs.prefixes entity
        that lists the "subfolders" under `a/`:

            a/b/
        """
        storage_client = storage.Client()
        # Note: Client.list_blobs requires at least package version 1.17.0.
        blobs = storage_client.list_blobs(
            bucket_name, prefix=prefix, delimiter=delimiter
        )

        # Note: The call returns a response only when the iterator is consumed.
        res = []
        for blob in blobs:
            res.append(blob.name)

        if delimiter:
            for p in blobs.prefixes:
                res.append(p)
        return res

    def download_file(
        self, bucket_name: str, file_name: str, destination_file_name: str
    ) -> Any:
        """Downloads a blob from the bucket."""

        # The path to which the file should be downloaded
        # destination_file_name = "local/path/to/file"
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")

        # Construct a client side representation of a blob.
        # Note `Bucket.blob` differs from `Bucket.get_blob` as it doesn't retrieve
        # any content from Google Cloud Storage. As we don't need additional data,
        # using `Bucket.blob` is preferred here.
        blob = bucket.blob(file_name)
        if not blob.exists():
            raise ValueError(file_name + ": File not found.")
        blob.download_to_filename(destination_file_name)

    def move_file(self, bucket_name: str, prev_name: str, new_name: str) -> None:
        """Rename a file."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")
        blob = bucket.blob(prev_name)
        if not blob.exists():
            raise ValueError(prev_name + ": File not found.")
        new_blob = bucket.blob(new_name)
        if new_blob.exists():
            raise ValueError(new_name + ": File already exists.")
        bucket.copy_blob(blob, bucket, new_name)
        blob.delete()

    def delete_file(self, bucket_name: str, file_name: str) -> None:
        """Delete a file."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        if not bucket.exists():
            raise ValueError("Storage bucket not found.")
        blob = bucket.blob(file_name)
        if not blob.exists():
            raise ValueError(file_name + ": File not found.")
        blob.delete()

    def delete_batch_files(
        self,
        bucket_name: str,
        batch_files: list[str],
    ) -> Any:
        prefix = "validated/"

        now_iso = datetime.datetime.now()
        deleted: List[Dict[str, str]] = []
        not_found: List[str] = []
        errors: List[Dict[str, str]] = []

        for fname in batch_files:
            if not isinstance(fname, str) or not fname.strip():
                errors.append(
                    {
                        "file": str(fname),
                        "path": f"{prefix}{fname}",
                        "error": "invalid filename",
                    }
                )
                continue

            blob_path = f"{prefix}{fname}"
            try:
                logger.info("Attempting to delete gs://%s/%s", bucket_name, blob_path)
                # One-liner delete; raises NotFound if missing
                self.delete_file(bucket_name=bucket_name, file_name=blob_path)
                logger.info("Delete successful: gs://%s/%s", bucket_name, blob_path)
                deleted.append(
                    {"file": fname, "path": blob_path, "deleted_at": str(now_iso)}
                )
            except ValueError:
                logger.warning(
                    "Blob or bucket not found: gs://%s/%s", bucket_name, blob_path
                )
                not_found.append(fname)
            except Exception as e:  # network/other unexpected errors
                logger.exception(
                    "Unexpected error deleting gs://%s/%s", bucket_name, blob_path
                )
                errors.append({"file": fname, "path": blob_path, "error": str(e)})

        return {
            "deleted": deleted,
            "not_found": not_found,
            "errors": errors,
        }

    def validate_file(
        self,
        bucket_name: str,
        file_name: str,
        allowed_schemas: list[str],
        base_schema: dict,
        inst_schema: Optional[Dict[Any, Any]] = None,
        institution_id: str = "pdp",
        institution_identifier: Optional[str] = None,
    ) -> List[str]:
        """Validate that a file conforms to one of the allowed schemas.

        On success: archives the original to raw/{file_name}, writes the normalized
        (canonical columns, coerced dtypes) DataFrame to validated/{file_name}, and
        deletes from unvalidated/. Downstream uses validated/ only; raw/ is kept for record.

        Args:
            bucket_name: GCS bucket name.
            file_name: Blob name under unvalidated/.
            allowed_schemas: List of schema/model names allowed.
            base_schema: Base schema dict.
            inst_schema: Optional extension schema with institutions.* blocks.
            institution_id: Key into inst_schema["institutions"]: "edvise", "pdp", or
                institution UUID for custom. Default "pdp" for backward compatibility.
            institution_identifier: Optional institution ID (e.g. UUID). Reserved for
                future use; Edvise uses JSON-based validation only (different shape).

        Returns:
            List of inferred schema names (e.g. ["STUDENT"]).

        Raises:
            ValueError: If file not in unvalidated/, validated/ already exists, or
                normalized_df was not returned.
            HardValidationError: If validation fails (propagated from validator).
        """
        if not file_name or not file_name.strip():
            raise ValueError("file_name is required and must be non-empty.")
        if "/" in file_name:
            raise ValueError("file_name must not contain '/'.")
        if not allowed_schemas:
            raise ValueError("allowed_schemas must not be empty.")

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(f"unvalidated/{file_name}")
        if not blob.exists():
            raise ValueError(
                f"File not found: unvalidated/{file_name}. "
                "Upload the file to unvalidated/ before validating."
            )

        inferred_schema_names, normalized_df = (
            self._run_validation_and_get_normalized_df(
                blob,
                file_name,
                allowed_schemas,
                base_schema,
                inst_schema,
                institution_id,
                institution_identifier,
            )
        )
        if normalized_df is None:
            raise ValueError(
                "Validation succeeded but normalized_df was not returned; "
                "cannot write validated output (e.g. empty schema list)."
            )

        validated_blob_name = f"validated/{file_name}"
        validated_blob = bucket.blob(validated_blob_name)
        if validated_blob.exists():
            raise ValueError(validated_blob_name + ": File already exists.")

        self._archive_raw_and_write_validated(bucket, blob, file_name, normalized_df)
        return inferred_schema_names

    def _archive_raw_and_write_validated(
        self,
        bucket: Any,
        blob: Any,
        file_name: str,
        normalized_df: pd.DataFrame,
    ) -> None:
        """Copy blob to raw/, write normalized DataFrame to validated/, delete from unvalidated/."""
        raw_blob_name = f"raw/{file_name}"
        validated_blob_name = f"validated/{file_name}"
        bucket.copy_blob(blob, bucket, raw_blob_name)
        logging.debug("Archived original to %s", raw_blob_name)
        self._write_dataframe_to_gcs_as_csv(bucket, validated_blob_name, normalized_df)
        logging.debug("Wrote normalized data to %s", validated_blob_name)
        blob.delete()
        logging.debug("Validation complete: validated=normalized, raw=archived")

    def _run_validation_and_get_normalized_df(
        self,
        blob: Any,
        file_name: str,
        allowed_schemas: list[str],
        base_schema: dict,
        inst_schema: Optional[Dict[Any, Any]],
        institution_id: str,
        institution_identifier: Optional[str],
    ) -> tuple[List[str], Any]:
        """Run validation on blob content; return inferred schema names and normalized DataFrame."""
        try:
            with blob.open("r") as file:
                result = validate_file_reader(
                    file,
                    allowed_schemas,
                    base_schema,
                    inst_schema,
                    institution_id=institution_id,
                    institution_identifier=institution_identifier,
                )
            inferred_schema_names = [str(s) for s in result.get("schemas", [])]
            logging.debug(
                "Validation successful for %s: %s", file_name, inferred_schema_names
            )
            return inferred_schema_names, result.get("normalized_df")
        except HardValidationError:
            raise
        except (ValueError, UnicodeError) as e:
            logging.exception("Validation failed for %s: %s", file_name, e)
            raise
        except Exception as e:
            # Log any other error with context before re-raising (no silent failures).
            logging.exception("Validation failed for %s: %s", file_name, e)
            raise

    def _write_dataframe_to_gcs_as_csv(
        self, bucket: Any, blob_name: str, normalized_df: pd.DataFrame
    ) -> None:
        """Write a DataFrame to GCS as UTF-8 CSV. Used for validated/ output."""
        csv_buffer = io.StringIO()
        normalized_df.to_csv(
            csv_buffer, index=False, encoding="utf-8", lineterminator="\n"
        )
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            csv_buffer.getvalue().encode("utf-8"),
            content_type="text/csv; charset=utf-8",
        )

    def get_file_contents(self, bucket_name: str, file_name: str) -> Any:
        """Returns a file as a bytes object."""
        storage_client = storage.Client()
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(file_name)
        res = blob.download_as_bytes()
        return res

    def read_csv_as_dataframe(self, bucket_name: str, file_name: str) -> Any:
        """Read a CSV file from GCS and return as pandas DataFrame.

        Args:
            bucket_name: GCS bucket name
            file_name: Full blob path (e.g., 'validated/filename.csv')

        Returns:
            pandas DataFrame

        Raises:
            ValueError: If bucket or file not found
        """
        import pandas as pd

        storage_client = storage.Client()
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(file_name)

        if not blob.exists():
            raise ValueError(f"File not found: {file_name}")

        with blob.open("r") as fh:
            return pd.read_csv(fh)
