from datetime import datetime
from typing import Union

from app.models.bundle import Bundle, BundleEntry, ContentForm
from app.models.page import Page, PageStatus
from app.models.page_version import PageVersion
from app.models.user import User
from app.services.permissions import can_view_page
from app.services.pages import get_page
from app.storage.base import BundleRepository, PageRepository


def render_bundle(entries: list[BundleEntry], contents: list[Union[Page, PageVersion]]) -> str:
    """Render a bundle deterministically as markdown.

    `contents[i]` is the resolved content for `entries[i]` — the live Page for an
    unpinned entry, or the frozen PageVersion snapshot for a pinned one. No volatile
    fields (timestamps, session ids, user names) are included. Fixed order = entry order.
    """
    blocks = []
    for entry, content in zip(entries, contents):
        body = content.description if entry.content_form == ContentForm.description else content.content
        blocks.append(f"### {content.title}\n\n{body.strip()}")
    return "\n\n---\n\n".join(blocks)


async def upsert_bundle(
    name: str,
    entries: list[BundleEntry],
    admin: User,
    bundle_repo: BundleRepository,
    page_repo: PageRepository,
) -> Bundle:
    """Create or replace a bundle's entries. Validates that admin can view all pages
    (and, for pinned entries, that the pinned version actually exists)."""
    for entry in entries:
        page = await get_page(entry.page_id, page_repo)
        if page is None:
            raise ValueError(f"page {entry.page_id} not found")
        if not await can_view_page(admin, entry.page_id, page_repo):
            raise PermissionError(f"admin cannot view page {entry.page_id}")
        if entry.version_id is not None:
            version = await page_repo.get_version(entry.version_id)
            if version is None or version.page_id != entry.page_id:
                raise ValueError(f"version {entry.version_id} not found for page {entry.page_id}")

    existing = await bundle_repo.get(name)
    now = datetime.utcnow()

    bundle = Bundle(
        name=name,
        entries=entries,
        created_by=existing.created_by if existing else admin.user_id,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    await bundle_repo.upsert(bundle)
    return bundle


async def fetch_bundle_text(
    name: str,
    user: User,
    bundle_repo: BundleRepository,
    page_repo: PageRepository,
) -> tuple[Bundle, str]:
    """Load a bundle and render it. Unpinned entries track the current live page;
    pinned entries (entry.version_id set) render the frozen snapshot instead.

    Permission and publishability are always validated against the live page,
    even for pinned entries, matching how any other page view is gated.
    """
    bundle = await bundle_repo.get(name)
    if bundle is None:
        raise ValueError(f"bundle {name} not found")

    contents: list = []
    for entry in bundle.entries:
        page = await get_page(entry.page_id, page_repo)
        if page is None or page.status != PageStatus.published:
            raise ValueError(f"page {entry.page_id} is not publishable (missing or not published)")
        if not await can_view_page(user, entry.page_id, page_repo):
            raise PermissionError(f"cannot view page {entry.page_id}")
        if entry.version_id is not None:
            version = await page_repo.get_version(entry.version_id)
            if version is None:
                raise ValueError(f"version {entry.version_id} not found for page {entry.page_id}")
            contents.append(version)
        else:
            contents.append(page)

    rendered = render_bundle(bundle.entries, contents)
    return bundle, rendered
