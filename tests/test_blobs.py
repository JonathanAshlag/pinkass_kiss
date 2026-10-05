"""kb.blobs: content-addressed originals over any ByteStore, S3ByteStore against a
stubbed boto3 client, and env-based store selection. No DB, no network."""

import hashlib
import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from langchain_classic.storage import LocalFileStore
from langchain_core.stores import InMemoryByteStore

from kb import blobs
from kb.blobs import S3ByteStore, get_original, guess_mime, put_original

PDF = b"%PDF-1.7 fake bytes"
DIGEST = hashlib.sha256(PDF).hexdigest()


@pytest.mark.parametrize("make_store", [lambda tmp: InMemoryByteStore(), lambda tmp: LocalFileStore(tmp)])
def test_put_get_roundtrip_and_dedupe(make_store, tmp_path):
    store = make_store(tmp_path)

    cols = put_original(store, PDF, "application/pdf")
    again = put_original(store, PDF, "application/pdf")

    assert cols == again == {
        "blob_key": f"sha256/{DIGEST}",
        "blob_size_bytes": len(PDF),
        "blob_mime_type": "application/pdf",
        "blob_checksum": f"sha256:{DIGEST}",
    }
    assert list(store.yield_keys()) == [f"sha256/{DIGEST}"]  # one object for both uploads
    assert get_original(store, cols["blob_key"]) == PDF
    assert get_original(store, "sha256/missing") is None


def test_put_original_defaults_mime():
    assert put_original(InMemoryByteStore(), b"x", None)["blob_mime_type"] == "application/octet-stream"


def test_guess_mime():
    assert guess_mime("a/Report.PDF") == "application/pdf"
    assert guess_mime("deck.pptx").endswith("presentationml.presentation")
    assert guess_mime("page.htm") == "text/html"
    assert guess_mime("noext") == "application/octet-stream"


# --------------------------------------------------------------------------
# S3ByteStore (stubbed boto3 client)
# --------------------------------------------------------------------------


@pytest.fixture
def s3():
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


def test_s3_mset_mget_with_prefix(s3):
    client, stub = s3
    store = S3ByteStore("bkt", prefix="kb/", client=client)
    stub.add_response("put_object", {}, {"Bucket": "bkt", "Key": "kb/a", "Body": b"1"})
    stub.add_response("get_object", {"Body": body(b"1")}, {"Bucket": "bkt", "Key": "kb/a"})
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404, expected_params={"Bucket": "bkt", "Key": "kb/b"})
    stub.add_client_error("get_object", "404", http_status_code=404, expected_params={"Bucket": "bkt", "Key": "kb/c"})

    store.mset([("a", b"1")])
    assert store.mget(["a", "b", "c"]) == [b"1", None, None]


def test_s3_other_errors_propagate(s3):
    client, stub = s3
    stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
    with pytest.raises(Exception, match="AccessDenied"):
        S3ByteStore("bkt", client=client).mget(["a"])


def test_s3_mdelete_and_yield_keys_strip_prefix(s3):
    client, stub = s3
    store = S3ByteStore("bkt", prefix="kb/", client=client)
    stub.add_response("delete_object", {}, {"Bucket": "bkt", "Key": "kb/a"})
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "kb/sha256/1"}, {"Key": "kb/sha256/2"}], "IsTruncated": False},
        {"Bucket": "bkt", "Prefix": "kb/sha256/"},
    )

    store.mdelete(["a"])
    assert list(store.yield_keys(prefix="sha256/")) == ["sha256/1", "sha256/2"]


def test_put_original_through_s3(s3):
    client, stub = s3
    stub.add_response("put_object", {}, {"Bucket": "bkt", "Key": f"sha256/{DIGEST}", "Body": PDF})
    cols = put_original(S3ByteStore("bkt", client=client), PDF, "application/pdf")
    assert cols["blob_key"] == f"sha256/{DIGEST}"


# --------------------------------------------------------------------------
# get_blob_store / set_blob_store (env selection)
# --------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("BLOB_BUCKET", "BLOB_ENDPOINT_URL", "BLOB_PREFIX", "BLOB_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    blobs.reset_blob_store()
    yield monkeypatch
    blobs.reset_blob_store()


def test_no_env_means_no_store(clean_env):
    assert blobs.get_blob_store() is None


def test_local_dir_env(clean_env, tmp_path):
    clean_env.setenv("BLOB_LOCAL_DIR", str(tmp_path))
    store = blobs.get_blob_store()
    assert isinstance(store, LocalFileStore)
    assert blobs.get_blob_store() is store  # cached


def test_bucket_env_wins(clean_env, tmp_path):
    clean_env.setenv("BLOB_LOCAL_DIR", str(tmp_path))
    clean_env.setenv("BLOB_BUCKET", "bkt")
    clean_env.setenv("BLOB_PREFIX", "kb/")
    clean_env.setenv("BLOB_ENDPOINT_URL", "http://minio:9000")
    clean_env.setenv("AWS_DEFAULT_REGION", "us-east-1")
    store = blobs.get_blob_store()
    assert isinstance(store, S3ByteStore)
    assert (store.bucket, store.prefix, store.client.meta.endpoint_url) == ("bkt", "kb/", "http://minio:9000")


def test_set_blob_store_overrides(clean_env, tmp_path):
    clean_env.setenv("BLOB_LOCAL_DIR", str(tmp_path))
    mem = InMemoryByteStore()
    blobs.set_blob_store(mem)
    assert blobs.get_blob_store() is mem
    blobs.set_blob_store(None)
    assert blobs.get_blob_store() is None
