"""Folder ingestion (kb.ingest): registry/processor and folder-plan tests (no DB), plus
DB-backed materialize/walker tests."""

import io
import uuid
from pathlib import Path

import pytest

from kb.ingest import (
    PlannedNode,
    ProcessedDocument,
    ProcessorRegistry,
    default_registry,
    ingest_folder,
    materialize,
    plan_folder,
)
from kb.ingest.processors.markdown import MarkdownProcessor


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


def test_default_registry_handles_pdf():
    registry = default_registry()
    assert {".md", ".markdown", ".pdf"} <= registry.supported_extensions
    assert getattr(registry.for_path(Path("a.PDF")), "retain_original", False)
    assert not getattr(registry.for_path(Path("a.md")), "retain_original", False)


def test_code_files_are_not_supported():
    registry = default_registry()
    assert {".txt", ".md"} <= registry.supported_extensions
    assert not {".py", ".js", ".json", ".yaml", ".sh", ".sql"} & registry.supported_extensions


def test_markdown_title_is_file_stem(tmp_path):
    proc = MarkdownProcessor()
    with_h1 = write(tmp_path / "my-notes.md", "intro\n\n# Real Title #\n\nbody\n## Sub\n")
    doc = proc.process(with_h1)
    assert doc.title == "my-notes"  # the H1 is not parsed
    assert doc.content == with_h1.read_text()  # stored verbatim


# --------------------------------------------------------------------------
# Folder plan (no DB)
# --------------------------------------------------------------------------


def test_plan_lists_root_then_files_only(tmp_path):
    src = tmp_path / "docs"
    write(src / "readme.md", "# Readme\nhello")
    write(src / "guide" / "deep" / "faq.md", "no heading")
    write(src / "images" / "logo.png", b"\x89PNG")
    write(src / ".hidden.md", "# hidden")

    plan = plan_folder(src, tags=["t"])

    assert [(n.path, n.type, n.title) for n in plan.nodes] == [
        ("", "folder", "docs"),
        ("guide/deep/faq.md", "file", "faq"),
        ("readme.md", "file", "readme"),
    ]
    faq = plan.nodes[1]
    assert faq.content == "no heading"
    assert faq.fields == {"tags": ["t"], "sources": [{"resource": (src / "guide/deep/faq.md").resolve().as_uri()}]}
    assert plan.folder_fields == {"tags": ["t"]}
    assert [p.name for p in plan.skipped] == ["logo.png"]


def test_bad_file_is_reported_not_fatal(tmp_path):
    write(tmp_path / "ok.md", "# OK")
    write(tmp_path / "bad.md", b"\xff\xfe\xfa not utf-8")

    plan = plan_folder(tmp_path)

    assert [n.path for n in plan.nodes] == ["", "ok.md"]
    assert [p.name for p, _ in plan.failed] == ["bad.md"]
    assert "UnicodeDecodeError" in plan.failed[0][1]


def test_custom_processor_is_picked_up(tmp_path):
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

    plan = plan_folder(tmp_path, registry=registry)

    assert [p.name for p in plan.skipped] == ["skip.md"]
    (note,) = plan.nodes[1:]
    assert (note.title, note.content, note.fields["description"]) == ("NOTE", "plain", "plain text")


def test_plan_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError):
        plan_folder(tmp_path / "missing")


# --------------------------------------------------------------------------
# Converted formats (LangChain loaders) + retained originals (no DB)
# --------------------------------------------------------------------------


def make_pdf(path: Path) -> Path:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Quarterly Report", fontsize=24)
    page.insert_text((72, 120), "The widget count rose sharply.")
    doc.new_page().insert_text((72, 72), "Second page text.")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def test_loader_processor_joins_docs_and_titles(tmp_path):
    from langchain_core.documents import Document

    from kb.ingest import LoaderProcessor

    class FakeLoader:
        def __init__(self, path):
            self.path = path

        def load(self):
            return [Document(page_content="intro\n# Big Title\n"), Document(page_content="  "), Document(page_content="more")]

    proc = LoaderProcessor("fake", {".FAKE"}, FakeLoader)
    assert proc.extensions == frozenset({".fake"})
    doc = proc.process(tmp_path / "x.fake")
    assert (doc.title, doc.content, doc.extra) == ("x", "intro\n# Big Title\n\nmore\n", {})

    empty = LoaderProcessor("empty", {".e"}, lambda p: type("L", (), {"load": lambda self: []})())
    with pytest.raises(ValueError, match="no text"):
        empty.process(tmp_path / "x.e")


