from datetime import datetime

from app.models.bundle import Bundle, BundleEntry, ContentForm
from app.models.page import Page, PageStatus
from app.models.user import User
from app.services.permissions import can_view_page
from app.services.pages import get_page
from app.storage.base import BundleRepository, PageRepository


def render_bundle(entries: list[BundleEntry], pages_by_id: dict[str, Page]) -> str:
    """Render a bundle deterministically as markdown.

    No volatile fields (timestamps, session ids, user names) are included.
    Fixed order = entry order.
    """
    blocks = []
    for entry in entries:
        page = pages_by_id[entry.page_id]
        body = page.description if entry.content_form == ContentForm.description else page.content
        blocks.append(f"### {page.title}\n\n{body.strip()}")
    return "\n\n---\n\n".join(blocks)


async def upsert_bundle(
    name: str,
    entries: list[BundleEntry],
    admin: User,
    bundle_repo: BundleRepository,
    page_repo: PageRepository,
) -> Bundle:
    """Create or replace a bundle's entries. Validates that admin can view all pages."""
    for entry in entries:
        page = await get_page(entry.page_id, page_repo)
        if page is None:
            raise ValueError(f"page {entry.page_id} not found")
        if not await can_view_page(admin, entry.page_id, page_repo):
            raise PermissionError(f"admin cannot view page {entry.page_id}")

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
    """Load a bundle and render it against current page content.

    Validates every entry's page is currently published and viewable by the caller.
    """
    bundle = await bundle_repo.get(name)
    if bundle is None:
        raise ValueError(f"bundle {name} not found")

    pages_by_id = {}
    for entry in bundle.entries:
        page = await get_page(entry.page_id, page_repo)
        if page is None or page.status != PageStatus.published:
            raise ValueError(f"page {entry.page_id} is not publishable (missing or not published)")
        if not await can_view_page(user, entry.page_id, page_repo):
            raise PermissionError(f"cannot view page {entry.page_id}")
        pages_by_id[entry.page_id] = page

    rendered = render_bundle(bundle.entries, pages_by_id)
    return bundle, rendered
