"""Hierarchical permission resolution for page visibility."""

import logging
from typing import Optional

from app.db import get_db
from app.models.user import User, PermissionLevel

logger = logging.getLogger("pinkas.permissions")


async def get_all_descendant_ids(page_id: str) -> set[str]:
    """Recursively get all descendant page_ids of a given page."""
    db = get_db()
    descendants: set[str] = set()
    queue = [page_id]
    while queue:
        current = queue.pop(0)
        cursor = db.pages.find(
            {"parent_id": current, "status": {"$ne": "deleted"}},
            {"page_id": 1}
        )
        async for doc in cursor:
            child_id = doc["page_id"]
            if child_id not in descendants:
                descendants.add(child_id)
                queue.append(child_id)
    return descendants


async def get_visible_page_ids(user: User) -> set[str]:
    """Resolve all page_ids visible to a user (hierarchical permissions).

    Admins see everything. For other users, each page_permission grants
    visibility to that page and its entire subtree.
    """
    if user.permission_level == PermissionLevel.admin:
        db = get_db()
        ids: set[str] = set()
        cursor = db.pages.find({"status": {"$ne": "deleted"}}, {"page_id": 1})
        async for doc in cursor:
            ids.add(doc["page_id"])
        return ids

    visible: set[str] = set()
    for pid in user.page_permissions:
        visible.add(pid)
        descendants = await get_all_descendant_ids(pid)
        visible.update(descendants)
    return visible


async def can_view_page(user: User, page_id: str) -> bool:
    """Check if a user can view a specific page."""
    if user.permission_level == PermissionLevel.admin:
        return True
    visible = await get_visible_page_ids(user)
    return page_id in visible


async def get_user_by_id(user_id: str) -> Optional[User]:
    """Fetch a user from the database."""
    db = get_db()
    doc = await db.users.find_one({"user_id": user_id})
    if doc:
        doc.pop("_id", None)
        return User(**doc)
    return None
