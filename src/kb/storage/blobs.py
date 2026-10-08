"""
Object storage for original uploaded bytes (the PDF/DOCX/... a node's markdown was
converted from), recorded on the node in the `files.blob_*` columns.

An S3 bucket (or an S3-compatible endpoint such as MinIO), via boto3. Each original is
stored under its own file's key, `originals/<file_id>` (`original_key`): exactly one row
ever refers to an object, so undoing a failed write can delete what it uploaded without
asking who else uses it (identical uploads aren't deduplicated, by choice). Objects are
written *before* the DB commit that references them: until it, nothing can reach them;
a failed write deletes them (kb.service), and `scripts/gc.py` sweeps what a crash left.

Independent of the DB layers: never imports kb.service / kb.storage.dal.

Production notes: the client gets short timeouts and standard retries (env-tunable, see
`_store_from_env`); `check()` verifies bucket + permissions (`head_bucket`, which needs
`s3:ListBucket` -- the same permission that makes a missing key a 404 rather than a 403);
`BLOB_REQUIRED=1` turns "no store configured" / "check failed" into a startup error.
Every S3 failure other than a missing key surfaces as `BlobStoreError`.
"""

import base64
import hashlib
import logging
import mimetypes
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
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


ORIGINALS_PREFIX = "originals/"


def original_key(file_id: uuid.UUID) -> str:
    """The object key of a file's original: `originals/<file_id>`."""
    return f"{ORIGINALS_PREFIX}{file_id}"


@dataclass(frozen=True)
class Original:
    """A local file to keep as a node's original, described up front (hashed while
    planning), so the `files.blob_*` columns are known before anything is uploaded."""

    path: Path
    mime: str
    size: int
    sha256: str  # hex

    @classmethod
    def of(cls, path: Path, mime: str | None = None) -> "Original":
        digest, size = hashlib.sha256(), 0
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return cls(Path(path), mime or guess_mime(Path(path).name), size, digest.hexdigest())

    def columns(self, key: str) -> dict[str, Any]:
        """The `files.blob_*` columns for this original stored under `key`."""
        return {
            "blob_key": key,
            "blob_size_bytes": self.size,
            "blob_mime_type": self.mime,
            "blob_checksum": f"sha256:{self.sha256}",
        }


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

    def put_original(self, key: str, original: Original) -> None:
        """Uploads the file at `original.path` under `key`, streamed (never read whole),
        with its SHA-256, so S3 rejects bytes that don't match what was planned."""
        try:
            with original.path.open("rb") as body:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self.prefix + key,
                    Body=body,
                    ContentType=original.mime,
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(original.sha256)).decode(),
                )
        except (ClientError, BotoCoreError, OSError) as exc:
            raise BlobStoreError(f"storing {key} in s3://{self.bucket}: {_describe(exc)}") from exc

    def delete_originals(self, keys: list[str]) -> None:
        """Deletes these keys (missing ones are fine), 1000 per request."""
        for i in range(0, len(keys), 1000):
            batch = [{"Key": self.prefix + k} for k in keys[i : i + 1000]]
            try:
                resp = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
            except (ClientError, BotoCoreError) as exc:
                raise BlobStoreError(f"deleting from s3://{self.bucket}: {_describe(exc)}") from exc
            if resp.get("Errors"):
                first = resp["Errors"][0]
                raise BlobStoreError(f"deleting {first.get('Key')} from s3://{self.bucket}: {first.get('Code')}")

    def list_originals(self) -> list[tuple[str, datetime]]:
        """(key, last modified) of every object under `originals/` (for scripts/gc.py)."""
        out: list[tuple[str, datetime]] = []
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=self.prefix + ORIGINALS_PREFIX
            ):
                out.extend((o["Key"][len(self.prefix) :], o["LastModified"]) for o in page.get("Contents", []))
        except (ClientError, BotoCoreError) as exc:
            raise BlobStoreError(f"listing s3://{self.bucket}: {_describe(exc)}") from exc
        return out

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
