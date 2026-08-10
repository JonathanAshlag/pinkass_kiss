import hashlib
import secrets
from datetime import datetime

from app.config import settings
from app.models.agent import Agent, AgentCreate, AgentCreateResponse, AgentUpdate
from app.models.user import User, PermissionLevel
from app.storage.base import AgentRepository, UserRepository


def _hash_api_key(raw_key: str) -> str:
    """Hash API key with pepper from settings."""
    pepper = settings.agent_api_key_pepper or ""
    return hashlib.sha256((pepper + raw_key).encode("utf-8")).hexdigest()


async def create_agent(
    data: AgentCreate,
    admin: User,
    agent_repo: AgentRepository,
    user_repo: UserRepository,
) -> AgentCreateResponse:
    """Create a new agent, either linked to an existing user or creating a new one."""
    if data.user_id:
        user = await user_repo.get(data.user_id)
        if not user:
            raise ValueError("user_id not found")
    else:
        # Create a new user for this agent — agents are read-only for now
        user = User(
            name=data.name,
            permission_level=PermissionLevel.read_only,
        )
        await user_repo.create(user)

    raw_key = secrets.token_urlsafe(32)
    agent = Agent(
        name=data.name,
        user_id=user.user_id,
        api_key_hash=_hash_api_key(raw_key),
        active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    await agent_repo.create(agent)

    return AgentCreateResponse(
        agent_id=agent.agent_id,
        name=agent.name,
        user_id=user.user_id,
        api_key=raw_key,
    )


async def update_agent(
    agent_id: str,
    data: AgentUpdate,
    agent_repo: AgentRepository,
) -> Agent | None:
    """Update an agent's name/active status. Returns None if the agent doesn't exist."""
    agent = await agent_repo.get(agent_id)
    if not agent:
        return None

    fields = data.model_dump(exclude_unset=True)
    if fields:
        fields["updated_at"] = datetime.utcnow()
        await agent_repo.update_fields(agent_id, fields)
        agent = await agent_repo.get(agent_id)
    return agent


async def rotate_api_key(
    agent_id: str,
    agent_repo: AgentRepository,
) -> str:
    """Rotate an agent's API key, invalidating the old one."""
    raw_key = secrets.token_urlsafe(32)
    await agent_repo.update_fields(
        agent_id,
        {
            "api_key_hash": _hash_api_key(raw_key),
            "updated_at": datetime.utcnow(),
        },
    )
    return raw_key
