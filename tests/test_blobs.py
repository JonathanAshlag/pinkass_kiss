"""kb.storage.blobs: content-addressed originals in S3 (moto's in-process fake, see the
`blob_store` fixture) and env-based store selection. No DB, no network."""

import hashlib

import pytest
from botocore.stub import Stubber

from kb.storage import blobs
from kb.storage.blobs import BlobStore, BlobStoreError, guess_mime

PDF = b"%PDF-1.7 fake bytes"
DIGEST = hashlib.sha256(PDF).hexdigest()


def test_put_original_is_content_addressed(blob_store):
    cols = blob_store.put_original(PDF, "application/pdf")
    assert blob_store.put_original(PDF, "application/pdf") == cols  # idempotent
    assert cols == {
        "blob_key": f"sha256/{DIGEST}",
        "blob_size_bytes": len(PDF),
        "blob_mime_type": "application/pdf",
        "blob_checksum": f"sha256:{DIGEST}",
    }
    listing = blob_store.client.list_objects_v2(Bucket="bkt")["Contents"]
    assert [o["Key"] for o in listing] == [f"kb/sha256/{DIGEST}"]  # one object, prefixed
    head = blob_store.client.head_object(Bucket="bkt", Key=f"kb/sha256/{DIGEST}")
    assert head["ContentType"] == "application/pdf"
    assert blob_store.get_original(cols["blob_key"]) == PDF


def test_get_original_missing_is_none(blob_store):
    assert blob_store.get_original("sha256/missing") is None


def test_other_s3_errors_raise_blob_store_error(blob_store):
    store = BlobStore("no-such-bucket", client=blob_store.client)
    with pytest.raises(BlobStoreError, match="NoSuchBucket"):
        store.get_original("sha256/x")
    with pytest.raises(BlobStoreError, match="NoSuchBucket"):
        store.put_original(PDF, "application/pdf")


def test_access_denied_is_an_error_not_missing(blob_store):
    # Without s3:ListBucket, S3 answers a missing key with AccessDenied: never read as "missing".
    with Stubber(blob_store.client) as stub:
        stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
        with pytest.raises(BlobStoreError, match="AccessDenied"):
            blob_store.get_original("sha256/x")


def test_check(blob_store):
    blob_store.check()  # the fixture's bucket exists
    with pytest.raises(BlobStoreError, match="no such bucket"):
        BlobStore("no-such-bucket", client=blob_store.client).check()
    with Stubber(blob_store.client) as stub:
        stub.add_client_error("head_bucket", "403", http_status_code=403)
        with pytest.raises(BlobStoreError, match="s3:ListBucket"):
            blob_store.check()


def test_put_original_defaults_mime(blob_store):
    assert blob_store.put_original(b"x", None)["blob_mime_type"] == "application/octet-stream"


def test_guess_mime():
    assert guess_mime("a/Report.PDF") == "application/pdf"
    assert guess_mime("deck.pptx").endswith("presentationml.presentation")
    assert guess_mime("page.htm") == "text/html"
    assert guess_mime("noext") == "application/octet-stream"


# --------------------------------------------------------------------------
# get_blob_store / set_blob_store (env selection)
# --------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "BLOB_BUCKET", "BLOB_ENDPOINT_URL", "BLOB_PREFIX", "BLOB_REQUIRED",
        "BLOB_CONNECT_TIMEOUT", "BLOB_READ_TIMEOUT", "BLOB_MAX_ATTEMPTS",
    ):
        monkeypatch.delenv(var, raising=False)
    blobs.reset_blob_store()
    yield monkeypatch
    blobs.reset_blob_store()


def test_no_env_means_no_store(clean_env):
    assert blobs.get_blob_store() is None
    assert blobs.check_blob_store() == "not configured"


def test_blob_required_without_bucket_raises(clean_env):
    clean_env.setenv("BLOB_REQUIRED", "1")
    with pytest.raises(BlobStoreError, match="BLOB_BUCKET"):
        blobs.get_blob_store()
    with pytest.raises(BlobStoreError):
        blobs.check_blob_store()


def test_check_blob_store(clean_env, blob_store):
    blobs.set_blob_store(blob_store)
    assert blobs.check_blob_store() == "ok"
    blobs.set_blob_store(BlobStore("no-such-bucket", client=blob_store.client))
    with pytest.raises(BlobStoreError, match="no such bucket"):
        blobs.check_blob_store()


def test_client_timeouts_and_retries(clean_env):
    clean_env.setenv("BLOB_BUCKET", "bkt")
    clean_env.setenv("AWS_DEFAULT_REGION", "us-east-1")
    config = blobs.get_blob_store().client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (5.0, 30.0)
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}

    blobs.reset_blob_store()
    clean_env.setenv("BLOB_CONNECT_TIMEOUT", "2")
    clean_env.setenv("BLOB_READ_TIMEOUT", "10")
    clean_env.setenv("BLOB_MAX_ATTEMPTS", "5")
    config = blobs.get_blob_store().client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (2.0, 10.0)
    assert config.retries["total_max_attempts"] == 5


def test_bucket_env(clean_env):
    clean_env.setenv("BLOB_BUCKET", "bkt")
    clean_env.setenv("BLOB_PREFIX", "kb/")
    clean_env.setenv("BLOB_ENDPOINT_URL", "http://minio:9000")
    clean_env.setenv("AWS_DEFAULT_REGION", "us-east-1")
    store = blobs.get_blob_store()
    assert isinstance(store, BlobStore)
    assert (store.bucket, store.prefix, store.client.meta.endpoint_url) == ("bkt", "kb/", "http://minio:9000")
    assert blobs.get_blob_store() is store  # cached


def test_set_blob_store_overrides(clean_env, blob_store):
    clean_env.setenv("BLOB_BUCKET", "other")
    blobs.set_blob_store(blob_store)
    assert blobs.get_blob_store() is blob_store
    blobs.set_blob_store(None)
    assert blobs.get_blob_store() is None
