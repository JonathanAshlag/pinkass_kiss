"""Pinkas API — Organizational Knowledge Wiki."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.infrastructure.mongo import init_client, close_client
from app.infrastructure.postgres.engine import init_engine, close_engine
from app.scheduler import setup_scheduler
from app.routers import pages, ask, produce, workflows, users, approvals

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
    scheduler = setup_scheduler()
    logger.info(f"Pinkas API started (backend: {settings.db_backend})")
    yield
    scheduler.shutdown()
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pinkas", "backend": settings.db_backend}

if __name__=="__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)