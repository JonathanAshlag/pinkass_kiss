"""Question answering endpoint."""

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Union

from app.container import PageRepo
from app.models.audit import AuditAction, AuditLogEntry, AuditOutcome, UserContext
from app.models.user import User
from app.routers.deps import get_current_user, get_user_context
from app.llm.retrieval import ask_with_retrieval
from app.services.audit import emit_audit_log

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: Union[str, None] = None
    messages: Union[list[dict], None] = None


@router.post("/ask")
async def ask(
    data: AskRequest,
    user: User = Depends(get_current_user),
    user_context: UserContext = Depends(get_user_context),
    page_repo: PageRepo = None,
):
    if data.messages:
        messages = data.messages
    elif data.question:
        messages = [{"role": "user", "content": data.question}]
    else:
        return {"answer": "No question provided.", "cited_pages": []}

    t0 = time.perf_counter()
    result = await ask_with_retrieval(messages, user, page_repo)
    outcome = AuditOutcome.error if result.get("answer", "").startswith("Error") else AuditOutcome.success
    emit_audit_log(AuditLogEntry(
        action=AuditAction.ask,
        user_context=user_context,
        outcome=outcome,
        result=result,
        latency_ms=(time.perf_counter() - t0) * 1000,
        request_path="/ask",
    ))
    return result
