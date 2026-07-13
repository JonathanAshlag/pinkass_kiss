"""Scheduled jobs for page expiry review and trust drift detection."""

import asyncio
import logging
from datetime import date, datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import get_db
from app.models.page import HistoryEntry, PageStatus, TrustTier
from app.models.request import ApprovalRequest, RequestStatus, RequestType, RequestHistoryEntry, ReviewPayload
from app.services.pages import compute_content_hash
from app.services.users import get_user

logger = logging.getLogger("pinkas.scheduler")


async def check_expired_pages() -> int:
    """Find pages with expired next_approval_date and create review requests."""
    db = get_db()
    today = date.today().isoformat()
    processed = 0

    cursor = db.pages.find({
        "status": PageStatus.published.value,
        "next_approval_date": {"$lte": today, "$ne": None},
    })

    async for doc in cursor:
        doc.pop("_id", None)
        page_id = doc["page_id"]
        created_by = doc["created_by"]

        user = await get_user(created_by)
        if not user or not user.workflow_id:
            continue

        existing = await db.requests.find_one({
            "page_id": page_id,
            "type": RequestType.review.value,
            "status": RequestStatus.pending.value,
        })
        if existing:
            continue

        req = ApprovalRequest(
            type=RequestType.review,
            page_id=page_id,
            requested_by=created_by,
            proposed_content=ReviewPayload(title=doc.get("title"), content=doc.get("content")),
            workflow_id=user.workflow_id,
            current_step=0,
            status=RequestStatus.pending,
        )
        req.history.append(RequestHistoryEntry(
            user_id="system",
            decision="submitted",
            comment="Automatic review: page approval date expired",
        ))
        await db.requests.insert_one(req.model_dump(mode="json"))

        await db.pages.update_one(
            {"page_id": page_id},
            {
                "$set": {"status": PageStatus.pending_approval.value},
                "$push": {"history": HistoryEntry(
                    user_id="system",
                    action="review_requested",
                    comment="Automatic review: approval date expired",
                ).model_dump(mode="json")},
            }
        )
        processed += 1

    logger.info(f"Expired pages check complete: {processed} pages sent for review")
    return processed


async def check_verification_drift() -> int:
    """Find verified pages whose content has changed since verification and raise a review request."""
    db = get_db()
    processed = 0

    cursor = db.pages.find({
        "trust_tier": TrustTier.verified.value,
        "status": PageStatus.published.value,
    })

    async for doc in cursor:
        current_hash = compute_content_hash(doc.get("content", ""))
        if current_hash == doc.get("verified_content_hash"):
            continue

        page_id = doc["page_id"]
        existing = await db.requests.find_one({
            "page_id": page_id,
            "type": RequestType.review.value,
            "status": RequestStatus.pending.value,
        })
        if existing:
            continue

        user = await get_user(doc["created_by"])
        if not user or not user.workflow_id:
            continue

        req = ApprovalRequest(
            type=RequestType.review,
            page_id=page_id,
            requested_by=doc["created_by"],
            proposed_content=ReviewPayload(
                title=doc.get("title"),
                content=doc.get("content"),
                trust_tier=TrustTier.verified.value,
            ),
            workflow_id=user.workflow_id,
            current_step=0,
            status=RequestStatus.pending,
        )
        req.history.append(RequestHistoryEntry(
            user_id="system",
            decision="submitted",
            comment="Automatic review: verified page content has drifted from verified hash",
        ))
        await db.requests.insert_one(req.model_dump(mode="json"))
        await db.pages.update_one(
            {"page_id": page_id},
            {"$push": {"history": HistoryEntry(
                user_id="system",
                action="verification_drift_detected",
                comment="Content changed since last human verification",
            ).model_dump(mode="json")}},
        )
        processed += 1

    logger.info(f"Verification drift check complete: {processed} pages flagged for re-verification")
    return processed


async def update_inbound_link_counts() -> None:
    """Recompute inbound page-reference counts for all published pages."""
    db = get_db()
    counts: dict[str, int] = {}

    cursor = db.pages.find(
        {"status": PageStatus.published.value},
        {"references": 1, "_id": 0},
    )
    async for doc in cursor:
        for ref in doc.get("references", []):
            if ref.get("type") == "page" and ref.get("page_id"):
                pid = ref["page_id"]
                counts[pid] = counts.get(pid, 0) + 1

    for page_id, count in counts.items():
        await db.pages.update_one(
            {"page_id": page_id},
            {"$set": {"inbound_link_count": count}},
        )
    if counts:
        await db.pages.update_many(
            {"page_id": {"$nin": list(counts.keys())}},
            {"$set": {"inbound_link_count": 0}},
        )
    logger.info(f"Inbound link counts updated: {len(counts)} pages have inbound links")


def setup_scheduler() -> AsyncIOScheduler:
    """Configure and start the APScheduler for daily page expiry checks."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_expired_pages,
        "cron",
        hour=settings.scheduler_hour,
        minute=settings.scheduler_minute,
        id="check_expired_pages",
        replace_existing=True,
    )
    scheduler.add_job(
        check_verification_drift,
        "cron",
        hour=settings.scheduler_hour,
        minute=settings.scheduler_minute,
        id="check_verification_drift",
        replace_existing=True,
    )
    scheduler.add_job(
        update_inbound_link_counts,
        "cron",
        hour=settings.scheduler_hour,
        minute=(settings.scheduler_minute + 5) % 60,
        id="update_inbound_link_counts",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started: daily check at {settings.scheduler_hour:02d}:{settings.scheduler_minute:02d}"
    )
    return scheduler
