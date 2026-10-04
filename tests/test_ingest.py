"""Folder ingestion (kb.ingest): registry/processor unit tests plus DB-backed walker tests."""

from pathlib import Path

import pytest

from kb.ingest import ProcessedDocument, ProcessorRegistry, default_registry, ingest_folder
from kb.ingest.markdown import MarkdownProcessor


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Registry / processors (no DB)
# --------------------------------------------------------------------------


def test_registry_lookup_is_case_insensitive():
    registry = default_registry()
    assert registry.for_path(Path("a/NOTES.MD")).name == "markdown"
    assert registry.for_path(Path("x.markdown")).name == "markdown"
    assert registry.for_path(Path("image.png")) is None
    assert registry.for_path(Path("Makefile")) is None


def test_registry_rejects_duplicate_extension():
    registry = default_registry()
    with pytest.raises(ValueError, match="already handled"):
        registry.register(MarkdownProcessor())


def test_markdown_title_from_h1_else_stem(tmp_path):
    proc = MarkdownProcessor()
    with_h1 = write(tmp_path / "a.md", "intro\n\n# Real Title #\n\nbody\n## Sub\n")
    no_h1 = write(tmp_path / "my-notes.md", "## only a subheading\n")
    doc = proc.process(with_h1)
    assert doc.title == "Real Title"
    assert doc.content == with_h1.read_text()  # stored verbatim
    assert proc.process(no_h1).title == "my-notes"


# --------------------------------------------------------------------------
# Walker (needs TEST_DATABASE_URL)
# --------------------------------------------------------------------------


@pytest.fixture
def db_session(migrated_db):
    from kb.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def children_by_title(session, parent_id):
    from kb import service

    return {n.title: n for n in service.list_children(session, parent_id)}


def test_mirrors_tree_and_skips_unsupported(db_session, tmp_path):
    src = tmp_path / "docs"
    write(src / "readme.md", "# Readme\nhello")
    write(src / "guide" / "setup.md", "# Setup\nsteps")
    write(src / "guide" / "deep" / "faq.md", "no heading")
    write(src / "guide" / "diagram.png", b"\x89PNG")
    write(src / "images" / "logo.png", b"\x89PNG")  # image-only dir: no node
    write(src / ".git" / "HEAD.md", "# hidden")
    write(src / ".hidden.md", "# hidden")

    report = ingest_folder(db_session, src, tags=["run:1"])

    assert len(report.files_created) == 3
    assert len(report.folders_created) == 3  # docs, guide, deep
    assert sorted(p.name for p in report.skipped) == ["diagram.png", "logo.png"]
    assert report.failed == []

    root = children_by_title(db_session, None)["docs"]
    assert root.id == report.root_id and root.kind == "folder"
    top = children_by_title(db_session, root.id)
    assert set(top) == {"Readme", "guide"}
    guide = children_by_title(db_session, top["guide"].id)
    assert set(guide) == {"Setup", "deep"}
    faq = children_by_title(db_session, guide["deep"].id)["faq"]
    assert faq.content == "no heading"
    assert faq.tags == ["run:1"]
    assert faq.sources == [{"resource": (src / "guide" / "deep" / "faq.md").resolve().as_uri()}]


def test_bad_file_is_reported_not_fatal(db_session, tmp_path):
    write(tmp_path / "ok.md", "# OK")
    write(tmp_path / "bad.md", b"\xff\xfe\xfa not utf-8")

    report = ingest_folder(db_session, tmp_path)

    assert len(report.files_created) == 1
    assert [p.name for p, _ in report.failed] == ["bad.md"]
    assert "UnicodeDecodeError" in report.failed[0][1]


def test_parent_must_be_an_existing_folder(db_session, tmp_path):
    from kb import service

    note = service.create_file(db_session, parent_id=None, kind="file", title="n", content="x")
    with pytest.raises(ValueError):
        ingest_folder(db_session, tmp_path, parent_id=note.id)
    with pytest.raises(ValueError):
        ingest_folder(db_session, tmp_path / "missing")

    folder = service.create_file(db_session, parent_id=None, kind="folder", title="f")
    report = ingest_folder(db_session, tmp_path, parent_id=folder.id)
    assert service.get_node(db_session, report.root_id).parent_id == folder.id


def test_custom_processor_is_picked_up(db_session, tmp_path):
    class TxtProcessor:
        name = "txt"
        extensions = frozenset({".txt"})

        def process(self, path):
            return ProcessedDocument(
                title=path.stem.upper(),
                content=path.read_text(),
                extra={"description": "plain text"},
            )

    registry = ProcessorRegistry()
    registry.register(TxtProcessor())
    write(tmp_path / "note.txt", "plain")
    write(tmp_path / "skip.md", "# not registered here")

    report = ingest_folder(db_session, tmp_path, registry=registry)

    assert [p.name for p in report.skipped] == ["skip.md"]
    node = children_by_title(db_session, report.root_id)["NOTE"]
    assert node.content == "plain" and node.description == "plain text"


# --------------------------------------------------------------------------
# Uploads (kb.ingest.upload + POST /ingest)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "paths",
    [
        ["../evil.md"],
        ["/abs/a.md"],
        ["docs/../../x.md"],
        ["docs\\a.md"],
        ["a.md"],  # not inside a folder
        ["docs/a.md", "other/b.md"],  # two top-level folders
        [],
    ],
)
def test_upload_rejects_bad_paths(paths):
    from kb.ingest import UploadError, ingest_upload

    with pytest.raises(UploadError):
        ingest_upload(None, [(p, b"# x") for p in paths])  # rejected before any DB use


def test_upload_mirrors_tree(db_session):
    from kb.ingest import ingest_upload

    report = ingest_upload(
        db_session,
        [
            ("docs/readme.md", b"# Readme"),
            ("docs/guide/setup.md", b"# Setup"),
            ("docs/img/logo.png", b"\x89PNG"),
            ("docs/bad.md", b"\xff\xfe not utf-8"),
        ],
        tags=["up"],
    )

    assert [p.as_posix() for p in report.skipped] == ["docs/img/logo.png"]
    assert [p.as_posix() for p, _ in report.failed] == ["docs/bad.md"]
    root = children_by_title(db_session, None)["docs"]
    assert root.id == report.root_id
    guide = children_by_title(db_session, children_by_title(db_session, root.id)["guide"].id)
    assert guide["Setup"].sources == [{"resource": "upload:docs/guide/setup.md"}]
    assert guide["Setup"].tags == ["up"]


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from kb.api import app, get_session

    app.dependency_overrides[get_session] = lambda: db_session  # rolled back by db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_ingest_endpoint(client, db_session):
    assert client.get("/ingest/extensions").json() == {"extensions": [".markdown", ".md"]}

    res = client.post(
        "/ingest",
        files=[("files", ("a.md", b"# A", "text/markdown")), ("files", ("p.png", b"x", "image/png"))],
        data={"paths": ["notes/sub/a.md", "notes/p.png"], "tags": ["t1", "t2"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["files_created"]) == 1 and len(body["folders_created"]) == 2
    assert body["skipped"] == ["notes/p.png"] and body["failed"] == []
    assert client.get(f"/files/{body['root_id']}").json()["tags"] == ["t1", "t2"]

    bad = client.post("/ingest", files=[("files", ("a.md", b"x"))], data={"paths": ["../a.md"]})
    assert bad.status_code == 422
    mismatch = client.post("/ingest", files=[("files", ("a.md", b"x"))], data={"paths": ["n/a.md", "n/b.md"]})
    assert mismatch.status_code == 422
