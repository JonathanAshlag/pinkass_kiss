"""Slack-style page_id generation: a normalized-title slug plus a short random suffix.

Titles are immutable after creation (see app/services/pages.py:update_page and
app/services/mutations.py:apply_page_mutation), so page_id only needs to be computed
once, at creation time — this module is shared by the create-page service path and
the one-off id-migration script (scripts/migrate_page_ids.py) so both stay in sync.
"""

import secrets
import unicodedata
from typing import Awaitable, Callable

# Crockford-style safe alphabet: excludes i/l/o (visually ambiguous with 1/1/0),
# since the whole point of this scheme is that an LLM or human can reliably retype it.
_SUFFIX_ALPHABET = "0123456789abcdefghjkmnpqrstuvwxyz"
_SUFFIX_LENGTH = 4
_MAX_ATTEMPTS = 5

ExistsCheck = Callable[[str], Awaitable[bool]]


class PageIdCollisionError(RuntimeError):
    """Raised when no unique page_id could be found after several random attempts."""


def normalize_title_for_id(title: str) -> str:
    """Slugify a title while preserving non-Latin scripts (e.g. Hebrew) as-is.

    NFKD-decomposes the title so combining marks (accents, Hebrew niqqud) split
    from their base letter, then drops those marks — leaving base letters (Hebrew
    included) untouched. Any run of non-alphanumeric characters (whitespace,
    punctuation) collapses to a single hyphen. Falls back to "page" if nothing
    alphanumeric survives (e.g. an emoji-only or symbols-only title).
    """
    decomposed = unicodedata.normalize("NFKD", title)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    parts: list[str] = []
    pending_sep = False
    for ch in no_marks:
        if ch.isalnum():
            if pending_sep and parts:
                parts.append("-")
            parts.append(ch.lower())
            pending_sep = False
        else:
            pending_sep = True

    slug = "".join(parts)
    return slug or "page"


def _random_suffix() -> str:
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))


async def generate_page_id(title: str, exists: ExistsCheck) -> str:
    """Generate `{normalized-title}-{suffix}`, retrying the suffix on collision.

    `exists(candidate)` must return True if that id is already taken.
    """
    base = normalize_title_for_id(title)
    for _ in range(_MAX_ATTEMPTS):
        candidate = f"{base}-{_random_suffix()}"
        if not await exists(candidate):
            return candidate
    raise PageIdCollisionError(
        f"Could not generate a unique page_id for title {title!r} after {_MAX_ATTEMPTS} attempts"
    )
