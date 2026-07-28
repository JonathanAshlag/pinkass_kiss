"""Permission resolution for page visibility."""

import logging
from typing import Optional

from app.models.page import ClassificationTriangle
from app.models.user import User
from app.storage.base import PageRepository, UserRepository
from app.services.classification import get_user_triangles, user_satisfies_classification

logger = logging.getLogger("pinkas.permissions")


async def can_view_page(user: User, page_id: str, page_repo: PageRepository) -> bool:
    page_classification = await page_repo.get_classification(page_id)
    if page_classification is None:
        return False
    if not page_classification:
        return True
    user_triangles = await get_user_triangles(user.user_id)
    return user_satisfies_classification(user_triangles, page_classification)


async def get_user_by_id(user_id: str, user_repo: UserRepository) -> Optional[User]:
    return await user_repo.get(user_id)
