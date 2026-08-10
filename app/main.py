"""Pinkas API — Organizational Knowledge Wiki."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.infrastructure.mongo import init_client, close_client
from app.infrastructure.postgres.engine import init_engine, close_engine
from app.infrastructure.elasticsearch import init_es, close_es
from app.scheduler import setup_scheduler
from app.routers import pages, ask, produce, workflows, users, approvals, agents, bundles, agent_api, agent_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pinkas")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MongoDB is always initialized (GridFS is used for file storage regardless of db_backend)
    init_client()
    if settings.db_backend == "postgres":
        init_engine(settings.postgres_uri)
        logger.info(f"PostgreSQL backend initialized: {settings.postgres_uri}")

    # Elasticsearch for audit and retrieval logging (optional)
    if settings.es_audit_enabled:
        es_kwargs = {}
        if settings.es_api_key:
            es_kwargs["api_key"] = settings.es_api_key
        elif settings.es_username and settings.es_password:
            es_kwargs["basic_auth"] = (settings.es_username, settings.es_password)
        if settings.es_cloud_id:
            es_kwargs["cloud_id"] = settings.es_cloud_id
        hosts = [h.strip() for h in settings.es_hosts.split(",") if h.strip()]
        init_es(hosts, **es_kwargs)
        logger.info(f"Elasticsearch initialized: {hosts or settings.es_cloud_id}")

    scheduler = setup_scheduler()
    logger.info(f"Pinkas API started (backend: {settings.db_backend})")
    yield
    scheduler.shutdown()
    if settings.es_audit_enabled:
        await close_es()
    if settings.db_backend == "postgres":
        await close_engine()
    close_client()
    logger.info("Pinkas API stopped")


app = FastAPI(
    title="Pinkas API",
    description="פנקס כיס — Organizational Knowledge Wiki API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(pages.router)
app.include_router(ask.router)
app.include_router(produce.router)
app.include_router(workflows.router)
app.include_router(users.router)
app.include_router(approvals.router)
app.include_router(agents.router)
app.include_router(bundles.router)
app.include_router(agent_api.router)
app.include_router(agent_logs.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pinkas", "backend": settings.db_backend}

if __name__=="__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)