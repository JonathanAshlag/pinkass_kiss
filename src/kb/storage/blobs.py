"""
Object storage for original uploaded bytes (the PDF/DOCX/... a node's markdown was
converted from), recorded on the node in the `files.blob_*` columns.

An S3 bucket (or an S3-compatible endpoint such as MinIO), via boto3. Originals are
stored content-addressed (`sha256/<hex>`), so identical uploads share one object.

Independent of the DB layers: never imports kb.service / kb.storage.dal.

Production notes: the client gets short timeouts and standard retries (env-tunable, see
`_store_from_env`); `check()` verifies bucket + permissions (`head_bucket`, which needs
`s3:ListBucket` -- the same permission that makes a missing key a 404 rather than a 403);
`BLOB_REQUIRED=1` turns "no store configured" / "check failed" into a startup error.
Every S3 failure other than a missing key surfaces as `BlobStoreError`.
"""

import hashlib
import logging
import mimetypes
import os
from pathlib import PurePath
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

# Client defaults; env overrides in _store_from_env. Worst case per call is roughly
# max_attempts * (connect + read) instead of botocore's 60 s + 60 s per attempt.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
MAX_ATTEMPTS = 3

# Only used when the platform's mimetypes table lacks an entry.
_MIME_FALLBACK = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
DEFAULT_MIME = "application/octet-stream"


def guess_mime(name: str | PurePath) -> str:
    """MIME type from a file name's extension, `application/octet-stream` if unknown."""
    name = str(name)
    mime, _ = mimetypes.guess_type(name)
    return mime or _MIME_FALLBACK.get(PurePath(name).suffix.lower(), DEFAULT_MIME)


class BlobStoreError(Exception):
    """S3 is unreachable or refused a request (anything but "no such key"). Deliberately
    not a ValueError: the API maps it to 502, not to the ValueError -> 404 handler."""


class BlobStore:
    """Originals in an S3 bucket. `prefix` namespaces every key (e.g. "kb/");
    `endpoint_url` points at an S3-compatible service. Pass `client` to reuse/inject a
    boto3 S3 client (used as-is: the timeout/retry arguments only apply to one built here)."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        client: Any = None,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            config = Config(
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                # total_max_attempts counts the first try; botocore's max_attempts doesn't
                retries={"mode": "standard", "total_max_attempts": max_attempts},
            )
            client = boto3.client("s3", endpoint_url=endpoint_url, config=config)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def put_original(self, data: bytes, mime: str | None) -> dict[str, Any]:
        """Stores `data` under `sha256/<hex>` and returns the `files.blob_*` columns for it."""
        digest = hashlib.sha256(data).hexdigest()
        key = f"sha256/{digest}"
        mime = mime or DEFAULT_MIME
        try:  # same bytes -> same key: re-uploads dedupe
            self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data, ContentType=mime)
        except (ClientError, BotoCoreError) as exc:
            raise BlobStoreError(f"storing {key} in s3://{self.bucket}: {_describe(exc)}") from exc
        return {
            "blob_key": key,
            "blob_size_bytes": len(data),
            "blob_mime_type": mime,
            "blob_checksum": f"sha256:{digest}",
        }

    def get_original(self, key: str) -> bytes | None:
        """The stored bytes for `key`, or None if missing. Without `s3:ListBucket`, S3
        answers a missing key with AccessDenied, which is raised (as BlobStoreError), not
        guessed to be "missing" -- that would hide a real permission problem."""
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
            return resp["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        except (ClientError, BotoCoreError) as exc:
            raise BlobStoreError(f"reading {key} from s3://{self.bucket}: {_describe(exc)}") from exc

    def check(self) -> None:
        """Raises BlobStoreError unless the bucket exists and is reachable with
        `s3:ListBucket` (credentials, region, endpoint and bucket name all right)."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as exc:
            hint = {
                "404": "no such bucket",
                "403": "access denied (the credentials need s3:ListBucket on the bucket)",
                "301": "bucket is in another region (set AWS_DEFAULT_REGION)",
                "400": "bad request (often a wrong region or endpoint)",
            }.get(_error_code(exc) or "", _describe(exc))
            raise BlobStoreError(f"s3://{self.bucket}: {hint}") from exc


def _error_code(exc: Exception) -> str | None:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code"))
    return None


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# The process-wide store (from env), overridable for tests
# --------------------------------------------------------------------------

_UNSET: Any = object()
_store: BlobStore | None = _UNSET


def blob_required() -> bool:
    """`BLOB_REQUIRED=1`: a missing or failing store is an error, not a warning (prod)."""
    return os.environ.get("BLOB_REQUIRED", "").strip().lower() in ("1", "true", "yes")


def _store_from_env() -> BlobStore | None:
    bucket = os.environ.get("BLOB_BUCKET")
    if bucket:
        return BlobStore(
            bucket,
            prefix=os.environ.get("BLOB_PREFIX", ""),
            endpoint_url=os.environ.get("BLOB_ENDPOINT_URL") or None,
            connect_timeout=float(os.environ.get("BLOB_CONNECT_TIMEOUT") or CONNECT_TIMEOUT),
            read_timeout=float(os.environ.get("BLOB_READ_TIMEOUT") or READ_TIMEOUT),
            max_attempts=int(os.environ.get("BLOB_MAX_ATTEMPTS") or MAX_ATTEMPTS),
        )
    if blob_required():
        raise BlobStoreError("BLOB_REQUIRED is set but BLOB_BUCKET is not")
    log.warning("no BLOB_BUCKET set: original uploaded files are not retained")
    return None


def get_blob_store() -> BlobStore | None:
    """The configured store, built once from env: `BLOB_BUCKET` (+ optional
    `BLOB_ENDPOINT_URL`, `BLOB_PREFIX`, timeouts); None (originals aren't retained) if
    unset. Raises BlobStoreError if unset while `BLOB_REQUIRED` is."""
    global _store
    if _store is _UNSET:
        _store = _store_from_env()
    return _store


def set_blob_store(store: BlobStore | None) -> None:
    """Override the process-wide store (tests). `None` means "no store"."""
    global _store
    _store = store


def reset_blob_store() -> None:
    """Forget the cached store, so the next `get_blob_store()` re-reads env."""
    global _store
    _store = _UNSET


def check_blob_store() -> str:
    """Startup / health check: "ok", or "not configured" when there's no store.
    Raises BlobStoreError when the store is required but missing, or fails `check()`."""
    store = get_blob_store()  # raises if BLOB_REQUIRED and no bucket
    if store is None:
        return "not configured"
    store.check()
    return "ok"