def test_pdf_plan_records_original_without_uploading(tmp_path):
    import hashlib

    pdf = make_pdf(tmp_path / "docs" / "report.pdf")
    write(tmp_path / "docs" / "notes.md", "# Notes")

    plan = plan_folder(tmp_path / "docs")  # no blob store involved: planning does no network I/O

    nodes = {n.path: n for n in plan.nodes}
    report = nodes["report.pdf"]
    assert report.title == "report"
    assert "widget count rose sharply" in report.content and "Second page text" in report.content
    assert report.original.path == pdf and report.original.mime == "application/pdf"
    assert report.original.size == pdf.stat().st_size
    assert report.original.sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert not any(k.startswith("blob_") for k in report.fields)  # set by ingest_folder, with a store
    assert nodes["notes.md"].original is None  # markdown: content is the original
    assert report.id is not None and nodes["notes.md"].id is not None  # ids chosen up front


def test_corrupt_pdf_is_reported_failed(tmp_path):
    write(tmp_path / "broken.pdf", b"definitely not a pdf")
    write(tmp_path / "ok.md", "# OK")

    plan = plan_folder(tmp_path)

    assert [n.path for n in plan.nodes] == ["", "ok.md"]
    assert [p.name for p, _ in plan.failed] == ["broken.pdf"]


def test_converter_routing():
    pytest.importorskip("langchain_docling")
    registry = default_registry()
    assert registry.for_path(Path("x.pdf")).name == "pymupdf4llm"
    for ext in (".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm"):
        assert registry.for_path(Path("x" + ext)).name == "docling"
    for ext in (".png", ".jpg", ".tsv", ".xlsm", ".xls", ".doc"):  # deliberately unsupported
        assert registry.for_path(Path("x" + ext)) is None


def docling_plan(tmp_path: Path, name: str):
    pytest.importorskip("langchain_docling")
    plan = plan_folder(tmp_path)
    assert not plan.failed, plan.failed
    return {n.path: n for n in plan.nodes}[name]


def test_docling_converts_html(tmp_path):
    write(tmp_path / "page.html", "<html><body><h1>Release Notes</h1><p>Widgets got faster.</p></body></html>")
    node = docling_plan(tmp_path, "page.html")
    assert node.title == "page"
    assert "Widgets got faster." in node.content


def test_docling_converts_csv_to_table(tmp_path):
    write(tmp_path / "sales.csv", "region,units\nnorth,10\nsouth,20\n")
    node = docling_plan(tmp_path, "sales.csv")
    assert "north" in node.content and "20" in node.content
    assert "|" in node.content  # rendered as a markdown table


def test_docling_converts_docx(tmp_path):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading("Design Memo", level=1)
    doc.add_paragraph("The gadget ships in March.")
    doc.save(str(tmp_path / "memo.docx"))
    node = docling_plan(tmp_path, "memo.docx")
    assert node.title == "memo"
    assert "The gadget ships in March." in node.content


def test_docling_converts_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["fruit", "count"])
    wb.active.append(["apple", 3])
    wb.save(str(tmp_path / "stock.xlsx"))
    node = docling_plan(tmp_path, "stock.xlsx")
    assert "apple" in node.content and "fruit" in node.content


def test_docling_records_original(tmp_path):
    pytest.importorskip("langchain_docling")
    html = write(tmp_path / "page.html", "<html><body><h1>T</h1><p>body</p></body></html>")

    plan = plan_folder(tmp_path)

    assert not plan.failed, plan.failed
    node = {n.path: n for n in plan.nodes}["page.html"]
    assert node.original.path == html and node.original.mime == "text/html"


# --------------------------------------------------------------------------
# Parallel conversion (kb.ingest.convert, no DB)
# --------------------------------------------------------------------------

# Module-level so they pickle into the pool's (spawned) workers.


