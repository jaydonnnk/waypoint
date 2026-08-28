---
kind: dependency_management
name: Multi-Module Dependency Management (Python pip, Node npm, Skills Lock)
category: dependency_management
scope:
    - '**'
source_files:
    - backend/requirements.txt
    - backend/Dockerfile
    - frontend/package.json
    - frontend/package-lock.json
    - skills-lock.json
---

## Overview

Waypoint is a polyglot repository with three distinct dependency surfaces: a Python backend, a Next.js frontend, and an external "skill" source consumed by the agent. Each surface uses its native package manager; there is no single top-level lockfile or monorepo tool.

## Python backend (`backend/`)

- **Manifest**: `backend/requirements.txt` declares six runtime/test dependencies using caret-style minimum versions (`fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0`, `pytest>=8.0`, `httpx>=0.27`). No upper bounds are pinned.
- **Lockfile**: None committed. The repo does not ship a `requirements.lock`, `Pipfile.lock`, `poetry.lock`, or `pyproject.toml`. Pinning is left to the local virtual environment under `backend/.venv/` (a standard `python -m venv` created at the repo root of the backend).
- **Installation in CI/build**: `backend/Dockerfile` copies only `requirements.txt` into the image and runs `pip install --no-cache-dir -r requirements.txt`. The Dockerfile comment explicitly calls out `requirements.txt` as "the real manifest" and notes that the image contains no keyring packages — credentials are excluded from the image by construction.
- **Virtual env**: A `.venv/` directory exists at `backend/.venv/` but is not used by the Docker build; the container installs fresh from PyPI each time. The `.venv` appears to be a developer-local artifact.
- **Private registries / vendoring**: No `--index-url`, `--extra-index-url`, `pip.conf`, `PYPI_TOKEN`, or vendored third-party wheels are present. All packages resolve against the public PyPI index.

## Frontend (`frontend/`)

- **Manifest**: `frontend/package.json` lists production dependencies (`next`, `react`, `react-dom`, `gsap`, `@gsap/react`) and dev dependencies (`typescript`, `@types/*`). Versions use caret ranges for most packages; `next`, `react`, and `react-dom` are pinned to exact versions (`15.5.23`, `19.2.8`).
- **Lockfile**: `frontend/package-lock.json` (lockfileVersion 3) is committed alongside `package.json`, providing deterministic installs via npm.
- **Registry**: No custom registry configuration is visible in `package.json`; installs resolve against the default npm registry.
- **Vendoring**: `node_modules/` is listed in the tree but not committed (it would be ignored by git); dependencies are installed on demand rather than vendored.

## External skills (`skills-lock.json`)

- **Skill pinning**: `skills-lock.json` at the repository root pins one external skill source:
  - Skill: `atlas-flight-booking`
  - Source: `atlas-doc/atlas-flight-booking-skill` on GitHub
  - Path inside source: `skills/atlas-flight-booking/SKILL.md`
  - Integrity: `computedHash` field holds a SHA-256 hash of the resolved skill content.
- This acts as a lockfile for the agent's consumable skill definitions, analogous to how `package-lock.json` locks Node deps. It ensures the same skill version is replayed deterministically.

## Conventions observed

| Surface | Manifest | Lockfile | Vendoring | Private registry |
|---|---|---|---|---|
| Python backend | `backend/requirements.txt` (minimum-version constraints) | None committed | Local `.venv/` only (not built into images) | Public PyPI only |
| Frontend | `frontend/package.json` (mix of exact and caret ranges) | `frontend/package-lock.json` committed | `node_modules/` generated locally | Default npm registry |
| Agent skills | `skills-lock.json` (source + computedHash) | N/A (hash-based) | Skill content fetched from GitHub at runtime | GitHub source |

## Constraints enforced by build artifacts

- The backend Docker image is intentionally minimal: it installs only what `requirements.txt` declares and excludes any credential-handling libraries (see Dockerfile comment about "zero credentials by construction").
- The backend image ships a pre-created empty `waypoint.db` so the service can start without external database setup, keeping deployment self-contained.
- The frontend relies on npm's lockfile for reproducible builds; no `yarn.lock` or `pnpm-lock.yaml` is present, indicating npm is the canonical tool.