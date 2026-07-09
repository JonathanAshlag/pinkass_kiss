"""Document ingestion endpoint."""

import logging
from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Form, UploadFile, File, HTTPException

from app.db import get_db, get_gridfs
from app.models.page import PageCreate, Reference, ReferenceType, TrustTier
from app.models.request import RequestType
from app.models.user import User, PermissionLevel
from app.routers.deps import require_editor
from app.services.pages import create_page, compute_content_hash
from app.services.requests import create_request
from app.llm.client import produce_pages

logger = logging.getLogger("pinkas.produce")

router = APIRouter(tags=["produce"])


def _extract_text(content: bytes, content_type: str, filename: str) -> str:
    """Extract text from various file formats."""
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text

    elif content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) or filename.endswith(".docx"):
        from docx import Document
        doc = Document(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)

    elif content_type == "text/html" or filename.endswith(".html") or filename.endswith(".htm"):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "lxml")
        return soup.get_text(separator="\n")

    elif content_type.startswith("text/") or filename.endswith((".txt", ".md")):
        return content.decode("utf-8", errors="replace")

    return content.decode("utf-8", errors="replace")


@router.post("/produce")
async def produce(
    files: list[UploadFile] = File(...),
    initial_trust_tier: str = Form(default=TrustTier.unverified.value),
    user: User = Depends(require_editor),
):
    """Ingest files, extract text, generate pages via LLM."""
    db = get_db()
    gridfs = get_gridfs()
    results = []

    for upload_file in files:
        content = await upload_file.read()
        content_type = upload_file.content_type or "application/octet-stream"
        filename = upload_file.filename or "unnamed"

        # Store in GridFS
        file_id = await gridfs.upload_from_stream(
            filename,
            BytesIO(content),
            metadata={
                "content_type": content_type,
                "uploaded_by": user.user_id,
            }
        )
        file_id_str = str(file_id)

        # Extract text
        extracted = _extract_text(content, content_type, filename)

        # Store file metadata
        await db.source_files.insert_one({
            "file_id": file_id_str,
            "filename": filename,
            "content_type": content_type,
            "uploaded_by": user.user_id,
            "extracted_text": extracted[:10000],
            "generated_page_ids": [],
        })

        # Use LLM to split into pages
        page_defs = await produce_pages(extracted, filename)

        generated_page_ids = []
        for page_def in page_defs:
            title = page_def.get("title", filename)
            page_content = page_def.get("content", extracted[:2000])

            page_data = PageCreate(
                title=title,
                content=page_content,
                references=[Reference(type=ReferenceType.file, file_id=file_id_str)],
            )

            page = await create_page(page_data, user)
            generated_page_ids.append(page.page_id)

            if user.workflow_id:
                await create_request(
                    req_type=RequestType.create,
                    page_id=page.page_id,
                    user=user,
                    proposed_content={"title": title, "content": page_content},
                )

            if (
                user.permission_level == PermissionLevel.admin
                and initial_trust_tier == TrustTier.verified.value
            ):
                await db.pages.update_one(
                    {"page_id": page.page_id},
                    {"$set": {
                        "trust_tier": TrustTier.verified.value,
                        "verified_content_hash": compute_content_hash(page.content),
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "verified_by": user.user_id,
                    }},
                )

            results.append({
                "page_id": page.page_id,
                "title": title,
                "status": "pending_approval" if user.workflow_id else "published",
            })

        # Update source file with generated page ids
        await db.source_files.update_one(
            {"file_id": file_id_str},
            {"$set": {"generated_page_ids": generated_page_ids}}
        )

    return {"generated_pages": results}


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Download a stored source file."""
    from bson import ObjectId
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
        headers={"Content-Disposition": f"attachment; filename={stream.filename}"}
    )
