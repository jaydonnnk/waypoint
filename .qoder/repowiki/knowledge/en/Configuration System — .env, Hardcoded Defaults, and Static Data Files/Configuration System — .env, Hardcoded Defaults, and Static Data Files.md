---
kind: configuration_system
name: Configuration System — .env, Hardcoded Defaults, and Static Data Files
category: configuration_system
scope:
    - '**'
source_files:
    - backend/.env
    - backend/app/main.py
    - backend/app/db/database.py
    - backend/app/agent/loop.py
    - backend/app/atlas/client.py
    - backend/app/data/loaders.py
    - backend/pytest.ini
    - frontend/next.config.mjs
    - skills-lock.json
---

## What system/approach is used

The repository uses a minimal, file-based configuration approach with no dedicated configuration library:

- **Backend secrets** are loaded from a `.env` file at `backend/.env`, containing `SANDBOX_ACCESS_KEY` and `SANDBOX_SECRET_KEY`. These keys are consumed by the external `atlas-flight` CLI tool (invoked via subprocess in `app/atlas/client.py`), which reads them from its own environment/OS keyring — the Python backend never parses `.env` itself.
- **Runtime defaults** are hard-coded directly in source files (e.g. CORS origin `http://localhost:3000` in `app/main.py`, SQLite path `sqlite:///./waypoint.db` in `app/db/database.py`, `CLI_TIMEOUT_SECONDS = 60.0`, demo trip constants `DEMO_ORIGIN`, `DEMO_DEST`, `DEMO_DEP_DATE`, `DEMO_PAX` in `app/agent/loop.py`).
- **Static reference data** lives under `backend/data/` as CSV files (`iata_country.csv`, `iata_city.csv`) and is loaded at runtime via `app/data/loaders.py` using `lru_cache`-memoized functions.
- **Frontend build config** is a single Next.js config object in `frontend/next.config.mjs` with only `reactStrictMode: true`.
- **Test configuration** is declared in `backend/pytest.ini` (markers, warning filters).
- **Skill pinning** is managed by `skills-lock.json`, which pins the `atlas-flight-booking` skill to a specific GitHub source and computed hash.

There is no centralized config loader, no YAML/TOML/JSON config files for application settings, and no feature-flag system.

## Key files and packages

- `backend/.env` — sandbox credentials for the Atlas CLI.
- `backend/app/main.py` — FastAPI app bootstrap; hardcodes CORS allow-origin.
- `backend/app/db/database.py` — SQLite connection string and engine setup.
- `backend/app/agent/loop.py` — hard-coded demo trip parameters and step budget.
- `backend/app/atlas/client.py` — subprocess invocation of `atlas-flight`; relies on OS-level auth (keyring) rather than passing secrets through code.
- `backend/app/data/loaders.py` — CSV loaders for bundled IATA reference data.
- `backend/pytest.ini` — pytest markers and warning filters.
- `frontend/next.config.mjs` — Next.js build-time config.
- `skills-lock.json` — pinned external skill manifest.

## Architecture and conventions

1. **Secrets stay out of the Python process.** The backend does not read `.env` or pass credentials to the Atlas client. Instead it invokes `atlas-flight` as an external process, which consumes authentication from the installed tool's stored OS keyring + sandbox env config. This keeps secrets out of logs, args, and code paths.
2. **Defaults-first, no validation layer.** Configuration values are either hard-coded literals or plain strings in `.env`; there is no schema validation, type coercion, or default-fallback logic beyond what Python provides.
3. **Static data is co-located with the backend.** Reference tables (IATA mappings) live in `backend/data/` and are loaded relative to the package root via `Path(__file__).resolve().parents[2]`, then cached per-process with `functools.lru_cache(maxsize=1)`.
4. **External tooling is configured by environment, not by code.** The Atlas integration depends on `atlas-flight` being on PATH and authenticated via the OS keyring; misconfiguration surfaces as `CLI_NOT_FOUND` or envelope-code errors rather than typed exceptions.
5. **Build-time vs runtime separation.** Frontend config is purely compile-time (`next.config.mjs`); backend runtime config is split between `.env` (secrets), source literals (behavioral defaults), and static CSV files (reference data).

## Conventions and constraints

- Secrets are placed in `backend/.env` and must match the names expected by the Atlas CLI (`SANDBOX_ACCESS_KEY`, `SANDBOX_SECRET_KEY`).
- The Atlas CLI must be installed and discoverable on PATH; otherwise `AtlasClient._cli()` raises `AtlasError("CLI_NOT_FOUND", ...)`.
- Database persistence uses a local SQLite file named `waypoint.db` created at the backend project root; changing storage requires editing `DATABASE_URL` in `app/db/database.py`.
- CORS is explicitly whitelisted to `http://localhost:3000` in `app/main.py`; other origins will be rejected by the middleware.
- Test suites opt into live Atlas calls via the `live` marker defined in `pytest.ini`; tests tagged `@pytest.mark.live` require keyring auth and hit the real sandbox.
- External skills are pinned via `skills-lock.json` with a `computedHash` to ensure reproducible skill sources; changes to the skill source require updating this manifest.