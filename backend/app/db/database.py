"""SQLite setup — the desk tables land in S1 (first real DB writes)."""
from __future__ import annotations

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./waypoint.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Legacy visa-pivot tables (fixed known list — never user input) that may
# still physically exist in waypoint.db. They are NOT in Base.metadata, so
# the all-empty drop guard inspects + drops them explicitly (fix 9).
LEGACY_TABLE_NAMES = (
    "passengers",
    "trips",
    "segments",
    "offers",
    "rule_verdicts",
    "decisions",
    "orders",
)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Drop-and-recreate the desk tables, then create them.

    Demo data only — drop-and-recreate per 02-architecture.md. The drop is
    guarded: it only fires when every EXISTING table (new desk tables AND
    legacy visa orphans) is empty (a missing table is skipped), so a
    restart never destroys a desk that has data. In that same all-empty
    branch the legacy orphan tables are dropped too (fix 9).
    """
    from app.db import schema  # noqa: F401  (import registers the tables)

    inspector = inspect(engine)
    existing = [
        table
        for table in Base.metadata.sorted_tables
        if inspector.has_table(table.name)
    ]
    legacy = [
        name for name in LEGACY_TABLE_NAMES if inspector.has_table(name)
    ]
    with engine.connect() as conn:
        # One-line row-count check per existing table: drop ONLY when all
        # existing tables (new + legacy) are empty (demo data only — never
        # real state). Any row anywhere vetoes the whole drop.
        all_empty = all(
            conn.execute(select(func.count()).select_from(table)).scalar() == 0
            for table in existing
        ) and all(
            conn.execute(
                text(f'SELECT COUNT(*) FROM "{name}"')
            ).scalar() == 0
            for name in legacy
        )

    if all_empty:
        if existing:
            Base.metadata.drop_all(bind=engine)  # demo data only — drop-and-recreate per 02-architecture.md
        if legacy:
            with engine.begin() as conn:
                for name in legacy:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
    Base.metadata.create_all(bind=engine)
