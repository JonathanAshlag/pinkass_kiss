from datetime import timedelta

import pytest

from kb.settings import describe, load_settings

URL = "postgresql+psycopg://u:secret@db:5432/kb"


def test_defaults():
    s = load_settings({"DATABASE_URL": URL})
    assert (s.db_lock_timeout, s.embeddings_batch_size, s.blob_upload_workers) == ("10s", 32, 8)
    assert (s.upload_max_files, s.upload_max_file_bytes, s.upload_max_total_bytes) == (500, 100 * 1024**2, 1024**3)
    assert s.blob_bucket is None and s.blob_required is False
    assert 1 <= s.ingest_workers <= 4
    assert s.gc_grace == timedelta(hours=24)


def test_parsing():
    s = load_settings({
        "DATABASE_URL": URL,
        "KB_UPLOAD_MAX_FILES": "7",
        "KB_UPLOAD_MAX_FILE_BYTES": "0",  # 0 = no cap
        "KB_UPLOAD_MAX_TOTAL_BYTES": "  ",  # blank = default
        "KB_INGEST_WORKERS": "0",  # floored at 1
        "BLOB_REQUIRED": "yes",
        "BLOB_READ_TIMEOUT": "2.5",
        "KB_GC_GRACE_HOURS": "0.5",
    })
    assert (s.upload_max_files, s.upload_max_file_bytes, s.upload_max_total_bytes) == (7, None, 1024**3)
    assert (s.ingest_workers, s.blob_required, s.blob_read_timeout) == (1, True, 2.5)
    assert s.gc_grace == timedelta(minutes=30)


def test_bad_value_names_the_variable():
    with pytest.raises(ValueError, match="KB_INGEST_WORKERS='four'"):
        load_settings({"DATABASE_URL": URL, "KB_INGEST_WORKERS": "four"})
    with pytest.raises(ValueError, match="DATABASE_URL is not set"):
        load_settings({})


def test_describe_masks_secrets():
    env = {"DATABASE_URL": URL, "EMBEDDINGS_API_KEY": "sk-123"}
    text = "\n".join(describe(load_settings(env), env))
    assert "secret" not in text and "sk-123" not in text
    assert "u:***@db:5432/kb" in text
    assert "EMBEDDINGS_API_KEY" in text and "(env)" in text and "(default)" in text
