"""Waypoint backend — FastAPI application."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.database import init_db

# Default allowed origins — the exact pre-S10 list. A deployment EXTENDS
# this list via WAYPOINT_CORS_ORIGIN (comma-separated); the localhost
# defaults always stay allowed.
CORS_ORIGIN_ENV = "WAYPOINT_CORS_ORIGIN"
DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://localhost:3001"]


def _cors_origins() -> list[str]:
    """Env-overridable CORS origins (S10, ADR 0007). Unset/empty keeps
    the default localhost list; otherwise the comma-separated value
    (whitespace-trimmed, blank entries dropped) EXTENDS the defaults —
    a deployment origin never locks the local dev frontend out."""
    raw = os.environ.get(CORS_ORIGIN_ENV)
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    merged = list(DEFAULT_CORS_ORIGINS)
    for origin in origins:
        if origin not in merged:
            merged.append(origin)
    return merged


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Wire the desk tables (mandate/positions/ledger/budgets) on startup —
    # S1's first real DB writes. Demo data: drop-and-recreate guard inside.
    init_db()
    yield


app = FastAPI(title="Waypoint backend", lifespan=lifespan)

# The Next.js dev server (Screen 1-3) calls the backend cross-origin, and
# EventSource honors CORS — so the frontend origin must be allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}
