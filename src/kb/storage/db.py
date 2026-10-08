import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Reads <repo>/.env; real environment variables win over it.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    return os.environ["DATABASE_URL"]


def _timeouts() -> dict:
    """Server-side timeouts for the app's own connections, so a write stuck on a lock or
    a runaway statement fails (and is rolled back, undoing its staged chunks/originals)
    instead of hanging a request: KB_DB_LOCK_TIMEOUT (default 10s) and
    KB_DB_STATEMENT_TIMEOUT (default 60s), Postgres duration syntax; 0 = none."""
    options = []
    for var, setting, default in (
        ("KB_DB_LOCK_TIMEOUT", "lock_timeout", "10s"),
        ("KB_DB_STATEMENT_TIMEOUT", "statement_timeout", "60s"),
    ):
        value = os.environ.get(var, "").strip() or default
        options.append(f"-c {setting}={value}")
    return {"options": " ".join(options)}


engine = create_engine(get_database_url(), connect_args=_timeouts())
SessionLocal = sessionmaker(bind=engine)
