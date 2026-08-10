"""Agent authentication dependencies."""

from fastapi import Header, HTTPException

from app.container import AgentRepo, UserRepo
from app.models.agent import Agent, AgentRequestContext
from app.models.user import User
from app.services.agent_provisioning import _hash_api_key


async def get_current_agent(
    x_api_key: str = Header(...),
    agent_repo: AgentRepo = None,
    user_repo: UserRepo = None,
) -> tuple[Agent, User]:
    """Authenticate an agent by API key and resolve its linked user."""
    key_hash = _hash_api_key(x_api_key)
    agent = await agent_repo.get_by_api_key_hash(key_hash)
    if not agent or not agent.active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    user = await user_repo.get(agent.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Agent's linked user not found")

    return agent, user


async def get_agent_request_context(
    x_api_key: str = Header(...),
    x_session_id: str = Header(...),
    agent_repo: AgentRepo = None,
    user_repo: UserRepo = None,
) -> AgentRequestContext:
    """Assemble the full context for an agent API request (auth + correlation id)."""
    agent, user = await get_current_agent(x_api_key, agent_repo, user_repo)
    return AgentRequestContext(
        agent=agent,
        user=user,
        session_id=x_session_id,
    )
