import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    return os.environ["DATABASE_URL"]


engine = create_engine(get_database_url())
SessionLocal = sessionmaker(bind=engine)
