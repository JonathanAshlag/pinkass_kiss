"""
OKF document layer: frontmatter-aware behavior built on top of the DAL.

Depends only on kb.models, never on kb.dal -- kb.dal doesn't import this
module at all, so there's no circularity to worry about either way.
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from kb.models import File

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_INTERNAL_LINK_PREFIX = "db://files/"
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")


def extract_links(content: str) -> list[str]:
    """Every markdown link target in `content`, in order of appearance."""
    return _LINK_RE.findall(content or "")


def find_broken_links(session: Session, node_id: uuid.UUID) -> list[str]:
    """
    Advisory only, never raises. Looks at this node's content for links using
    the internal `db://files/<uuid>` convention and returns the ones that
    don't resolve to a real, non-deleted node. Non-internal links (http(s),
    anything not db://files/...) are out of scope and ignored -- OKF mandates
    permissive link handling, and this project already treats
    sources[].resource as opaque, unenforced text.
    """
    node = session.get(File, node_id)
    if node is None or node.deleted_at is not None or not node.content:
        return []

    broken = []
    for target in extract_links(node.content):
        if not target.startswith(_INTERNAL_LINK_PREFIX):
            continue
        raw_id = target[len(_INTERNAL_LINK_PREFIX) :]
        try:
            target_id = uuid.UUID(raw_id)
        except ValueError:
            broken.append(target)
            continue
        target_node = session.get(File, target_id)
        if target_node is None or target_node.deleted_at is not None:
            broken.append(target)
    return broken


# --------------------------------------------------------------------------
# Frontmatter validation / lifecycle helpers
#
# Unlike the functions above, these never need a Session -- everything they
# look at (sources, verified, generated, content, stale_after) lives on the
# node's own row, so they take a `File` object directly.
# --------------------------------------------------------------------------


def _is_valid_actor(by: str) -> bool:
    """OKF's actor convention: `<producer>/<version>`, `human:<id>`, `process:<id>`."""
    return by.startswith("human:") or by.startswith("process:") or "/" in by


def _validate_actor_entry(prefix: str, entry, problems: list[str]) -> None:
    if not isinstance(entry, dict):
        problems.append(f"{prefix} is not an object")
        return
    by = entry.get("by")
    if not by:
        problems.append(f"{prefix} missing required 'by'")
    elif not _is_valid_actor(by):
        problems.append(
            f"{prefix}.by {by!r} doesn't match the "
            "human:<id>/process:<id>/<producer>/<version> convention"
        )
    if not entry.get("at"):
        problems.append(f"{prefix} missing required 'at'")


def validate_frontmatter(node: File) -> list[str]:
    """
    Advisory only, never raises. Reports shape problems in this node's
    sources/verified/generated JSONB columns against the shapes OKF documents
    for them. None of this is DB-enforced (these columns are intentionally
    opaque at the schema level) -- purely informational.
    """
    problems: list[str] = []

    for i, entry in enumerate(node.sources or []):
        if not isinstance(entry, dict):
            problems.append(f"sources[{i}] is not an object")
        elif not entry.get("resource"):
            problems.append(f"sources[{i}] missing required 'resource'")

    for i, entry in enumerate(node.verified or []):
        _validate_actor_entry(f"verified[{i}]", entry, problems)

    if node.generated is not None:
        _validate_actor_entry("generated", node.generated, problems)

    return problems


def extract_footnote_refs(content: str) -> list[str]:
    """
    Every in-body footnote reference label (`[^label]`) in `content`, in
    order of appearance -- excludes footnote *definitions* (`[^label]: ...`).
    """
    return _FOOTNOTE_REF_RE.findall(content or "")


def find_unresolved_footnotes(node: File) -> list[str]:
    """
    Advisory only, never raises. Reports footnote reference labels in this
    node's content that don't match any `id` among its own sources[]
    entries. No session needed -- sources lives on the same row as content.
    """
    if not node.content:
        return []
    known_ids = {
        entry.get("id") for entry in (node.sources or []) if isinstance(entry, dict)
    }
    return [
        label for label in extract_footnote_refs(node.content) if label not in known_ids
    ]


def is_stale(node: File, *, now: datetime | None = None) -> bool:
    """True when `node.stale_after` is set and has passed."""
    if node.stale_after is None:
        return False
    return (now or datetime.now(timezone.utc)) >= node.stale_after