class PidProcessor:
    """Records which process converted the file; `.boom` files raise, `.die` kills the worker."""

    name = "pid"
    extensions = frozenset({".pid", ".boom", ".die"})
    cpu_bound = True

    def process(self, path):
        import os

        if path.suffix == ".boom":
            raise RuntimeError("bad input")
        if path.suffix == ".die":
            os._exit(1)
        return ProcessedDocument(title=path.stem, content=f"{os.getpid()}\n")


def pid_registry():
    registry = ProcessorRegistry()
    registry.register(PidProcessor())
    registry.register(MarkdownProcessor())
    return registry


def test_cpu_bound_files_convert_in_worker_processes_in_order(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("KB_INGEST_WORKERS", "2")
    for i in range(6):
        write(tmp_path / f"{i}.pid", "")
    write(tmp_path / "a.md", "# md")
    write(tmp_path / "x.boom", "")

    plan = plan_folder(tmp_path, registry=pid_registry())

    assert [n.path for n in plan.nodes] == ["", "0.pid", "1.pid", "2.pid", "3.pid", "4.pid", "5.pid", "a.md"]
    pids = {int(n.content) for n in plan.nodes[1:7]}
    assert os.getpid() not in pids  # converted in the pool
    assert plan.nodes[7].content == "# md"  # markdown: in-process
    assert plan.failed == [(tmp_path.resolve() / "x.boom", "RuntimeError: bad input")]


def test_one_worker_converts_in_process(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("KB_INGEST_WORKERS", "1")
    for i in range(3):
        write(tmp_path / f"{i}.pid", "")

    plan = plan_folder(tmp_path, registry=pid_registry())

    assert {int(n.content) for n in plan.nodes[1:]} == {os.getpid()}


def test_unpicklable_processor_runs_in_process(tmp_path, monkeypatch):
    import os

    class Local(PidProcessor):  # a local class can't be pickled
        pass

    monkeypatch.setenv("KB_INGEST_WORKERS", "2")
    registry = ProcessorRegistry()
    registry.register(Local())
    for i in range(3):
        write(tmp_path / f"{i}.pid", "")

    plan = plan_folder(tmp_path, registry=registry)

    assert {int(n.content) for n in plan.nodes[1:]} == {os.getpid()}


def test_crashed_worker_fails_the_plan_and_the_pool_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_INGEST_WORKERS", "2")
    crash = tmp_path / "crash"
    write(crash / "a.pid", "")
    write(crash / "b.die", "")

    plan = plan_folder(crash, registry=pid_registry())

    assert "b.die" in {p.name for p, _ in plan.failed}
    assert all("crashed" in error for _, error in plan.failed)

    ok = tmp_path / "ok"
    write(ok / "a.pid", "")
    write(ok / "b.pid", "")
    plan = plan_folder(ok, registry=pid_registry())
    assert not plan.failed and len(plan.nodes) == 3


def test_builtin_converters_pickle():
    import pickle

    registry = default_registry()
    for name in ("x.pdf", "x.docx"):
        processor = registry.for_path(Path(name))
        assert processor.cpu_bound
        assert pickle.loads(pickle.dumps(processor)).name == processor.name


# --------------------------------------------------------------------------
# Materialize + walker (needs TEST_DATABASE_URL)
# --------------------------------------------------------------------------


@pytest.fixture
def db_session(migrated_db):
    from kb.storage.db import SessionLocal

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

    root = children_by_title(db_session, None)["docs"]
    assert root.id == report.root_id and root.node_type == "folder"
    top = children_by_title(db_session, root.id)
    assert set(top) == {"readme", "guide"}
    guide = children_by_title(db_session, top["guide"].id)
    assert set(guide) == {"setup", "deep"}
    faq = children_by_title(db_session, guide["deep"].id)["faq"]
    assert faq.content == "no heading"
    assert faq.tags == ["run:1"]
    assert faq.sources == [{"resource": (src / "guide" / "deep" / "faq.md").resolve().as_uri()}]


def test_materialize_creates_implied_folders_once(db_session):
    created = materialize(
        db_session,
        [
            PlannedNode("", "folder", title="Root"),
            PlannedNode("a/b/one.md", content="1"),
            PlannedNode("a/b/two.md", content="2", fields={"tags": ["x"]}),
            PlannedNode("a", "folder", fields={"description": "explainer"}),  # explicit, listed after its child
            PlannedNode("empty", "folder"),  # explicit folders are kept even with no children
        ],
        folder_fields={"tags": ["implied"]},
    )

    assert list(created) == ["", "a", "a/b", "a/b/one.md", "a/b/two.md", "empty"]
    assert created[""].title == "Root" and created[""].parent_id is None
    assert (created["a"].description, created["a"].tags) == ("explainer", [])
    assert (created["a/b"].title, created["a/b"].node_type, created["a/b"].tags) == ("b", "folder", ["implied"])
    assert created["a/b"].kind == "manual"
    assert created["a/b/two.md"].parent_id == created["a/b"].id
    assert created["a/b/two.md"].title == "two.md" and created["a/b/two.md"].tags == ["x"]


def test_materialize_rejects_bad_plans(db_session):
    with pytest.raises(ValueError, match="no root"):
        materialize(db_session, [PlannedNode("a.md", content="x")])
    with pytest.raises(ValueError, match="twice"):
        materialize(db_session, [PlannedNode("", "folder", title="r"), PlannedNode("", "folder", title="r")])
    with pytest.raises(ValueError, match="must be a folder"):
        materialize(db_session, [PlannedNode("", content="x")])
    with pytest.raises(ValueError, match="folders have no content"):
        materialize(db_session, [PlannedNode("", "folder", content="x")])
    with pytest.raises(ValueError, match="file has no content"):
        materialize(db_session, [PlannedNode("", "folder"), PlannedNode("a.md")])


def test_parent_must_be_an_existing_folder(db_session, tmp_path):
    from kb import service

    folder = service.create_folder(db_session, parent_id=None, title="f")
    note = service.create_file(db_session, parent_id=folder.id, title="n", content="x")
    with pytest.raises(ValueError):
        ingest_folder(db_session, tmp_path, parent_id=note.id)

    report = ingest_folder(db_session, tmp_path, parent_id=folder.id)
    assert service.get_node(db_session, report.root_id).parent_id == folder.id


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
        ["a.md"],  # loose files with no parent folder (the KB root holds folders only)
        ["docs/a.md", "other/b.md"],  # two top-level folders
        ["a.md", "docs/b.md"],  # loose files and a folder mixed
        ["docs/a.md", "docs/a.md"],  # duplicate
        ["docs/Notes.md", "docs/notes.md"],  # differ only in case: would overwrite on macOS
        ["docs/a", "docs/a/b.md"],  # "docs/a" both a file and a directory
        ["docs/A/b.md", "docs/a"],  # same, across case and order
        [],
    ],
)
def test_upload_rejects_bad_paths(paths):
    from kb.ingest import UploadError, ingest_upload

    with pytest.raises(UploadError):
        ingest_upload(None, [(p, b"# x") for p in paths])  # rejected before any DB use


