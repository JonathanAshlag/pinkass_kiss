"""Admin endpoints for agent provisioning."""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AgentRepo, UserRepo
from app.models.agent import AgentCreate, AgentCreateResponse, AgentUpdate
from app.routers.deps import require_admin
from app.models.user import User
from app.services.agent_provisioning import create_agent, rotate_api_key, update_agent

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("")
async def create_agent_endpoint(
    data: AgentCreate,
    admin: User = Depends(require_admin),
    agent_repo: AgentRepo = None,
    user_repo: UserRepo = None,
) -> AgentCreateResponse:
    """Create a new agent. Returns the API key exactly once."""
    try:
        return await create_agent(data, admin, agent_repo, user_repo)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("")
async def list_agents(
    admin: User = Depends(require_admin),
    agent_repo: AgentRepo = None,
) -> dict:
    """List all agents (never returns api_key_hash)."""
    agents = await agent_repo.list_all()
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "user_id": a.user_id,
                "active": a.active,
                "created_at": a.created_at.isoformat(),
                "updated_at": a.updated_at.isoformat(),
            }
            for a in agents
        ]
    }


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    admin: User = Depends(require_admin),
    agent_repo: AgentRepo = None,
) -> dict:
    """Get a specific agent."""
    agent = await agent_repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "user_id": agent.user_id,
        "active": agent.active,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat(),
    }


@router.put("/{agent_id}")
async def update_agent_endpoint(
    agent_id: str,
    data: AgentUpdate,
    admin: User = Depends(require_admin),
    agent_repo: AgentRepo = None,
) -> dict:
    """Update an agent (name, active status)."""
    agent = await update_agent(agent_id, data, agent_repo)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "user_id": agent.user_id,
        "active": agent.active,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat(),
    }


@router.post("/{agent_id}/rotate-key")
async def rotate_key(
    agent_id: str,
    admin: User = Depends(require_admin),
    agent_repo: AgentRepo = None,
) -> dict:
    """Rotate an agent's API key. Returns the new key exactly once."""
    agent = await agent_repo.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    raw_key = await rotate_api_key(agent_id, agent_repo)
    return {"api_key": raw_key}
