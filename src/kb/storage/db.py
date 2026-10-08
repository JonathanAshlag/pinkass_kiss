from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from kb.settings import get_settings


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    return get_settings().database_url


def _timeouts() -> dict:
    """Server-side timeouts for the app's own connections, so a write stuck on a lock or
    a runaway statement fails (and is rolled back, undoing its staged chunks/originals)
    instead of hanging a request."""
    s = get_settings()
    return {"options": f"-c lock_timeout={s.db_lock_timeout} -c statement_timeout={s.db_statement_timeout}"}


engine = create_engine(get_database_url(), connect_args=_timeouts())
SessionLocal = sessionmaker(bind=engine)