def test_upload_rejects_loose_case_collision():
    from kb.ingest import UploadError, ingest_upload

    with pytest.raises(UploadError, match="duplicate"):
        ingest_upload(None, [("a.md", b"# x"), ("A.md", b"# y")], parent_id=uuid.uuid4())


def test_upload_mirrors_tree(db_session):
    from kb.ingest import ingest_upload

    report = ingest_upload(
        db_session,
        [
            ("docs/readme.md", b"# Readme"),
            ("docs/guide/setup.md", b"# Setup"),
            ("docs/img/logo.png", b"\x89PNG"),
        ],
        tags=["up"],
    )

    assert [p.as_posix() for p in report.skipped] == ["docs/img/logo.png"]
    root = children_by_title(db_session, None)["docs"]
    assert root.id == report.root_id
    guide = children_by_title(db_session, children_by_title(db_session, root.id)["guide"].id)
    assert guide["setup"].sources == [{"resource": "upload:docs/guide/setup.md"}]
    assert guide["setup"].tags == ["up"]


def test_upload_with_a_bad_file_ingests_nothing(db_session):
    from kb.ingest import IngestFailed, ingest_upload

    with pytest.raises(IngestFailed) as exc:
        ingest_upload(
            db_session,
            [
                ("bad/readme.md", b"# Readme"),
                ("bad/x.md", b"\xff\xfe not utf-8"),
                ("bad/y.md", b"\xff\xfe nor this"),
            ],
        )
    # every failure is listed (not just the first), with upload paths
    assert [p.as_posix() for p, _ in exc.value.failures] == ["bad/x.md", "bad/y.md"]
    assert "bad" not in children_by_title(db_session, None)


