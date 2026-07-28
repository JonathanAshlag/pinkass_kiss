"""Scheduled jobs for page expiry review and trust drift detection."""

import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.container import background_repos
from app.models.page import HistoryEntry, PageStatus, TrustTier
from app.models.request import ApprovalRequest, RequestStatus, RequestType, RequestHistoryEntry, ReviewPayload
from app.services.pages import compute_content_hash

logger = logging.getLogger("pinkas.scheduler")


async def check_expired_pages() -> int:
    """Find pages with expired next_approval_date and create review requests."""
    today = date.today().isoformat()
    processed = 0

    async with background_repos() as repos:
        expired = await repos.pages.list_expired(today)

        for doc in expired:
            page_id = doc["page_id"]
            created_by = doc.get("created_by", "")

            user = await repos.users.get(created_by)
            if not user or not user.workflow_id:
                continue

            existing = await repos.requests.get_pending_for_page(page_id, RequestType.review.value)
            if existing:
                continue

            req = ApprovalRequest(
                type=RequestType.review,
                page_id=page_id,
                requested_by=created_by,
                proposed_content=ReviewPayload(
                    title=doc.get("title"),
                    content=doc.get("content"),
                ),
                workflow_id=user.workflow_id,
                current_step=0,
                status=RequestStatus.pending,
            )
            req.history.append(RequestHistoryEntry(
                user_id="system",
                decision="submitted",
                comment="Automatic review: page approval date expired",
            ))
            await repos.requests.create(req)
            await repos.pages.update_with_history(
                page_id,
                {"status": PageStatus.pending_approval.value},
                HistoryEntry(
                    user_id="system",
                    action="review_requested",
                    comment="Automatic review: approval date expired",
                ),
            )
            processed += 1

    logger.info(f"Expired pages check complete: {processed} pages sent for review")
    return processed


async def check_verification_drift() -> int:
    """Find verified pages whose content has changed since verification."""
    processed = 0

    async with background_repos() as repos:
        verified = await repos.pages.list_verified_published()

        for doc in verified:
            current_hash = compute_content_hash(doc.get("content", ""))
            if current_hash == doc.get("verified_content_hash"):
                continue

            page_id = doc["page_id"]
            existing = await repos.requests.get_pending_for_page(page_id, RequestType.review.value)
            if existing:
                continue

            user = await repos.users.get(doc.get("created_by", ""))
            if not user or not user.workflow_id:
                continue

            req = ApprovalRequest(
                type=RequestType.review,
                page_id=page_id,
                requested_by=doc.get("created_by", ""),
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
            await repos.requests.create(req)
            await repos.pages.append_history(
                page_id,
                HistoryEntry(
                    user_id="system",
                    action="verification_drift_detected",
                    comment="Content changed since last human verification",
                ),
            )
            processed += 1

    logger.info(f"Verification drift check complete: {processed} pages flagged for re-verification")
    return processed


async def update_inbound_link_counts() -> None:
    """Recompute inbound page-reference counts for all published pages."""
    async with background_repos() as repos:
        docs = await repos.pages.list_published_with_references()
        counts: dict[str, int] = {}

        for doc in docs:
            for ref in doc.get("references", []):
                if ref.get("type") == "page" and ref.get("page_id"):
                    pid = ref["page_id"]
                    counts[pid] = counts.get(pid, 0) + 1

        for page_id, count in counts.items():
            await repos.pages.update_inbound_link_count(page_id, count)

        await repos.pages.reset_inbound_link_counts(list(counts.keys()))

    logger.info(f"Inbound link counts updated: {len(counts)} pages have inbound links")


def setup_scheduler() -> AsyncIOScheduler:
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
