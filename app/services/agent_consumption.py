"""Core agent consumption logic: search, fetch, scan with logging.

Session-based budgets (tool-call caps, dedup, query cache) are the responsibility of the
client SDK, not the server. The server observes all retrieval events via logging but does
not enforce context-window constraints it cannot see.
"""

import hashlib
import time

from app.models.bundle import Bundle
from app.models.consumption import SearchResponse, SearchHit, FetchResponse, FetchHit, UnavailablePage, ScanResponse, ScanMatch
from app.models.retrieval_log import RetrievalLogEntry, ConsumptionMode, MissCandidate
from app.models.agent import AgentRequestContext
from app.models.page import PageStatus
from app.search_config import ACTIVE_SEARCH_TOP_N, ACTIVE_SEARCH_CANDIDATE_POOL, ACTIVE_SEARCH_MISS_THRESHOLD
from app.services.bundles import fetch_bundle_text
from app.services.event_log import emit_retrieval_log
from app.services.pages import find_page_docs_fuzzy_scored, get_page
from app.services.permissions import can_view_page
from app.services.passive_scan import scan_text
from app.storage.base import BundleRepository, PageRepository


def _approx_tokens(text: str) -> int:
    """Approximate token count (simple: ~4 chars per token)."""
    return max(1, len(text) // 4)


async def run_search(
    query: str,
    ctx: AgentRequestContext,
    page_repo: PageRepository,
) -> SearchResponse:
    """Search for a term. Returns short-tier results with scores."""
    t0 = time.perf_counter()
    q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()

    # Search with scores
    candidates = await find_page_docs_fuzzy_scored(
        query,
        fields=["page_id", "title", "description", "trust_tier"],
        user=ctx.user,
        repo=page_repo,
        limit=ACTIVE_SEARCH_CANDIDATE_POOL,
    )
    candidates.sort(key=lambda d: d["score"], reverse=True)
    good = [c for c in candidates if c["score"] >= ACTIVE_SEARCH_MISS_THRESHOLD][
        :ACTIVE_SEARCH_TOP_N
    ]

    if not good:
        near = [
            MissCandidate(page_id=c["page_id"], title=c["title"], score=c["score"])
            for c in candidates
        ]
        response = SearchResponse(
            miss=True,
            message=f"No confident match for '{query}'.",
            nearest_candidates=near,
        )
        log_entry = RetrievalLogEntry(
            agent_id=ctx.agent.agent_id,
            session_id=ctx.session_id,
            mode=ConsumptionMode.search,
            query=query,
            query_hash=q_hash,
            candidates_not_returned=near,
            miss=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    else:
        hits = [
            SearchHit(
                page_id=c["page_id"],
                title=c["title"],
                description=c["description"],
                trust_tier=c["trust_tier"],
                score=c["score"],
            )
            for c in good
        ]
        response = SearchResponse(miss=False, results=hits)
        log_entry = RetrievalLogEntry(
            agent_id=ctx.agent.agent_id,
            session_id=ctx.session_id,
            mode=ConsumptionMode.search,
            query=query,
            query_hash=q_hash,
            page_ids=[h.page_id for h in hits],
            tiers=["short"] * len(hits),
            scores=[h.score for h in hits],
            returned_tokens=sum(_approx_tokens(h.description) for h in hits),
            miss=False,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    emit_retrieval_log(log_entry)
    return response


async def run_fetch(
    page_ids: list[str],
    ctx: AgentRequestContext,
    page_repo: PageRepository,
) -> FetchResponse:
    """Fetch long-tier (full content) for explicit page ids."""
    t0 = time.perf_counter()

    results, unavailable = [], []
    for pid in page_ids:
        if not await can_view_page(ctx.user, pid, page_repo):
            unavailable.append(UnavailablePage(page_id=pid, reason="forbidden"))
            continue
        page = await get_page(pid, page_repo)
        if page is None or page.status == PageStatus.deleted:
            unavailable.append(UnavailablePage(page_id=pid, reason="not_found"))
            continue
        results.append(
            FetchHit(
                page_id=page.page_id,
                title=page.title,
                content=page.content,
                trust_tier=page.trust_tier.value,
            )
        )

    emit_retrieval_log(
        RetrievalLogEntry(
            agent_id=ctx.agent.agent_id,
            session_id=ctx.session_id,
            mode=ConsumptionMode.fetch,
            page_ids=[r.page_id for r in results],
            tiers=["long"] * len(results),
            returned_tokens=sum(_approx_tokens(r.content) for r in results),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    )
    return FetchResponse(results=results, unavailable=unavailable)


async def run_scan(
    text: str,
    log_raw_text: bool,
    ctx: AgentRequestContext,
    page_repo: PageRepository,
) -> ScanResponse:
    """Passive scan of text for title/alias matches."""
    t0 = time.perf_counter()
    matches, spans = await scan_text(text, ctx.user, page_repo)
    q_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    emit_retrieval_log(
        RetrievalLogEntry(
            agent_id=ctx.agent.agent_id,
            session_id=ctx.session_id,
            mode=ConsumptionMode.scan,
            query_hash=q_hash,
            raw_text=text if log_raw_text else None,
            raw_text_logged=log_raw_text,
            matched_spans=spans,
            page_ids=[m["page_id"] for m in matches],
            tiers=["short"] * len(matches),
            returned_tokens=sum(_approx_tokens(m["description"]) for m in matches),
            miss=(len(matches) == 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    return ScanResponse(
        matches=[
            ScanMatch(
                page_id=m["page_id"],
                title=m["title"],
                description=m["description"],
                trust_tier=m["trust_tier"],
            )
            for m in matches
        ],
    )


async def run_bundle_fetch(
    name: str,
    ctx: AgentRequestContext,
    bundle_repo: BundleRepository,
    page_repo: PageRepository,
) -> tuple[Bundle, str]:
    """Fetch a bundle, rendered against current page content. Raises ValueError (not found)
    or PermissionError (unviewable page) from fetch_bundle_text; caller translates to HTTP."""
    t0 = time.perf_counter()
    bundle, rendered = await fetch_bundle_text(name, ctx.user, bundle_repo, page_repo)

    emit_retrieval_log(
        RetrievalLogEntry(
            agent_id=ctx.agent.agent_id,
            session_id=ctx.session_id,
            mode=ConsumptionMode.bundle,
            bundle_name=name,
            page_ids=[e.page_id for e in bundle.entries],
            tiers=[e.content_form.value for e in bundle.entries],
            returned_tokens=max(1, len(rendered) // 4),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    )
    return bundle, rendered