def test_upload_loose_files_go_into_parent(db_session):
    from kb import service
    from kb.ingest import ingest_upload

    parent = service.create_folder(db_session, parent_id=None, title="inbox")
    service.create_file(db_session, parent_id=parent.id, title="Existing", content="x")

    report = ingest_upload(
        db_session,
        [("a.md", b"# A"), ("logo.png", b"\x89PNG")],
        parent_id=parent.id,
        tags=["up"],
    )

    assert report.root_id == parent.id
    assert report.folders_created == [] and len(report.files_created) == 1
    assert [p.as_posix() for p in report.skipped] == ["logo.png"]
    children = children_by_title(db_session, parent.id)
    assert set(children) == {"Existing", "a"}
    assert children["a"].sources == [{"resource": "upload:a.md"}] and children["a"].tags == ["up"]
    assert service.get_folder(db_session, parent.id).tags == []  # the parent isn't retagged


def test_upload_reports_hidden_files_as_skipped(db_session):
    from kb.ingest import ingest_upload

    report = ingest_upload(
        db_session,
        [("docs/a.md", b"# A"), ("docs/.env", b"SECRET=1"), ("docs/.git/notes.md", b"# Hidden")],
    )

    assert sorted(p.as_posix() for p in report.skipped) == ["docs/.env", "docs/.git/notes.md"]
    assert len(report.files_created) == 1 and report.folders_created == [report.root_id]
    assert set(children_by_title(db_session, report.root_id)) == {"a"}


def test_upload_streams_file_objects(db_session):
    from kb.ingest import ingest_upload

    report = ingest_upload(db_session, [("docs/a.md", io.BytesIO(b"# A\n\nbody"))])

    node = children_by_title(db_session, report.root_id)["a"]
    assert node.content == "# A\n\nbody"


@pytest.mark.parametrize(
    "limits, files",
    [
        (dict(max_files=1), [("docs/a.md", b"# A"), ("docs/b.md", b"# B")]),
        (dict(max_file_bytes=3), [("docs/a.md", b"# AB")]),
        (dict(max_file_bytes=3), [("docs/a.md", io.BytesIO(b"# AB"))]),
        (dict(max_total_bytes=5), [("docs/a.md", b"# A"), ("docs/b.md", b"# B")]),
    ],
)
def test_upload_limits(limits, files):
    from kb.ingest import UploadLimits, UploadTooLarge, ingest_upload

    with pytest.raises(UploadTooLarge):
        ingest_upload(None, files, limits=UploadLimits(**limits))  # never reaches the DB


def test_upload_limits_within_caps(db_session):
    from kb.ingest import UploadLimits, ingest_upload

    files = [("docs/a.md", b"# A"), ("docs/b.md", b"# B")]
    report = ingest_upload(db_session, files, limits=UploadLimits(max_files=2, max_file_bytes=3, max_total_bytes=6))
    assert len(report.files_created) == 2


def test_upload_limits_from_env(monkeypatch):
    from kb.ingest import UploadLimits

    for name in ("MAX_FILES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES"):
        monkeypatch.delenv(f"KB_UPLOAD_{name}", raising=False)
    assert UploadLimits.from_env() == UploadLimits(**UploadLimits.DEFAULTS)

    monkeypatch.setenv("KB_UPLOAD_MAX_FILES", "7")
    monkeypatch.setenv("KB_UPLOAD_MAX_FILE_BYTES", "0")  # 0 = no cap
    monkeypatch.setenv("KB_UPLOAD_MAX_TOTAL_BYTES", "")  # empty = default
    assert UploadLimits.from_env() == UploadLimits(
        max_files=7, max_file_bytes=None, max_total_bytes=UploadLimits.DEFAULTS["max_total_bytes"]
    )


@pytest.fixture
def client(db_session, monkeypatch):
    from fastapi.testclient import TestClient

    from kb.api import app, get_session


    app.dependency_overrides[get_session] = lambda: db_session  # rolled back by db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# PDF ingestion end to end (DB)
# --------------------------------------------------------------------------


