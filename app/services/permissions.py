"""Permission resolution for page visibility."""

import logging
from typing import Optional

from app.db import get_db
from app.models.page import ClassificationTriangle
from app.models.user import User
from app.services.classification import get_user_triangles, user_satisfies_classification

logger = logging.getLogger("pinkas.permissions")


async def can_view_page(user: User, page_id: str) -> bool:
    """Check if a user can view a specific page (classification-based)."""
    db = get_db()
    doc = await db.pages.find_one({"page_id": page_id}, {"classification": 1})
    if not doc:
        return False
    page_classification = [
        ClassificationTriangle(**t) for t in doc.get("classification", [])
    ]
    if not page_classification:
        return True
    user_triangles = await get_user_triangles(user.user_id)
    return user_satisfies_classification(user_triangles, page_classification)


async def get_user_by_id(user_id: str) -> Optional[User]:
    """Fetch a user from the database."""
    db = get_db()
    doc = await db.users.find_one({"user_id": user_id})
    if doc:
        doc.pop("_id", None)
        return User(**doc)
    return None
