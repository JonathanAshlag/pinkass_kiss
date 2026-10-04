import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Reads <repo>/.env; real environment variables win over it.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    return os.environ["DATABASE_URL"]


engine = create_engine(get_database_url())
SessionLocal = sessionmaker(bind=engine)
