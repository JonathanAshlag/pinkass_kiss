"""Passive text-scanning for exact term/alias matches.

The server returns all matches found in a single document scan (no per-session dedup).
The client SDK is responsible for tracking whether it has already seen results and
respecting its own context-window budgets.
"""

import re
from datetime import datetime, timezone

from app.models.page import ClassificationTriangle
from app.search_config import PASSIVE_MAX_PAGES_PER_DOC, PASSIVE_TERM_CACHE_TTL_SECONDS
from app.services.classification import get_user_triangles, user_satisfies_classification
from app.storage.base import PageRepository
from app.models.user import User

_cache: dict = {"pages": [], "loaded_at": None}


async def _get_cached_pages(page_repo: PageRepository) -> list[dict]:
    """Fetch all published pages, cached with TTL expiry."""
    now = datetime.now(timezone.utc)
    if _cache["loaded_at"] is None or (now - _cache["loaded_at"]).total_seconds() > PASSIVE_TERM_CACHE_TTL_SECONDS:
        _cache["pages"] = await page_repo.list_scannable_pages(
            fields=["page_id", "title", "aliases", "description", "trust_tier", "classification"]
        )
        _cache["loaded_at"] = now
    return _cache["pages"]


async def scan_text(
    text: str,
    user: User,
    page_repo: PageRepository,
) -> tuple[list[dict], list[str]]:
    """Scan text for exact title/alias matches. Returns (matches, matched_terms).

    Classification-filtered, sorted by first occurrence in text. No per-session dedup
    or capping — that's the client SDK's responsibility.
    """
    pages = await _get_cached_pages(page_repo)
    user_triangles = await get_user_triangles(user.user_id)

    # Filter by classification
    visible = []
    for p in pages:
        classification = [
            ClassificationTriangle(**t) for t in p.get("classification", [])
        ]
        if user_satisfies_classification(user_triangles, classification):
            visible.append(p)

    # Find first occurrence of each visible page's title/aliases in text
    candidates = []  # (first_index, page_dict, matched_term)
    for p in visible:
        terms = [p["title"]] + (p.get("aliases") or [])
        best_idx, best_term = None, None
        for term in terms:
            if not term:
                continue
            # Word-boundary regex match, case-insensitive
            m = re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE)
            if m and (best_idx is None or m.start() < best_idx):
                best_idx, best_term = m.start(), term
        if best_idx is not None:
            candidates.append((best_idx, p, best_term))

    # Sort by first occurrence (deterministic ordering) and cap per-document
    candidates.sort(key=lambda c: c[0])
    candidates = candidates[:PASSIVE_MAX_PAGES_PER_DOC]

    matches = [c[1] for c in candidates]
    spans = [c[2] for c in candidates]

    return matches, spans
