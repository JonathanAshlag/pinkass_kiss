"""Document ingestion: upload, background candidate generation, and review endpoints."""

import logging
import uuid
from dataclasses import asdict
from io import BytesIO

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Form, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.container import PageRepo, RequestRepo, SourceFileRepo, UserRepo, WorkflowRepo, background_repos
from app.infrastructure.mongo import get_gridfs
from app.llm import batch_store
from app.llm.extraction import extract_content
from app.llm.pipeline import run_ingestion_pipeline
from app.models.page import PageCreate, PageUpdate, Reference, ReferenceType, TrustTier
from app.models.request import RequestType
from app.models.user import User, PermissionLevel
from app.routers.deps import require_editor
from app.services.mutations import apply_page_mutation
from app.services.pages import get_page, set_page_references
from app.storage.base import SourceFileRepository

logger = logging.getLogger("pinkas.produce")

router = APIRouter(tags=["produce"])


class CandidateEdits(BaseModel):
    title: str | None = None
    description: str | None = None
    content: str | None = None
    aliases: list[str] | None = None


def _candidate_dict(candidate: batch_store.Candidate) -> dict:
    return asdict(candidate)


def _get_owned_batch(batch_id: str, user: User) -> batch_store.Batch:
    batch = batch_store.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not the owner of this ingestion batch")
    return batch


def _get_pending_candidate(batch: batch_store.Batch, candidate_id: str) -> batch_store.Candidate:
    candidate = batch.candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if candidate.status != "pending":
        raise HTTPException(status_code=409, detail=f"Candidate already {candidate.status}")
    return candidate


async def _purge_source_file(file_id: str, source_file_repo: SourceFileRepository) -> None:
    """No candidate from this file was approved — remove the uploaded blob and its record."""
    try:
        await get_gridfs().delete(ObjectId(file_id))
    except Exception:
        logger.warning(f"GridFS delete failed for file_id={file_id}", exc_info=True)
    await source_file_repo.delete(file_id)


async def _run_batch(
    batch_id: str,
    file_infos: list[tuple[str, str, str, list[dict]]],
    ingestion_context: str | None,
) -> None:
    """Background task: run the LLM pipeline for every uploaded file in a batch."""
    try:
        async with background_repos() as repos:
            for file_id_str, filename, text, parts in file_infos:
                await run_ingestion_pipeline(
                    batch_id=batch_id,
                    file_id_str=file_id_str,
                    filename=filename,
                    text=text,
                    content_parts=parts,
                    ingestion_context=ingestion_context,
                    page_repo=repos.pages,
                )
                if batch_store.file_fully_resolved_with_zero_approvals(batch_id, file_id_str):
                    await _purge_source_file(file_id_str, repos.source_files)
        batch_store.mark_done(batch_id)
    except Exception as e:
        logger.exception(f"Ingestion batch {batch_id} failed")
        batch_store.mark_error(batch_id, str(e))


@router.post("/produce")
async def produce(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    initial_trust_tier: str = Form(default=TrustTier.unverified.value),
    ingestion_context: str | None = Form(default=None),
    user: User = Depends(require_editor),
    source_file_repo: SourceFileRepo = None,
):
    """Upload files and kick off background candidate generation. Returns immediately."""
    batch_id = str(uuid.uuid4())
    file_infos: list[tuple[str, str, str, list[dict]]] = []

    for upload_file in files:
        content = await upload_file.read()
        content_type = upload_file.content_type or "application/octet-stream"
        filename = upload_file.filename or "unnamed"

        gridfs = get_gridfs()
        oid = await gridfs.upload_from_stream(
            filename,
            BytesIO(content),
            metadata={"content_type": content_type, "uploaded_by": user.user_id},
        )
        file_id_str = str(oid)

        extracted = extract_content(content, content_type, filename)

        await source_file_repo.create(
            file_id=file_id_str,
            filename=filename,
            content_type=content_type,
            uploaded_by=user.user_id,
            extracted_text=extracted.text,
        )
        file_infos.append((file_id_str, filename, extracted.text, extracted.parts))

    batch_store.create_batch(batch_id, user.user_id, initial_trust_tier)
    background_tasks.add_task(_run_batch, batch_id, file_infos, ingestion_context)

    return {"batch_id": batch_id}


