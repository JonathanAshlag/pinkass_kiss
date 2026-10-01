"""
REST API for the KB service layer. Thin: every route calls into kb.service and
shapes the result with kb.schemas -- no business logic lives here.

Run locally: `uvicorn kb.api:app --reload` (needs DATABASE_URL in the environment,
same as Alembic).
"""

import uuid
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from kb import service
from kb.db import SessionLocal
from kb.schemas import (
    FileCreate,
    FileRead,
    FileSummary,
    FileUpdate,
    ManifestCreate,
    ManifestMemberCreate,
    ManifestMemberRead,
    ManifestRead,
    MoveRequest,
)

app = FastAPI(title="pinkass_kiss KB API")


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


@app.exception_handler(ValueError)
def _value_error_handler(request, exc):
    # dal.py only raises plain ValueError for "no such node/manifest: {id}" --
    # shape-validation (e.g. add_manifest_member's exactly-one-of check) is caught
    # earlier by Pydantic and never reaches here.
    return _json_error(404, str(exc))


def _json_error(status_code: int, detail: str):
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _read(session: Session, node) -> FileRead:
    data = FileRead.model_validate(node)
    data.warnings = service.get_warnings(session, node)
    return data


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


@app.post("/files", response_model=FileRead, status_code=201)
def create_file(body: FileCreate, session: Session = Depends(get_session)):
    # exclude_unset lets DB server defaults (aliases='{}', tags='{}', sources='[]',
    # verified='[]', status='draft') apply when the client omits those fields --
    # except `kind`/`parent_id`, which dal.create_file requires explicitly (no
    # server-side fallback in the function signature), so their schema defaults
    # ("file" / None-for-root) are restored if the client left them out.
    payload = body.model_dump(exclude_unset=True)
    payload.setdefault("kind", body.kind)
    payload.setdefault("parent_id", body.parent_id)
    node = service.create_file(session, **payload)
    return _read(session, node)


@app.get("/files/roots", response_model=list[FileSummary])
def list_roots(include_deleted: bool = False, session: Session = Depends(get_session)):
    return service.list_children(session, None, include_deleted=include_deleted)


@app.get("/files", response_model=list[FileSummary])
def query_files(
    tags: list[str] | None = Query(None),
    status: str | None = None,
    parent_id: uuid.UUID | None = None,
    include_deleted: bool = False,
    session: Session = Depends(get_session),
):
    return service.query_metadata(
        session,
        tags=tags,
        status=status,
        parent_id=parent_id,
        include_deleted=include_deleted,
    )


@app.get("/files/{node_id}", response_model=FileRead)
def get_file(node_id: uuid.UUID, session: Session = Depends(get_session)):
    node = service.get_node(session, node_id)
    if node is None:
        raise HTTPException(404, f"no such node: {node_id}")
    return _read(session, node)


@app.patch("/files/{node_id}", response_model=FileRead)
def update_file(node_id: uuid.UUID, body: FileUpdate, session: Session = Depends(get_session)):
    node = service.update_node(session, node_id, **body.model_dump(exclude_unset=True))
    return _read(session, node)


@app.post("/files/{node_id}/move", response_model=FileRead)
def move_file(node_id: uuid.UUID, body: MoveRequest, session: Session = Depends(get_session)):
    node = service.move_node(session, node_id, body.new_parent_id)
    return _read(session, node)


@app.post("/files/{node_id}/restore", response_model=FileRead)
def restore_file(node_id: uuid.UUID, session: Session = Depends(get_session)):
    node = service.restore_node(session, node_id)
    return _read(session, node)


@app.delete("/files/{node_id}", status_code=204, response_class=Response)
def delete_file(node_id: uuid.UUID, cascade: bool = True, session: Session = Depends(get_session)):
    service.delete_node(session, node_id, cascade=cascade)


@app.get("/files/{node_id}/children", response_model=list[FileSummary])
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
        session, manifest_id, file_id=body.file_id, child_manifest_id=body.child_manifest_id
    )
    return Response(status_code=201)


@app.delete("/manifests/{manifest_id}/members", status_code=204, response_class=Response)
def remove_manifest_member(
    manifest_id: uuid.UUID,
    file_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
):
    service.remove_manifest_member(
        session, manifest_id, file_id=file_id, child_manifest_id=child_manifest_id
    )


@app.get("/manifests/{manifest_id}/resolve", response_model=list[FileSummary])
def resolve_manifest(manifest_id: uuid.UUID, session: Session = Depends(get_session)):
    return list(service.resolve_manifest(session, manifest_id))


# --------------------------------------------------------------------------
# Dev UI (static, no logic) -- mounted last so it never shadows an API route
# --------------------------------------------------------------------------

app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