def test_ingest_pdf_sets_blob_columns(db_session, tmp_path, blob_store):
    from kb import service

    pdf = make_pdf(tmp_path / "papers" / "report.pdf")

    report = ingest_folder(db_session, tmp_path / "papers", blob_store=blob_store)

    assert len(report.files_created) == 1
    node = service.get_node(db_session, report.files_created[0])
    assert node.title == "report" and "widget count rose sharply" in node.content
    assert node.sources == [{"resource": pdf.resolve().as_uri()}]
    assert (node.blob_mime_type, node.blob_size_bytes) == ("application/pdf", pdf.stat().st_size)
    assert node.blob_key == f"originals/{node.id}" and node.blob_checksum.startswith("sha256:")
    assert blob_store.get_original(node.blob_key).read() == pdf.read_bytes()


def test_ingest_pdf_without_blob_store_leaves_blob_null(db_session, tmp_path):
    from kb import service

    make_pdf(tmp_path / "papers" / "report.pdf")

    report = ingest_folder(db_session, tmp_path / "papers", blob_store=None)

    node = service.get_node(db_session, report.files_created[0])
    assert "widget count" in node.content
    assert (node.blob_key, node.blob_size_bytes, node.blob_mime_type, node.blob_checksum) == (None,) * 4


def test_ingest_uses_configured_blob_store_by_default(db_session, tmp_path, blob_store):
    from kb import service
    from kb.storage import blobs

    blobs.set_blob_store(blob_store)
    try:
        make_pdf(tmp_path / "papers" / "report.pdf")
        report = ingest_folder(db_session, tmp_path / "papers")
    finally:
        blobs.reset_blob_store()
    node = service.get_node(db_session, report.files_created[0])
    assert blob_store.get_original(node.blob_key) is not None


def test_upload_pdf_keeps_original(db_session, tmp_path, blob_store):
    from kb import service
    from kb.ingest import ingest_upload

    data = make_pdf(tmp_path / "report.pdf").read_bytes()

    report = ingest_upload(db_session, [("docs/report.pdf", data)], blob_store=blob_store)

    node = service.get_node(db_session, report.files_created[0])
    assert node.sources == [{"resource": "upload:docs/report.pdf"}]
    assert node.blob_mime_type == "application/pdf"
    assert blob_store.get_original(node.blob_key).read() == data


def test_raw_original_endpoint(client, db_session, tmp_path, blob_store):
    from kb.storage import blobs

    pdf = make_pdf(tmp_path / "papers" / "report.pdf")
    (tmp_path / "papers" / "notes.md").write_text("# Notes\n")
    blobs.set_blob_store(blob_store)
    try:
        report = ingest_folder(db_session, tmp_path / "papers")
        by_title = children_by_title(db_session, report.root_id)
        pdf_id, md_id = by_title["report"].id, by_title["notes"].id

        res = client.get(f"/nodes/{pdf_id}/raw")
        assert res.status_code == 200 and res.content == pdf.read_bytes()
        assert res.headers["content-type"] == "application/pdf"
        assert 'filename="report.pdf"' in res.headers["content-disposition"]
        assert client.get(f"/nodes/{pdf_id}").json()["blob_mime_type"] == "application/pdf"

        # A non-Latin name (Latin-1-only headers used to make this a 500, #16).
        hebrew = make_pdf(tmp_path / "hebrew" / "דוח.pdf")
        hebrew_id = ingest_folder(db_session, tmp_path / "hebrew").files_created[0]
        res = client.get(f"/nodes/{hebrew_id}/raw")
        assert res.status_code == 200 and res.content == hebrew.read_bytes()
        assert res.headers["content-disposition"] == "attachment; filename*=utf-8''%D7%93%D7%95%D7%97.pdf"

        assert client.get(f"/nodes/{md_id}/raw").status_code == 404  # markdown keeps no original
        assert client.get(f"/nodes/{uuid.uuid4()}/raw").status_code == 404

        # An unreachable/refusing store is an upstream failure (502), not "no original" (404).
        blobs.set_blob_store(blobs.BlobStore("no-such-bucket", client=blob_store.client))
        res = client.get(f"/nodes/{pdf_id}/raw")
        assert res.status_code == 502 and "blob store unavailable" in res.json()["detail"]
    finally:
        blobs.reset_blob_store()