@router.get("/produce/batches/{batch_id}")
async def get_batch_status(batch_id: str, user: User = Depends(require_editor)):
    batch = _get_owned_batch(batch_id, user)
    return {
        "status": batch.status,
        "error": batch.error,
        "progress_message": batch.progress_message,
        "candidates": [_candidate_dict(c) for c in batch.candidates.values()],
    }


@router.post("/produce/batches/{batch_id}/candidates/{candidate_id}/approve")
async def approve_candidate(
    batch_id: str,
    candidate_id: str,
    edits: CandidateEdits | None = Body(default=None),
    user: User = Depends(require_editor),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
    wf_repo: WorkflowRepo = None,
    user_repo: UserRepo = None,
    source_file_repo: SourceFileRepo = None,
):
    batch = _get_owned_batch(batch_id, user)
    candidate = _get_pending_candidate(batch, candidate_id)
    edits = edits or CandidateEdits()

    title = edits.title if edits.title is not None else candidate.title
    description = edits.description if edits.description is not None else candidate.description
    content = edits.content if edits.content is not None else candidate.content
    aliases = edits.aliases if edits.aliases is not None else candidate.aliases

    file_ref = Reference(type=ReferenceType.file, file_id=candidate.file_id)
    page_id: str | None = None

    if candidate.action == "create":
        should_verify = (
            user.permission_level == PermissionLevel.admin
            and batch.initial_trust_tier == TrustTier.verified.value
        )
        result = await apply_page_mutation(
            RequestType.create, user, page_repo, req_repo,
            data=PageCreate(title=title, description=description, content=content,
                             aliases=aliases, references=[file_ref]),
            trust_tier=TrustTier.verified if should_verify else None,
        )
        page_id = result.page["page_id"] if result.page else None

    elif candidate.action == "merge":
        live_page = await get_page(candidate.matched_page_id, page_repo)
        if not live_page:
            raise HTTPException(status_code=404, detail="Matched page no longer exists")
        updated_refs = list(live_page.references) + [file_ref]
        await apply_page_mutation(
            # title is intentionally omitted: it's immutable once the page exists,
            # so a merge can't rename the matched page even if the candidate's
            # extracted title differs.
            RequestType.edit, user, page_repo, req_repo,
            data=PageUpdate(description=description, content=content,
                             aliases=aliases, references=updated_refs),
            page_id=candidate.matched_page_id, page=live_page,
            wf_repo=wf_repo, user_repo=user_repo,
        )
        page_id = candidate.matched_page_id

    elif candidate.action == "link":
        live_page = await get_page(candidate.matched_page_id, page_repo)
        if not live_page:
            raise HTTPException(status_code=404, detail="Matched page no longer exists")
        updated_refs = list(live_page.references) + [file_ref]
        await set_page_references(candidate.matched_page_id, updated_refs, page_repo)
        page_id = candidate.matched_page_id

    if page_id:
        accumulated = batch_store.record_approved_page(batch_id, candidate.file_id, page_id)
        await source_file_repo.set_page_ids(candidate.file_id, accumulated)

    batch_store.resolve_candidate(batch_id, candidate_id, "approved")
    return {"page_id": page_id}


@router.post("/produce/batches/{batch_id}/candidates/{candidate_id}/reject")
async def reject_candidate(
    batch_id: str,
    candidate_id: str,
    user: User = Depends(require_editor),
    source_file_repo: SourceFileRepo = None,
):
    batch = _get_owned_batch(batch_id, user)
    candidate = _get_pending_candidate(batch, candidate_id)

    batch_store.resolve_candidate(batch_id, candidate_id, "rejected")

    if batch_store.file_fully_resolved_with_zero_approvals(batch_id, candidate.file_id):
        await _purge_source_file(candidate.file_id, source_file_repo)

    return {"status": "rejected"}


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Download a stored source file."""
    from fastapi.responses import StreamingResponse

    gridfs = get_gridfs()
    try:
        stream = await gridfs.open_download_stream(ObjectId(file_id))
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    async def iterfile():
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        iterfile(),
        media_type=stream.metadata.get("content_type", "application/octet-stream") if stream.metadata else "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={stream.filename}"},
        )
