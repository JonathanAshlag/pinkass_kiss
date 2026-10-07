"""
REST API for the KB service layer. Thin: every route calls into kb.service and
shapes the result with kb.api.schemas -- no business logic lives here.

Run locally: `uvicorn kb.api:app --reload` (needs DATABASE_URL in the environment,
same as Alembic).
"""

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from kb import service
from kb.storage import blobs
from kb.storage.db import SessionLocal
from kb.retrieval.dci import DEFAULT_MAX_CHARS, DEFAULT_READ_LIMIT, MAX_CHARS_LIMIT
from kb.ingest import UploadError, UploadLimits, UploadTooLarge, default_registry, ingest_upload
from kb.api.schemas import (
    IngestExtensionsRead,
    IngestFailure,
    IndexFailure,
    IndexResultRead,
    IngestReportRead,
    ManifestCreate,
    ManifestMemberCreate,
    ManifestMemberRead,
    ManifestRead,
    MoveRequest,
    NodeCreate,
    NodeRead,
    NodeSummary,
    NodeUpdate,
    ReindexRequest,
    SearchHitRead,
    ToolOutputRead,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Checks the blob store once at startup: with BLOB_REQUIRED=1 a missing or broken
    store aborts startup; otherwise a broken one is only logged."""
    try:
        status = blobs.check_blob_store()
        log.info("blob store: %s", status)
    except blobs.BlobStoreError as exc:
        if blobs.blob_required():
            raise
        log.error("blob store check failed (originals may not be kept): %s", exc)
    yield


app = FastAPI(title="pinkass_kiss KB API", lifespan=lifespan)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.exception_handler(service.TreeCycleError)
@app.exception_handler(service.ManifestCycleError)
def _cycle_error_handler(request, exc):
    return _json_error(409, str(exc))


@app.exception_handler(service.PatternError)
def _pattern_error_handler(request, exc):
    return _json_error(422, str(exc))


@app.exception_handler(UploadTooLarge)
def _too_large_handler(request, exc):
    # An UploadError subclass; Starlette picks the most specific handler.
    return _json_error(413, str(exc))


@app.exception_handler(UploadError)
@app.exception_handler(service.FieldError)
def _unprocessable_handler(request, exc):
    # Both subclass ValueError; Starlette picks the most specific handler.
    return _json_error(422, str(exc))


@app.exception_handler(service.PermissionDenied)
def _permission_denied_handler(request, exc):
    return _json_error(403, str(exc))


@app.exception_handler(ValueError)
def _value_error_handler(request, exc):
    # dal.py/dci.py only raise plain ValueError for "no such node/manifest/path" --
    # shape-validation (add_manifest_member's exactly-one-of check, dci's
    # max_chars/offset/limit/context bounds) is caught earlier by Pydantic/Query
    # constraints and never reaches here.
    return _json_error(404, str(exc))


@app.exception_handler(blobs.BlobStoreError)
def _blob_store_error_handler(request, exc):
    # S3 unreachable / refusing: an upstream failure, not a missing node.
    log.error("blob store: %s", exc)
    return _json_error(502, f"blob store unavailable: {exc}")


def _json_error(status_code: int, detail: str):
    return JSONResponse(status_code=status_code, content={"detail": detail})


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@app.get("/health")
def health():
    """DB and blob-store status; 200 when everything is usable, 503 otherwise. A blob
    store that isn't configured counts as fine unless BLOB_REQUIRED is set."""
    checks: dict[str, str] = {}
    try:
        with SessionLocal() as s:
            s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 -- report, don't raise
        checks["db"] = f"error: {type(exc).__name__}: {exc}"
    try:
        checks["blob_store"] = blobs.check_blob_store()
    except blobs.BlobStoreError as exc:
        checks["blob_store"] = f"error: {exc}"
    healthy = all(v in ("ok", "not configured") for v in checks.values())
    return JSONResponse(status_code=200 if healthy else 503, content=checks)


# --------------------------------------------------------------------------
# Committing writes (kb.service.commit keeps the semantic index in step)
# --------------------------------------------------------------------------


def _commit(session: Session, background_tasks: BackgroundTasks) -> None:
    """Commit the request's writes *now* via service.commit, which then schedules
    indexing of whatever they touched as a background task (if KB_AUTO_INDEX is on).

    The explicit commit is what guarantees the indexer (which opens its own session)
    sees the new state. FastAPI 0.115 happens to close yield-dependencies -- and so run
    get_session's commit -- before background tasks, but that ordering has changed
    between FastAPI releases, so it isn't relied on. get_session's own commit is then
    a harmless no-op. If the commit raises, nothing is scheduled.
    """
    service.commit(session, schedule=background_tasks.add_task)


def _read(session: Session, node) -> NodeRead:
    data = NodeRead.model_validate(node)
    data.warnings = service.get_warnings(session, node)
    return data


# --------------------------------------------------------------------------
# Nodes (files and folders)
# --------------------------------------------------------------------------


@app.post("/nodes", response_model=NodeRead, status_code=201)
def create_node(
    body: NodeCreate, background_tasks: BackgroundTasks, session: Session = Depends(get_session)
):
    # exclude_unset lets DB server defaults (kind='manual', tags='{}', status='draft',
    # ...) apply when the client omits those fields.
    payload = body.model_dump(exclude_unset=True)
    node_type = payload.pop("type", body.type)
    payload.setdefault("parent_id", body.parent_id)
    if node_type == "folder":
        node = service.create_folder(session, **payload)
    else:
        node = service.create_file(session, **payload)
    _commit(session, background_tasks)
    return _read(session, node)


@app.get("/nodes/roots", response_model=list[NodeSummary])
def list_roots(include_deleted: bool = False, session: Session = Depends(get_session)):
    return service.list_children(session, None, include_deleted=include_deleted)


@app.get("/nodes", response_model=list[NodeSummary])
def query_nodes(
    tags: list[str] | None = Query(None),
    status: str | None = None,
    type: str | None = Query(None, pattern="^(file|folder)$"),
    kind: str | None = None,
    parent_id: uuid.UUID | None = None,
    include_deleted: bool = False,
    session: Session = Depends(get_session),
):
    return service.query_metadata(
        session,
        tags=tags,
        status=status,
        node_type=type,
        kind=kind,
        parent_id=parent_id,
        include_deleted=include_deleted,
    )


@app.get("/nodes/{node_id}", response_model=NodeRead)
def get_node(node_id: uuid.UUID, session: Session = Depends(get_session)):
    node = service.get_node(session, node_id)
    if node is None:
        raise HTTPException(404, f"no such node: {node_id}")
    return _read(session, node)


@app.get("/nodes/{node_id}/raw", response_class=Response)
def get_node_raw(node_id: uuid.UUID, session: Session = Depends(get_session)):
    """The retained original (e.g. the uploaded PDF), 404 if the node has none."""
    original = service.get_original(session, node_id)
    if original is None:
        raise HTTPException(404, f"node {node_id} has no retained original")
    data, mime, filename = original
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.patch("/nodes/{node_id}", response_model=NodeRead)
def update_node(
    node_id: uuid.UUID,
    body: NodeUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    node = service.update_node(session, node_id, **body.model_dump(exclude_unset=True))
    _commit(session, background_tasks)
    return _read(session, node)


@app.post("/nodes/{node_id}/move", response_model=NodeRead)
def move_node(node_id: uuid.UUID, body: MoveRequest, session: Session = Depends(get_session)):
    node = service.move_node(session, node_id, body.new_parent_id)
    return _read(session, node)


@app.post("/nodes/{node_id}/restore", response_model=NodeRead)
def restore_node(
    node_id: uuid.UUID, background_tasks: BackgroundTasks, session: Session = Depends(get_session)
):
    node = service.restore_node(session, node_id)
    _commit(session, background_tasks)
    return _read(session, node)


@app.delete("/nodes/{node_id}", status_code=204, response_class=Response)
def delete_node(
    node_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    cascade: bool = True,
    session: Session = Depends(get_session),
):
    service.delete_node(session, node_id, cascade=cascade)
    _commit(session, background_tasks)


@app.get("/nodes/{node_id}/children", response_model=list[NodeSummary])
def list_children(
    node_id: uuid.UUID, include_deleted: bool = False, session: Session = Depends(get_session)
):
    return service.list_children(session, node_id, include_deleted=include_deleted)


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


@app.post("/manifests", response_model=ManifestRead, status_code=201)
def create_manifest(body: ManifestCreate, session: Session = Depends(get_session)):
    return service.create_manifest(session, body.name, body.description)


@app.get("/manifests", response_model=list[ManifestRead])
def list_manifests(include_deleted: bool = False, session: Session = Depends(get_session)):
    return service.list_manifests(session, include_deleted=include_deleted)


@app.get("/manifests/{manifest_id}", response_model=ManifestRead)
def get_manifest(manifest_id: uuid.UUID, session: Session = Depends(get_session)):
    manifest = service.get_manifest(session, manifest_id)
    if manifest is None:
        raise HTTPException(404, f"no such manifest: {manifest_id}")
    return manifest


@app.get("/manifests/{manifest_id}/members", response_model=list[ManifestMemberRead])
def list_manifest_members(manifest_id: uuid.UUID, session: Session = Depends(get_session)):
    if service.get_manifest(session, manifest_id) is None:
        raise HTTPException(404, f"no such manifest: {manifest_id}")
    return service.list_manifest_members(session, manifest_id)


@app.post("/manifests/{manifest_id}/members", status_code=201)
def add_manifest_member(
    manifest_id: uuid.UUID, body: ManifestMemberCreate, session: Session = Depends(get_session)
):
    service.add_manifest_member(
        session, manifest_id, node_id=body.node_id, child_manifest_id=body.child_manifest_id
    )
    return Response(status_code=201)


@app.delete("/manifests/{manifest_id}/members", status_code=204, response_class=Response)
def remove_manifest_member(
    manifest_id: uuid.UUID,
    node_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
):
    service.remove_manifest_member(
        session, manifest_id, node_id=node_id, child_manifest_id=child_manifest_id
    )


@app.get("/manifests/{manifest_id}/resolve", response_model=list[NodeSummary])
def resolve_manifest(manifest_id: uuid.UUID, session: Session = Depends(get_session)):
    return list(service.resolve_manifest(session, manifest_id))


# --------------------------------------------------------------------------
# Direct corpus interaction (agent-facing ls/grep/read over a manifest)
# --------------------------------------------------------------------------

MaxChars = Query(DEFAULT_MAX_CHARS, ge=1, le=MAX_CHARS_LIMIT)


@app.get("/manifests/{manifest_id}/paths", response_model=ToolOutputRead)
def list_paths(
    manifest_id: uuid.UUID,
    under: str | None = None,
    recursive: bool = False,
    max_chars: int = MaxChars,
    session: Session = Depends(get_session),
):
    return service.list_paths(
        session, manifest_id, under=under, recursive=recursive, max_chars=max_chars
    )


@app.get("/manifests/{manifest_id}/search", response_model=ToolOutputRead)
def search_lines(
    manifest_id: uuid.UUID,
    pattern: list[str] = Query(..., min_length=1),
    path: list[str] | None = Query(None),
    ignore_case: bool = False,
    context: int = Query(0, ge=0),
    files_only: bool = False,
    max_chars: int = MaxChars,
    session: Session = Depends(get_session),
):
    return service.search_lines(
        session,
        manifest_id,
        pattern,
        paths=path,
        ignore_case=ignore_case,
        context=context,
        files_only=files_only,
        max_chars=max_chars,
    )


@app.get("/manifests/{manifest_id}/read", response_model=ToolOutputRead)
def read_lines(
    manifest_id: uuid.UUID,
    path: str,
    offset: int = Query(1, ge=1),
    limit: int = Query(DEFAULT_READ_LIMIT, ge=1),
    max_chars: int = MaxChars,
    session: Session = Depends(get_session),
):
    return service.read_lines(
        session, manifest_id, path, offset=offset, limit=limit, max_chars=max_chars
    )


@app.get("/manifests/{manifest_id}/semantic", response_model=list[SearchHitRead])
def semantic_search(
    manifest_id: uuid.UUID,
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=100),
    tags: list[str] | None = Query(None),
    status: str | None = None,
    session: Session = Depends(get_session),
):
    """Embedding search over the manifest's indexed chunks, best first. A sync route on
    purpose: the vector store's sync API must not run inside the event loop."""
    return service.semantic_search(session, manifest_id, q, k=k, tags=tags, status=status)


# --------------------------------------------------------------------------
# Semantic index maintenance
# --------------------------------------------------------------------------


@app.post("/index/reindex", response_model=IndexResultRead)
def reindex(body: ReindexRequest | None = Body(None)):
    """(Re)index the given node ids, or the whole KB when `file_ids` is omitted. Runs
    synchronously and returns the counts; ignores KB_AUTO_INDEX (it's an explicit ask)."""
    if body is None or body.file_ids is None:
        result = service.reindex_all()
    else:
        result = service.index_files(body.file_ids)
    return IndexResultRead(
        num_added=result.num_added,
        num_updated=result.num_updated,
        num_skipped=result.num_skipped,
        num_deleted=result.num_deleted,
        failed=[IndexFailure(file_id=f, error=e) for f, e in result.failed],
    )


# --------------------------------------------------------------------------
# Ingestion (folder upload)
# --------------------------------------------------------------------------


@app.get("/ingest/extensions", response_model=IngestExtensionsRead)
def ingest_extensions():
    """File extensions a processor exists for -- lets clients skip uploading the rest."""
    return IngestExtensionsRead(extensions=sorted(default_registry().supported_extensions))


@app.post("/ingest", response_model=IngestReportRead, status_code=201)
def ingest(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    # Relative path of each file, same order as `files` (e.g. "docs/guide/setup.md").
    # Sent separately because multipart filenames aren't reliably kept with directories.
    paths: list[str] = Form(...),
    parent_id: uuid.UUID | None = Form(None),
    tags: list[str] = Form([]),
    session: Session = Depends(get_session),
):
    if len(files) != len(paths):
        raise UploadError(f"got {len(files)} files but {len(paths)} paths")
    report = ingest_upload(
        session,
        # The spooled files themselves, streamed by ingest_upload -- not .read() whole.
        [(path, upload.file) for path, upload in zip(paths, files)],
        parent_id=parent_id,
        tags=tags,
        limits=UploadLimits.from_env(),
    )
    _commit(session, background_tasks)
    return IngestReportRead(
        root_id=report.root_id,
        files_created=report.files_created,
        folders_created=report.folders_created,
        skipped=[p.as_posix() for p in report.skipped],
        failed=[IngestFailure(path=p.as_posix(), error=e) for p, e in report.failed],
    )


# --------------------------------------------------------------------------
# Dev UI (static, no logic) -- mounted last so it never shadows an API route
# --------------------------------------------------------------------------

app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
