from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models import AgentORM
from app.models.agent import Agent
from app.storage.base import AgentRepository


class PostgresAgentRepository(AgentRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(self, agent_id: str) -> Agent | None:
        result = await self._s.execute(select(AgentORM).where(AgentORM.agent_id == agent_id))
        row = result.scalars().first()
        if not row:
            return None
        return Agent(
            agent_id=row.agent_id,
            name=row.name,
            user_id=row.user_id,
            api_key_hash=row.api_key_hash,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_by_api_key_hash(self, api_key_hash: str) -> Agent | None:
        result = await self._s.execute(select(AgentORM).where(AgentORM.api_key_hash == api_key_hash))
        row = result.scalars().first()
        if not row:
            return None
        return Agent(
            agent_id=row.agent_id,
            name=row.name,
            user_id=row.user_id,
            api_key_hash=row.api_key_hash,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, agent: Agent) -> None:
        orm = AgentORM(
            agent_id=agent.agent_id,
            name=agent.name,
            user_id=agent.user_id,
            api_key_hash=agent.api_key_hash,
            active=agent.active,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        self._s.add(orm)
        await self._s.flush()

    async def list_all(self) -> list[Agent]:
        result = await self._s.execute(select(AgentORM))
        rows = result.scalars().all()
        return [
            Agent(
                agent_id=row.agent_id,
                name=row.name,
                user_id=row.user_id,
                api_key_hash=row.api_key_hash,
                active=row.active,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def update_fields(self, agent_id: str, fields: dict) -> None:
        if not fields:
            return
        await self._s.execute(
            update(AgentORM).where(AgentORM.agent_id == agent_id).values(**fields)
        )
        await self._s.flush()
