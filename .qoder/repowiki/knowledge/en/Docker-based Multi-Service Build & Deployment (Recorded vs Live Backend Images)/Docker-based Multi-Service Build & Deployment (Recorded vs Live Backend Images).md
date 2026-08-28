---
kind: build_system
name: Docker-based Multi-Service Build & Deployment (Recorded vs Live Backend Images)
category: build_system
scope:
    - '**'
source_files:
    - docker-compose.yml
    - backend/Dockerfile
    - backend/Dockerfile.live
    - frontend/Dockerfile
    - backend/requirements.txt
    - backend/pytest.ini
    - frontend/package.json
    - backend/scripts/prewarm.py
    - backend/scripts/restore_atlas_keyring.sh
---

## What system/approach is used

The Waypoint project builds and deploys as a **docker-compose stack** of two services — a FastAPI backend and a Next.js frontend — orchestrated from the repository root via `docker-compose.yml`. There are no Makefiles or shell build scripts at the repo root; each service carries its own Dockerfile, and the compose file is the single entry point for building, running, and health-checking the full application.

Two distinct backend images exist:
- `backend/Dockerfile` — the **recorded-rail image**, built by default in compose. It contains only the app code, recorded envelope fixtures (`data/recorded/`), and Python dependencies pinned in `requirements.txt`. No external CLI, no keyring, no sandbox credentials — it runs with `WAYPOINT_ATLAS_MODE=recorded` so every Atlas call replays captured envelopes deterministically.
- `backend/Dockerfile.live` — the **live-rail image**. It installs the `atlas-flight-booking==0.3.12` CLI into an isolated Python 3.12 venv managed by `uv`, plus `keyrings.alt` in both the app's 3.11 environment and the CLI's venv, then boots uvicorn with a startup script that restores the Atlas keyring before exec-ing the server.

The frontend uses a standard **multi-stage Node 20 Alpine build**: a `deps` stage (`npm ci`), a `build` stage (`next build` with `NEXT_STANDALONE=1`), and a minimal `runner` stage that serves the Next.js standalone output on port 3000. The API endpoint URL is injected at build time via the `NEXT_PUBLIC_API_URL` build arg (default `http://localhost:8000`).

## Key files and packages

- `docker-compose.yml` — top-level orchestrator defining `backend`, `frontend`, and a one-shot `prewarm` helper that seeds a desk and runs its recorded cycle after the backend is healthy.
- `backend/Dockerfile` — recorded-rail image definition (Python 3.11-slim, single uvicorn worker).
- `backend/Dockerfile.live` — live-rail image definition (adds `curl`, `uv`, `atlas-flight-booking==0.3.12`, `keyrings.alt`, keyring restore script).
- `frontend/Dockerfile` — multi-stage Next.js 15 / React 19 build using `node:20-alpine`.
- `backend/requirements.txt` — backend dependency manifest (`fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0`, `pytest>=8.0`, `httpx>=0.27`).
- `backend/pytest.ini` — test configuration that excludes `live` and `eval` markers by default; tests tagged `@pytest.mark.live` or `@pytest.mark.eval` must be opted into explicitly.
- `frontend/package.json` — frontend dependency manifest and npm scripts (`dev`, `build`, `start`).
- `backend/scripts/prewarm.py` — invoked by the compose `prewarm` service to warm the database and SSE replay buffer.
- `backend/scripts/restore_atlas_keyring.sh` — executed by `Dockerfile.live`'s CMD to restore sandbox credentials before uvicorn starts.

## Architecture and conventions

- **Compose-first deployment**: `docker compose up` is the canonical way to build and run the whole stack. Services depend on each other (`depends_on.backend.condition: service_healthy`) and share a named volume `waypoint-db` mounted at `/app/db` so SQLite state survives container rebuilds.
- **Recorded-by-default safety**: The default image and compose env set `WAYPOINT_ATLAS_MODE=recorded` and `WAYPOINT_LIVE_BOOKING=1`, meaning the demo never touches real providers. The compose comment calls this "ZERO CREDENTIALS" by construction — no `DASHSCOPE_API_KEY`, `SANDBOX_ACCESS_KEY`, or `SANDBOX_SECRET_KEY` are present.
- **Single-worker SQLite constraint**: Both Dockerfiles hard-code `--workers 1` in the uvicorn command line and include comments stating "Never add --workers > 1", because the backend uses asyncio-to-thread DB access against a single SQLite file.
- **Build-time config injection**: Frontend API origin is baked into the client bundle via `NEXT_PUBLIC_API_URL` build arg; backend runtime behavior is controlled entirely through environment variables (`WAYPOINT_ATLAS_MODE`, `WAYPOINT_ESCALATION_WAIT`, `WAYPOINT_DATABASE_URL`, etc.).
- **Separation of concerns between images**: Recorded and live backends are split into separate Dockerfiles rather than toggled via flags, so the zero-credential recorded image can be distributed without any risk of leaking sandbox credentials.
- **Test isolation by marker**: `pytest.ini` sets `addopts = -m "not live and not eval"`, so running `pytest` alone executes only deterministic, non-network tests. Tests that hit real APIs must be explicitly selected with `-m live` or `-m eval`.

## Conventions and constraints

- **No root-level build scripts**: All build logic lives per-service under `backend/` and `frontend/`; the repository root only contains `docker-compose.yml` and documentation.
- **SQLite persistence via volume mount**: The compose file mounts `waypoint-db:/app/db`; the Dockerfiles touch an empty `waypoint.db` as a fallback for plain `docker run` without the volume.
- **Health checks gate orchestration**: The backend exposes `/api/health`; compose uses a `CMD python -c "import urllib.request..."` healthcheck with a 10s start period and 12 retries before the `prewarm` service starts.
- **Frontend standalone mode**: The frontend Dockerfile sets `NEXT_STANDALONE=1` and copies both `.next/standalone` and `.next/static` into the runner image, producing a self-contained Node server.
- **Live image requires explicit opt-in**: To use the live rail you must build and run `backend/Dockerfile.live` (or override the compose image) and provide sandbox credentials; the default compose setup cannot reach real providers.
- **Dependency pinning style**: Backend deps use `>=` lower bounds in `requirements.txt`; the live atlas CLI is pinned to an exact version (`atlas-flight-booking==0.3.12`) installed via `uv tool install`.