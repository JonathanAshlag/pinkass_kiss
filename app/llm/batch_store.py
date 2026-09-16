"""In-memory store for document-ingestion proposals awaiting user review.

Deliberately not persisted to any database: batches live only as long as the
FastAPI process does, and review is scoped to the user who started the batch
(single-worker `uvicorn` run — see CLAUDE.md commands). A simple size cap keeps
memory bounded on a long-running server; no locking is needed since all access
happens on the single asyncio event loop.
"""

from dataclasses import dataclass, field
from typing import Literal

MAX_BATCHES = 50

CandidateAction = Literal["create", "merge", "link"]
CandidateStatus = Literal["pending", "approved", "rejected"]
BatchStatus = Literal["running", "done", "error"]


@dataclass
class Candidate:
    candidate_id: str
    file_id: str
    filename: str
    action: CandidateAction
    title: str
    description: str
    content: str
    aliases: list[str] = field(default_factory=list)
    matched_page_id: str | None = None
    matched_page_title: str | None = None
    matched_page_description: str | None = None
    matched_page_content: str | None = None
    matched_page_aliases: list[str] = field(default_factory=list)
    status: CandidateStatus = "pending"


@dataclass
class Batch:
    batch_id: str
    user_id: str
    initial_trust_tier: str
    status: BatchStatus = "running"
    error: str | None = None
    progress_message: str = ""
    candidates: dict[str, Candidate] = field(default_factory=dict)
    file_candidate_ids: dict[str, list[str]] = field(default_factory=dict)
    file_approved_page_ids: dict[str, list[str]] = field(default_factory=dict)


_BATCHES: dict[str, Batch] = {}


def create_batch(batch_id: str, user_id: str, initial_trust_tier: str) -> Batch:
    batch = Batch(batch_id=batch_id, user_id=user_id, initial_trust_tier=initial_trust_tier)
    _BATCHES[batch_id] = batch
    if len(_BATCHES) > MAX_BATCHES:
        del _BATCHES[next(iter(_BATCHES))]
    return batch


def get_batch(batch_id: str) -> Batch | None:
    return _BATCHES.get(batch_id)


def register_file(batch_id: str, file_id: str) -> None:
    batch = _BATCHES[batch_id]
    batch.file_candidate_ids.setdefault(file_id, [])
    batch.file_approved_page_ids.setdefault(file_id, [])


def add_candidate(batch_id: str, candidate: Candidate) -> None:
    batch = _BATCHES[batch_id]
    batch.candidates[candidate.candidate_id] = candidate
    batch.file_candidate_ids.setdefault(candidate.file_id, []).append(candidate.candidate_id)


def set_progress(batch_id: str, message: str) -> None:
    batch = _BATCHES.get(batch_id)
    if batch:
        batch.progress_message = message


def mark_done(batch_id: str) -> None:
    batch = _BATCHES.get(batch_id)
    if batch:
        batch.status = "done"
        batch.progress_message = ""


def mark_error(batch_id: str, error: str) -> None:
    batch = _BATCHES.get(batch_id)
    if batch:
        batch.status = "error"
        batch.progress_message = ""


def resolve_candidate(batch_id: str, candidate_id: str, status: CandidateStatus) -> Candidate | None:
    batch = _BATCHES.get(batch_id)
    if not batch:
        return None
    candidate = batch.candidates.get(candidate_id)
    if candidate:
        candidate.status = status
    return candidate


def record_approved_page(batch_id: str, file_id: str, page_id: str) -> list[str]:
    batch = _BATCHES[batch_id]
    ids = batch.file_approved_page_ids.setdefault(file_id, [])
    ids.append(page_id)
    return ids


def file_fully_resolved_with_zero_approvals(batch_id: str, file_id: str) -> bool:
    """True once every candidate from this file has been decided and none were approved."""
    batch = _BATCHES.get(batch_id)
    if not batch:
        return False
    candidate_ids = batch.file_candidate_ids.get(file_id, [])
    if any(batch.candidates[cid].status == "pending" for cid in candidate_ids):
        return False
    return len(batch.file_approved_page_ids.get(file_id, [])) == 0
