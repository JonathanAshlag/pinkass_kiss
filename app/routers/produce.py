"""Document ingestion endpoint."""

import logging
from io import BytesIO

from fastapi import APIRouter, Depends, Form, UploadFile, File, HTTPException

from app.container import PageRepo, RequestRepo, SourceFileRepo
from app.infrastructure.mongo import get_gridfs
from app.models.page import TrustTier
from app.models.user import User
from app.routers.deps import require_editor
from app.llm.extraction import extract_content
from app.llm.pipeline import run_ingestion_pipeline

logger = logging.getLogger("pinkas.produce")

router = APIRouter(tags=["produce"])


@router.post("/produce")
async def produce(
    files: list[UploadFile] = File(...),
    initial_trust_tier: str = Form(default=TrustTier.unverified.value),
    user: User = Depends(require_editor),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
    source_file_repo: SourceFileRepo = None,
):
    """Ingest files, extract text, generate pages via LLM."""
    results = []

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

        pipeline_results = await run_ingestion_pipeline(
            text=extracted.text,
            content_parts=extracted.parts,
            filename=filename,
            file_id_str=file_id_str,
            user=user,
            initial_trust_tier=initial_trust_tier,
            page_repo=page_repo,
            req_repo=req_repo,
        )

        generated_page_ids = [r.page_id for r in pipeline_results]
        results.extend([r.model_dump(mode="json") for r in pipeline_results])

        await source_file_repo.set_page_ids(file_id_str, generated_page_ids)

    return {"generated_pages": results}


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Download a stored source file."""
    from fastapi.responses import StreamingResponse

    from bson import ObjectId
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
