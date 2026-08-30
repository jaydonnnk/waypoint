---
kind: build_system
name: Local Dev Build & Test Setup (Python FastAPI + Next.js) — No CI/Containerization
category: build_system
scope:
    - '**'
source_files:
    - backend/requirements.txt
    - backend/pytest.ini
    - frontend/package.json
    - frontend/next.config.mjs
    - backend/.env
    - skills-lock.json
---

## What system/approach is used

This repository has no centralized build orchestration (no Makefile, no Dockerfile, no CI pipeline). Each subproject manages its own dependencies and uses the framework's built-in tooling:

- **Backend** (`backend/`): Python FastAPI application. Dependencies are pinned via `requirements.txt`. The server is run with `uvicorn` (declared as a dependency). Tests are executed with `pytest`, configured in `pytest.ini`.
- **Frontend** (`frontend/`): Next.js 15 / React 19 app. Build/dev/start commands are declared in `package.json` scripts (`next dev`, `next build`, `next start`).

There is no cross-compilation, container image, or release artifact produced by this repo. Deployment is assumed to happen outside the repository (e.g., manually running `uvicorn` and `next start` on a host).

## Key files and packages

- `backend/requirements.txt` — declares runtime and test dependencies: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0`, `pytest>=8.0`, `httpx>=0.27`.
- `backend/pytest.ini` — pytest configuration; defines a `live` marker for tests that hit the real Atlas sandbox, gated behind `-m live`.
- `frontend/package.json` — project metadata, version `0.1.0`, and npm scripts for development, build, and production start.
- `frontend/next.config.mjs` — minimal Next.js config enabling `reactStrictMode`.
- `backend/.env` — backend environment file (used at runtime; not checked into source control per `.gitignore`).
- `skills-lock.json` — pins external skill sources consumed by child modules (not a build artifact, but part of the reproducible setup).

## Architecture and conventions

- **Per-subproject dependency management**: each language stack keeps its own manifest (`requirements.txt` for Python, `package.json` + `package-lock.json` for Node). There is no monorepo-level lockfile or workspace manager.
- **Framework-native tooling**: the backend relies on `uvicorn` directly rather than a WSGI/ASGI entrypoint script; the frontend relies on `next build`/`next start` rather than a custom webpack config.
- **Test isolation convention**: integration tests that touch external services are marked with `@pytest.mark.live` and require opt-in execution via `pytest -m live`; they also need keyring-based auth for the Atlas sandbox.
- **No shared build root**: there is no top-level `Makefile`, `build.sh`, `Dockerfile`, or CI directory. Developers are expected to enter each subdirectory and run the framework's native commands.

## Conventions and constraints

- Backend dependencies use loose upper bounds (`>=X.Y`) rather than exact pinning; reproducibility relies on the local virtual environment (`backend/.venv/`) and `requirements.txt`.
- Frontend dependencies are pinned by `package-lock.json`; versions are specified exactly in `package.json` (e.g., `next: 15.5.23`, `react: 19.2.8`).
- Tests that call external APIs must be explicitly opted into via the `live` marker; default `pytest` runs do not hit the Atlas sandbox.
- Environment variables for the backend are loaded from `backend/.env`; secrets are excluded from version control via `.gitignore`.
- There is no automated packaging, version bumping, or deployment step defined in this repository.