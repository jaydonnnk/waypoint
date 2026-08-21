"""Waypoint backend — FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.database import init_db


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
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}
