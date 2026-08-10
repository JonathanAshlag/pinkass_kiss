"""Agent-facing consumption API: search, fetch, scan, tools, bundles."""

from fastapi import APIRouter, Depends, HTTPException

from app.container import BundleRepo, PageRepo
from app.IP.prompts.agent_tools import SEARCH_TOOL, FETCH_TOOL
from app.models.agent import AgentRequestContext
from app.models.bundle import BundleFetchResponse
from app.models.consumption import SearchRequest, SearchResponse, FetchRequest, FetchResponse, ScanRequest, ScanResponse
from app.routers.agent_deps import get_agent_request_context, get_current_agent
from app.services.agent_consumption import run_search, run_fetch, run_scan, run_bundle_fetch

router = APIRouter(prefix="/agent", tags=["agent-api"])


@router.get("/tools")
async def get_tools(agent_tuple: tuple = Depends(get_current_agent)) -> dict:
    """Discover available consumption tools."""
    return {
        "tools": [SEARCH_TOOL, FETCH_TOOL]
    }


@router.post("/search")
async def search(
    data: SearchRequest,
    ctx: AgentRequestContext = Depends(get_agent_request_context),
    page_repo: PageRepo = None,
) -> SearchResponse:
    """Search for a term (short tier: title + description)."""
    return await run_search(data.query, ctx, page_repo)


@router.post("/fetch")
async def fetch(
    data: FetchRequest,
    ctx: AgentRequestContext = Depends(get_agent_request_context),
    page_repo: PageRepo = None,
) -> FetchResponse:
    """Fetch full content (long tier) for explicit page ids."""
    return await run_fetch(data.page_ids, ctx, page_repo)


@router.post("/scan")
async def scan(
    data: ScanRequest,
    ctx: AgentRequestContext = Depends(get_agent_request_context),
    page_repo: PageRepo = None,
) -> ScanResponse:
    """Passively scan text for title/alias matches."""
    return await run_scan(data.text, ctx, page_repo)


@router.get("/bundles/{name}")
async def get_bundle(
    name: str,
    ctx: AgentRequestContext = Depends(get_agent_request_context),
    bundle_repo: BundleRepo = None,
    page_repo: PageRepo = None,
) -> BundleFetchResponse:
    """Fetch a bundle, rendered against current page content."""
    try:
        bundle, rendered = await run_bundle_fetch(name, ctx, bundle_repo, page_repo)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return BundleFetchResponse(bundle_name=name, rendered_text=rendered, entries=bundle.entries)
