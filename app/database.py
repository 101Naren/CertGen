"""Database connection.

`engine` is the connection pool; `SessionLocal()` makes a new session.
A session must never be shared between threads, so the request handler
and every background worker each create their own.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# SQLite refuses to be used from a thread other than the one that opened
# the connection unless we say it is fine (each session still stays in one thread).
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create the tables if they do not exist yet."""
    from app import models  # noqa: F401  (importing registers the tables)

    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
