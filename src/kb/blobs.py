"""
Object storage for original uploaded bytes (the PDF/DOCX/... a node's markdown was
converted from), recorded on the node in the `files.blob_*` columns.

Any LangChain `ByteStore` works: `S3ByteStore` (S3 or an S3-compatible endpoint such as
MinIO/GCS), `LocalFileStore` for dev, `InMemoryByteStore` for tests. Originals are stored
content-addressed (`sha256/<hex>`), so identical uploads share one object.

Independent of the DB layers: never imports kb.service / kb.dal.
"""

import hashlib
import logging
import mimetypes
import os
from collections.abc import Iterator, Sequence
from pathlib import PurePath
from typing import Any

from langchain_core.stores import ByteStore

log = logging.getLogger(__name__)

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


class S3ByteStore(ByteStore):
    """A `ByteStore` over an S3 bucket. `prefix` namespaces every key (e.g. "kb/");
    `endpoint_url` points at an S3-compatible service. Pass `client` to reuse/inject a
    boto3 S3 client (tests stub one)."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        client: Any = None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return self.prefix + key

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        out: list[bytes | None] = []
        for key in keys:
            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            except self.client.exceptions.NoSuchKey:
                out.append(None)
                continue
            except Exception as exc:  # some S3-compatibles answer a bare 404
                if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                    out.append(None)
                    continue
                raise
            out.append(resp["Body"].read())
        return out

    def mset(self, key_value_pairs: Sequence[tuple[str, bytes]]) -> None:
        for key, value in key_value_pairs:
            self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=value)

    def mdelete(self, keys: Sequence[str]) -> None:
        for key in keys:  # S3 delete is idempotent: missing keys are fine
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def yield_keys(self, *, prefix: str | None = None) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix or "")):
            for obj in page.get("Contents", []):
                yield obj["Key"][len(self.prefix):]


def _error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code"))
    return None


# --------------------------------------------------------------------------
# The process-wide store (from env), overridable for tests
# --------------------------------------------------------------------------

_UNSET: Any = object()
_store: ByteStore | None = _UNSET


def _store_from_env() -> ByteStore | None:
    bucket = os.environ.get("BLOB_BUCKET")
    if bucket:
        return S3ByteStore(
            bucket,
            prefix=os.environ.get("BLOB_PREFIX", ""),
            endpoint_url=os.environ.get("BLOB_ENDPOINT_URL") or None,
        )
    local_dir = os.environ.get("BLOB_LOCAL_DIR")
    if local_dir:
        from langchain_classic.storage import LocalFileStore

        return LocalFileStore(local_dir)
    log.warning("no BLOB_BUCKET or BLOB_LOCAL_DIR set: original uploaded files are not retained")
    return None


def get_blob_store() -> ByteStore | None:
    """The configured store, built once from env: `BLOB_BUCKET` (+ optional
    `BLOB_ENDPOINT_URL`, `BLOB_PREFIX`) -> S3; else `BLOB_LOCAL_DIR` -> LocalFileStore;
    else None (originals aren't retained)."""
    global _store
    if _store is _UNSET:
        _store = _store_from_env()
    return _store


def set_blob_store(store: ByteStore | None) -> None:
    """Override the process-wide store (tests). `None` means "no store"."""
    global _store
    _store = store


def reset_blob_store() -> None:
    """Forget the cached store, so the next `get_blob_store()` re-reads env."""
    global _store
    _store = _UNSET


# --------------------------------------------------------------------------
# Originals
# --------------------------------------------------------------------------


def put_original(store: ByteStore, data: bytes, mime: str | None) -> dict[str, Any]:
    """Stores `data` under `sha256/<hex>` and returns the `files.blob_*` columns for it."""
    digest = hashlib.sha256(data).hexdigest()
    key = f"sha256/{digest}"
    store.mset([(key, data)])  # same bytes -> same key: re-uploads dedupe
    return {
        "blob_key": key,
        "blob_size_bytes": len(data),
        "blob_mime_type": mime or DEFAULT_MIME,
        "blob_checksum": f"sha256:{digest}",
    }


def get_original(store: ByteStore, key: str) -> bytes | None:
    """The stored bytes for `key`, or None if missing."""
    (data,) = store.mget([key])
    return data
