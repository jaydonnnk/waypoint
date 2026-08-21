"""SQLite setup — the desk tables land in S1 (first real DB writes)."""
from __future__ import annotations

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./waypoint.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Drop-and-recreate the desk tables, then create them.

    Demo data only — drop-and-recreate per 02-architecture.md. The drop is
    guarded: it only fires when every EXISTING table is empty (a missing
    table is skipped), so a restart never destroys a desk that has data.
    """
    from app.db import schema  # noqa: F401  (import registers the tables)

    inspector = inspect(engine)
    existing = [
        table
        for table in Base.metadata.sorted_tables
        if inspector.has_table(table.name)
    ]
    with engine.connect() as conn:
        # One-line row-count check per existing table: drop ONLY when all
        # existing tables are empty (demo data only — never real state).
        all_empty = all(
            conn.execute(select(func.count()).select_from(table)).scalar() == 0
            for table in existing
        )

    if existing and all_empty:
        Base.metadata.drop_all(bind=engine)  # demo data only — drop-and-recreate per 02-architecture.md
    Base.metadata.create_all(bind=engine)
