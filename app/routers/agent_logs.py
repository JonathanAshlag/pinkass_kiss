"""Admin endpoint for retrieving agent consumption logs and miss review."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.infrastructure.elasticsearch import get_es
from app.routers.deps import require_admin
from app.models.user import User

router = APIRouter(prefix="/agent", tags=["agent-logs"])


@router.get("/logs/misses")
async def get_miss_logs(
    since_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(require_admin),
) -> dict:
    """Get recent search misses for admin review. Queries the pinkas-events-* ES indices.

    Scoped to mode=search: that's the only retrieval mode where `miss` means "no confident
    match" (see dashboards/pinkas-ops.json for the full rationale). Fetch/bundle never set
    `miss`; scan used to but no longer does, since a zero-match scan isn't distinguishable
    from text that simply had no wiki terms in it. The mode filter also guards against stale
    pre-fix documents where scan still logged `miss: true`.
    """
    es = get_es()
    if es is None:
        raise HTTPException(status_code=503, detail="Elasticsearch is not available")

    try:
        result = await es.search(
            index="pinkas-events-*",
            query={
                "bool": {
                    "filter": [
                        {"term": {"event_kind": "retrieval"}},
                        {"term": {"mode": "search"}},
                        {"term": {"miss": True}},
                        {"range": {"ts": {"gte": f"now-{since_hours}h"}}},
                    ]
                }
            },
            sort=[{"ts": "desc"}],
            size=limit,
        )
        return {
            "misses": [hit["_source"] for hit in result.get("hits", {}).get("hits", [])]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query logs: {str(e)}")
