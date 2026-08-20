"""Waypoint backend — FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Wire SQLite on startup (Slice 1). The recovery flow doesn't touch it
    # yet; audit persistence lands in Slice 6.
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
