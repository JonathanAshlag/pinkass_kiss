"""kb.storage.blobs: per-file originals in S3 (moto's in-process fake, see the
`blob_store` fixture) and env-based store selection. No DB, no network."""

import hashlib
import uuid

import pytest
from botocore.stub import Stubber

from kb.storage import blobs
from kb.storage.blobs import BlobStore, BlobStoreError, Original, guess_mime, original_key

PDF = b"%PDF-1.7 fake bytes"
DIGEST = hashlib.sha256(PDF).hexdigest()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(PDF)
    return Original.of(path)


def test_original_describes_the_file(pdf):
    assert (pdf.mime, pdf.size, pdf.sha256) == ("application/pdf", len(PDF), DIGEST)
    fid = uuid.uuid4()
    assert original_key(fid) == f"originals/{fid}"
    assert pdf.columns(original_key(fid)) == {
        "blob_key": f"originals/{fid}",
        "blob_size_bytes": len(PDF),
        "blob_mime_type": "application/pdf",
        "blob_checksum": f"sha256:{DIGEST}",
    }


def test_put_get_delete_original(blob_store, pdf):
    key = original_key(uuid.uuid4())
    blob_store.put_original(key, pdf)
    listing = blob_store.client.list_objects_v2(Bucket="bkt")["Contents"]
    assert [o["Key"] for o in listing] == [f"kb/{key}"]  # prefixed
    assert blob_store.client.head_object(Bucket="bkt", Key=f"kb/{key}")["ContentType"] == "application/pdf"
    assert blob_store.get_original(key).read() == PDF
    assert [k for k, _ in blob_store.list_originals()] == [key]

    blob_store.delete_originals([key, original_key(uuid.uuid4())])  # a missing key is fine
    assert blob_store.get_original(key) is None
    assert blob_store.list_originals() == []


def test_put_original_sends_the_planned_checksum(blob_store, pdf):
    # S3 rejects a PUT whose bytes don't hash to ChecksumSHA256 (moto doesn't check, so
    # assert the header is sent: the hash taken while planning, base64 of the digest).
    import base64

    from botocore.stub import ANY

    with Stubber(blob_store.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "bkt",
                "Key": "kb/originals/x",
                "Body": ANY,
                "ContentType": "application/pdf",
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(DIGEST)).decode(),
            },
        )
        blob_store.put_original("originals/x", pdf)
        stub.assert_no_pending_responses()


def test_get_original_missing_is_none(blob_store):
    assert blob_store.get_original("sha256/missing") is None


def test_other_s3_errors_raise_blob_store_error(blob_store):
    store = BlobStore("no-such-bucket", client=blob_store.client)
    with pytest.raises(BlobStoreError, match="NoSuchBucket"):
        store.get_original("sha256/x")
    with pytest.raises(BlobStoreError, match="NoSuchBucket"):
        store.put_original("originals/x", Original.of(__file__))
    with pytest.raises(BlobStoreError, match="NoSuchBucket"):
        store.delete_originals(["originals/x"])


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


def test_original_defaults_mime(tmp_path):
    (tmp_path / "noext").write_bytes(b"x")
    assert Original.of(tmp_path / "noext").mime == "application/octet-stream"


def test_guess_mime():
    assert guess_mime("a/Report.PDF") == "application/pdf"
    assert guess_mime("deck.pptx").endswith("presentationml.presentation")
    assert guess_mime("page.htm") == "text/html"
    assert guess_mime("noext") == "application/octet-stream"


# --------------------------------------------------------------------------
# get_blob_store / set_blob_store (selection from kb.settings)
# --------------------------------------------------------------------------


@pytest.fixture
def clean_env(override_settings, monkeypatch):
    """No blob settings (whatever .env says); yields `override_settings`."""
    override_settings(blob_bucket=None, blob_endpoint_url=None, blob_prefix="", blob_required=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    blobs.reset_blob_store()
    yield override_settings
    blobs.reset_blob_store()


def test_no_env_means_no_store(clean_env):
    assert blobs.get_blob_store() is None
    assert blobs.check_blob_store() == "not configured"


def test_blob_required_without_bucket_raises(clean_env):
    clean_env(blob_required=True)
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
    clean_env(blob_bucket="bkt")
    config = blobs.get_blob_store().client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (5.0, 30.0)
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}

    blobs.reset_blob_store()
    clean_env(blob_connect_timeout=2.0, blob_read_timeout=10.0, blob_max_attempts=5)
    config = blobs.get_blob_store().client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (2.0, 10.0)
    assert config.retries["total_max_attempts"] == 5


def test_bucket_settings(clean_env):
    clean_env(blob_bucket="bkt", blob_prefix="kb/", blob_endpoint_url="http://minio:9000")
    store = blobs.get_blob_store()
    assert isinstance(store, BlobStore)
    assert (store.bucket, store.prefix, store.client.meta.endpoint_url) == ("bkt", "kb/", "http://minio:9000")
    assert blobs.get_blob_store() is store  # cached


def test_set_blob_store_overrides(clean_env, blob_store):
    clean_env(blob_bucket="other")
    blobs.set_blob_store(blob_store)
    assert blobs.get_blob_store() is blob_store
    blobs.set_blob_store(None)
    assert blobs.get_blob_store() is None
