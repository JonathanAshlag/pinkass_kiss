"""Pinkas API — Organizational Knowledge Wiki."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_client, close_client
from app.scheduler import setup_scheduler
from app.routers import pages, ask, produce, workflows, users, approvals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pinkas")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_client()
    scheduler = setup_scheduler()
    logger.info("Pinkas API started")
    yield
    scheduler.shutdown()
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
    return {"status": "ok", "service": "pinkas"}