def test_health(client, blob_store, monkeypatch):
    from kb.storage import blobs

    monkeypatch.delenv("BLOB_REQUIRED", raising=False)
    try:
        blobs.set_blob_store(blob_store)
        res = client.get("/health")
        assert res.status_code == 200 and res.json() == {"db": "ok", "blob_store": "ok"}

        blobs.set_blob_store(None)
        assert client.get("/health").json()["blob_store"] == "not configured"

        blobs.set_blob_store(blobs.BlobStore("no-such-bucket", client=blob_store.client))
        res = client.get("/health")
        assert res.status_code == 503 and "no such bucket" in res.json()["blob_store"]
    finally:
        blobs.reset_blob_store()


def test_startup_fails_when_required_store_is_broken(blob_store, monkeypatch):
    from fastapi.testclient import TestClient

    from kb.api import app
    from kb.storage import blobs

    try:
        blobs.set_blob_store(blobs.BlobStore("no-such-bucket", client=blob_store.client))
        monkeypatch.setenv("BLOB_REQUIRED", "1")
        with pytest.raises(blobs.BlobStoreError):
            with TestClient(app):  # entering runs the lifespan startup check
                pass

        monkeypatch.delenv("BLOB_REQUIRED")
        with TestClient(app) as c:  # not required: logged, startup continues
            assert c.get("/ingest/extensions").status_code == 200
    finally:
        blobs.reset_blob_store()


def test_ingest_endpoint(client, db_session):
    extensions = client.get("/ingest/extensions").json()["extensions"]
    assert extensions == sorted(default_registry().supported_extensions)
    assert {".markdown", ".md", ".pdf"} <= set(extensions)

    res = client.post(
        "/ingest",
        files=[("files", ("a.md", b"# A", "text/markdown")), ("files", ("p.png", b"x", "image/png"))],
        data={"paths": ["notes/sub/a.md", "notes/p.png"], "tags": ["t1", "t2"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["files_created"]) == 1 and len(body["folders_created"]) == 2
    assert body["skipped"] == ["notes/p.png"] and "failed" not in body
    assert client.get(f"/nodes/{body['root_id']}").json()["tags"] == ["t1", "t2"]

    bad = client.post("/ingest", files=[("files", ("a.md", b"x"))], data={"paths": ["../a.md"]})
    assert bad.status_code == 422
    mismatch = client.post("/ingest", files=[("files", ("a.md", b"x"))], data={"paths": ["n/a.md", "n/b.md"]})
    assert mismatch.status_code == 422

    loose = client.post(
        "/ingest", files=[("files", ("c.md", b"# C"))], data={"paths": ["c.md"], "parent_id": body["root_id"]}
    )
    assert loose.status_code == 201, loose.text
    assert loose.json()["root_id"] == body["root_id"] and loose.json()["folders_created"] == []
    assert client.post("/ingest", files=[("files", ("c.md", b"# C"))], data={"paths": ["c.md"]}).status_code == 422


def test_ingest_endpoint_rejects_collisions_and_creates_nothing(client, db_session):
    for paths in (["n/a.md", "n/a.md"], ["n/a", "n/a/b.md"]):
        res = client.post(
            "/ingest",
            files=[("files", ("a.md", b"# A")), ("files", ("b.md", b"# B"))],
            data={"paths": paths},
        )
        assert res.status_code == 422, res.text
    assert "n" not in children_by_title(db_session, None)


def test_ingest_endpoint_limits(client, db_session, monkeypatch):
    files = [("files", ("a.md", b"# A")), ("files", ("b.md", b"# B"))]
    data = {"paths": ["n/a.md", "n/b.md"]}

    monkeypatch.setenv("KB_UPLOAD_MAX_FILES", "1")
    res = client.post("/ingest", files=files, data=data)
    assert res.status_code == 413 and "limit" in res.json()["detail"]

    monkeypatch.setenv("KB_UPLOAD_MAX_FILES", "")
    monkeypatch.setenv("KB_UPLOAD_MAX_FILE_BYTES", "2")
    assert client.post("/ingest", files=files, data=data).status_code == 413
    assert "n" not in children_by_title(db_session, None)

    monkeypatch.setenv("KB_UPLOAD_MAX_FILE_BYTES", "")
    res = client.post("/ingest", files=files + [("files", (".env", b"x"))], data={"paths": [*data["paths"], "n/.env"]})
    assert res.status_code == 201, res.text
    assert res.json()["skipped"] == ["n/.env"]
