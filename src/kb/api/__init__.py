"""REST surface over kb.service: `uvicorn kb.api:app`."""

from kb.api.app import app, get_session

__all__ = ["app", "get_session"]
