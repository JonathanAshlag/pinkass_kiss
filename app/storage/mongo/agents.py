from app.models.agent import Agent
from app.storage.base import AgentRepository


class MongoAgentRepository(AgentRepository):
    def __init__(self, db):
        self._db = db

    async def get(self, agent_id: str) -> Agent | None:
        doc = await self._db.agents.find_one({"agent_id": agent_id})
        if not doc:
            return None
        doc.pop("_id", None)
        return Agent(**doc)

    async def get_by_api_key_hash(self, api_key_hash: str) -> Agent | None:
        doc = await self._db.agents.find_one({"api_key_hash": api_key_hash})
        if not doc:
            return None
        doc.pop("_id", None)
        return Agent(**doc)

    async def create(self, agent: Agent) -> None:
        doc = agent.model_dump(mode="json")
        await self._db.agents.insert_one(doc)

    async def list_all(self) -> list[Agent]:
        agents = []
        async for doc in self._db.agents.find():
            doc.pop("_id", None)
            agents.append(Agent(**doc))
        return agents

    async def update_fields(self, agent_id: str, fields: dict) -> None:
        await self._db.agents.update_one({"agent_id": agent_id}, {"$set": fields})
