"""SQLite setup — wired in Slice 1, used by the flow from Slice 6."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./waypoint.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Called once at startup.

    Slice 1 wires the schema so Slice 6 (guards + audit persistence) builds
    on it directly; the recovery flow itself does not read/write SQLite yet.
    """
    from app.db import schema  # noqa: F401  (import registers the tables)

    Base.metadata.create_all(bind=engine)
