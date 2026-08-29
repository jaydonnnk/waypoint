"""SQLite setup — the desk tables land in S1 (first real DB writes)."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# S10 (ADR 0007): env-overridable so the container can park the DB on a
# volume-mounted DIRECTORY (a named volume mounted on a FILE path
# initializes as a directory and breaks SQLite). Unset keeps the exact
# pre-S10 value — local behavior unchanged.
DATABASE_URL = os.environ.get(
    "WAYPOINT_DATABASE_URL", "sqlite:///./waypoint.db"
)

engine = create_engine(
    DATABASE_URL,
    # SQLite-only driver arg (other backends reject it): allows the
    # asyncio.to_thread seams to use connections off the creating thread.
    connect_args=(
        {"check_same_thread": False}
        if DATABASE_URL.startswith("sqlite")
        else {}
    ),
)
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


# Mandate columns added after the first shipped schema. No migration tooling
# exists; stale pre-existing DBs (e.g. a synced-backup file restoring the old
# 7-column table) must self-heal on startup, so backfill any missing columns.
_MANDATE_COLUMN_BACKFILL = (
    ("team_size", "INTEGER NOT NULL DEFAULT 1"),
    ("destination_label", "TEXT NOT NULL DEFAULT ''"),
    ("trip_purpose", "TEXT NOT NULL DEFAULT ''"),
    # Waybot lifecycle-gate columns (S1). Constant defaults so an old
    # 3-added-column DB self-heals with today's behavior (lifecycle
    # 'released'). SQLite ADD COLUMN cannot attach the invite_token index;
    # create_all builds it on fresh DBs, and the token lookup is correct
    # (just unindexed) on a backfilled one.
    ("lifecycle", "TEXT NOT NULL DEFAULT 'released'"),
    ("invite_token", "TEXT"),
    ("confirmation_code_hash", "TEXT"),
    ("approved_offer_id", "TEXT"),
    ("policy_json", "TEXT"),
    ("reapproval_count", "INTEGER NOT NULL DEFAULT 0"),
    ("code_attempts", "INTEGER NOT NULL DEFAULT 0"),
    # G4 pre-trip approval (S5): the identity snapshot taken at approval
    # (S6's pack reads it) and the hash of the per-round approval token.
    ("approved_snapshot_json", "TEXT"),
    ("approval_token_hash", "TEXT"),
)


def _ensure_invite_token_index() -> None:
    """S2 (L4 fix): the invite_token index only exists on fresh DBs built
    by create_all. Shim-upgraded DBs (ALTER TABLE ADD COLUMN) never got
    it — SQLite ADD COLUMN cannot attach an index. Now that S2's
    bind_chat does a token→desk lookup, the missing index would cause a
    full table scan. Idempotent: IF NOT EXISTS."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_mandate_invite_token "
                "ON mandate (invite_token)"
            )
        )


def _backfill_mandate_columns() -> None:
    """Idempotent ALTER TABLE ADD COLUMN for missing mandate columns.

    No-op on fresh or already-migrated DBs; SQLite supports ADD COLUMN with
    constant defaults, so no table rebuild or indexes are needed.
    """
    with engine.begin() as conn:
        present = {
            row.name for row in conn.execute(text("PRAGMA table_info(mandate)"))
        }
        for name, ddl in _MANDATE_COLUMN_BACKFILL:
            if name not in present:
                conn.execute(
                    text(f"ALTER TABLE mandate ADD COLUMN {name} {ddl}")
                )


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
    # PRAGMA-based backfill shim is SQLite-only; skip on other dialects.
    if engine.dialect.name == "sqlite":
        _backfill_mandate_columns()
        _ensure_invite_token_index()
