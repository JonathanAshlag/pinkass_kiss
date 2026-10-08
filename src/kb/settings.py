"""Every value that tunes the app in production (timeouts, workers, limits, endpoints), in
one place. Run `python -m kb.settings` to see the effective values and where they came from.

Values come from environment variables: on OpenShift a ConfigMap/Secret, locally
<repo>/.env (real env vars win over it). `.env.example` documents each one. They are read
and validated once, on the first `get_settings()` (the API logs them at startup), so a bad
value fails there instead of in the middle of a request. Unset or empty = the default.

Tests swap them with `set_settings(dataclasses.replace(get_settings(), ...))` (see the
`override_settings` fixture) and test parsing with `load_settings({...})`.

Not here: Alembic (`migrations/` reads DATABASE_URL / EMBEDDING_DIM itself, so migrations
never import app code), test/eval-only vars (E2E_*, JUDGE_*, OPIK_*), and the deploy
manifests' own knobs (router timeout, pod limits: `deploy/openshift/`).
"""

import os
import sys
from dataclasses import dataclass, field, fields
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _bool(raw: str) -> bool:
    return raw.lower() in ("1", "true", "yes")


def _cap(raw: str) -> int | None:
    return int(raw) or None  # 0 = no cap


def _workers(raw: str) -> int:
    return max(1, int(raw))


def _hours(raw: str) -> timedelta:
    return timedelta(hours=float(raw))


def _default_workers() -> int:
    # In a container this is the node's CPU count, not the pod's limit: set KB_INGEST_WORKERS.
    return min(4, getattr(os, "process_cpu_count", os.cpu_count)() or 1)  # process_cpu_count: 3.13+


def _masked(value: Any) -> str:
    return "***" if value else repr(value)


def _url_masked(value: Any) -> str:
    from sqlalchemy import make_url

    return make_url(value).render_as_string(hide_password=True) if value else repr(value)


def _setting(env: str, default: Any, parse: Callable[[str], Any] = str, show: Callable[[Any], str] = repr):
    return field(default=default, metadata={"env": env, "parse": parse, "show": show})


@dataclass(frozen=True)
class Settings:
    # --- Postgres (kb.storage.db) ---
    database_url: str = _setting("DATABASE_URL", None, show=_url_masked)  # required
    # Server-side, Postgres duration syntax, "0" = none: a stuck write fails and is undone.
    db_lock_timeout: str = _setting("KB_DB_LOCK_TIMEOUT", "10s")
    db_statement_timeout: str = _setting("KB_DB_STATEMENT_TIMEOUT", "60s")

    # --- Embeddings / semantic index (kb.semantic_index) ---
    # init_embeddings spec; names the index namespace, so changing it means a full re-embed.
    embeddings_model: str = _setting("EMBEDDINGS_MODEL", "ollama:nomic-embed-text")
    embeddings_base_url: str | None = _setting("EMBEDDINGS_BASE_URL", None)  # OpenAI-compatible server
    embeddings_api_key: str | None = _setting("EMBEDDINGS_API_KEY", None, show=_masked)
    embeddings_batch_size: int = _setting("EMBEDDINGS_BATCH_SIZE", 32, int)  # TEI's default max
    embedding_dim: int = _setting("EMBEDDING_DIM", 768, int)  # must match kb_chunks.embedding
    stage_batch_size: int = _setting("KB_STAGE_BATCH_SIZE", 128, int)  # chunks per add-only index() batch

    # --- S3 originals (kb.storage.blobs) ---
    blob_bucket: str | None = _setting("BLOB_BUCKET", None)  # unset = originals not kept
    blob_prefix: str = _setting("BLOB_PREFIX", "")
    blob_endpoint_url: str | None = _setting("BLOB_ENDPOINT_URL", None)  # MinIO etc.
    blob_required: bool = _setting("BLOB_REQUIRED", False, _bool)  # prod: no/broken store aborts startup
    # Worst case per call ~ attempts * (connect + read), instead of botocore's 60 s + 60 s.
    blob_connect_timeout: float = _setting("BLOB_CONNECT_TIMEOUT", 5.0, float)
    blob_read_timeout: float = _setting("BLOB_READ_TIMEOUT", 30.0, float)
    blob_max_attempts: int = _setting("BLOB_MAX_ATTEMPTS", 3, int)  # including the first
    blob_upload_workers: int = _setting("BLOB_UPLOAD_WORKERS", 8, int)  # parallel uploads per write

    # --- Ingest (kb.ingest) ---
    ingest_workers: int = _setting("KB_INGEST_WORKERS", _default_workers(), _workers)  # 1 = no pool
    # 0 = no cap. Starlette refuses forms with >1000 files with a bare 400: stay under ~990.
    upload_max_files: int | None = _setting("KB_UPLOAD_MAX_FILES", 500, _cap)
    upload_max_file_bytes: int | None = _setting("KB_UPLOAD_MAX_FILE_BYTES", 100 * 1024**2, _cap)
    upload_max_total_bytes: int | None = _setting("KB_UPLOAD_MAX_TOTAL_BYTES", 1024**3, _cap)

    # --- Maintenance (kb.maintenance, scripts/gc.py) ---
    # Orphans younger than this are left alone; it must outlast the longest ingest (#21).
    gc_grace: timedelta = _setting("KB_GC_GRACE_HOURS", timedelta(hours=24), _hours, str)


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """Parse `env` into Settings; ValueError (naming the variable) on a bad or missing value."""
    values = {}
    for f in fields(Settings):
        name = f.metadata["env"]
        raw = (env.get(name) or "").strip()
        if not raw:
            continue
        try:
            values[f.name] = f.metadata["parse"](raw)
        except ValueError as exc:
            raise ValueError(f"{name}={raw!r}: {exc}") from None
    settings = Settings(**values)
    if not settings.database_url:
        raise ValueError("DATABASE_URL is not set")
    return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    """The process-wide settings, loaded from the environment on first use."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Override (tests) or reset (None -> reloaded from env on next use)."""
    global _settings
    _settings = settings


def describe(settings: Settings, env: Mapping[str, str] = os.environ) -> list[str]:
    """One line per setting: env var, effective value (secrets masked), and its source."""
    lines = []
    for f in fields(Settings):
        name = f.metadata["env"]
        source = "env" if (env.get(name) or "").strip() else "default"
        lines.append(f"{name:<26} {f.metadata['show'](getattr(settings, f.name)):<40} ({source})")
    return lines


if __name__ == "__main__":
    try:
        print("\n".join(describe(get_settings())))
    except ValueError as exc:
        sys.exit(f"invalid settings: {exc}")
