"""Question answering endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Union

from app.models.user import User
from app.routers.deps import get_current_user
from app.llm.retrieval import ask_with_retrieval

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: Union[str, None] = None
    messages: Union[list[dict], None] = None


@router.post("/ask")
async def ask(data: AskRequest, user: User = Depends(get_current_user)):
    """Answer a question using LLM with retrieval from the knowledge base."""
    if data.messages:
        messages = data.messages
    elif data.question:
        messages = [{"role": "user", "content": data.question}]
    else:
        return {"answer": "No question provided.", "cited_pages": []}

    result = await ask_with_retrieval(messages, user)
    return result
