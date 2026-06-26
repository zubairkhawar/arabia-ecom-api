from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from typing import Generator

from .config import settings


def _normalize_db_url(url: str) -> str:
    """Force SQLAlchemy to use psycopg v3 (which we install via psycopg[binary]).

    Render and Heroku-style providers hand us URLs like:
        postgres://...            (legacy alias, SA can't parse)
        postgresql://...          (no driver → SA defaults to psycopg2, which we don't ship)
    Both should become:
        postgresql+psycopg://...
    URLs that already specify a driver (e.g. postgresql+psycopg://...) pass through unchanged.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


engine = create_engine(
    _normalize_db_url(settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
