"""Classification triangle access control."""

import logging

import httpx

from app.config import settings
from app.models.page import ClassificationTriangle

logger = logging.getLogger("pinkas.classification")


async def get_user_triangles(user_id: str) -> list[ClassificationTriangle]:
    """Fetch classification triangles for a user from the external API.

    Returns empty list on failure (fail-closed: user cannot access classified pages).
    Returns empty list if classification_api_url is not configured (feature disabled).
    """
    if not settings.classification_api_url:
        return []
    url = f"{settings.classification_api_url}/users/{user_id}/classifications"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return [ClassificationTriangle(**t) for t in data.get("triangles", [])]
    except Exception as e:
        logger.warning(f"Failed to fetch classification for user {user_id}: {e}")
        return []


def user_satisfies_classification(
    user_triangles: list[ClassificationTriangle],
    page_classification: list[ClassificationTriangle],
) -> bool:
    """Check if user's triangles satisfy all of a page's classification requirements.

    Every triangle in page_classification must be matched by a user triangle
    with the same id and level >= the page's required level.
    """
    if not page_classification:
        return True
    user_map = {t.id: t.level for t in user_triangles}
    for required in page_classification:
        user_level = user_map.get(required.id)
        if user_level is None or user_level < required.level:
            return False
    return True
