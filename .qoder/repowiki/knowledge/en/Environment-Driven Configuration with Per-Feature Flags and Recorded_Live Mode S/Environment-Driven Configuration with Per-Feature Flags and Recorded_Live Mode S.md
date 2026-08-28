---
kind: configuration_system
name: Environment-Driven Configuration with Per-Feature Flags and Recorded/Live Mode Switching
category: configuration_system
scope:
    - '**'
source_files:
    - backend/.env
    - backend/app/atlas/config.py
    - backend/app/main.py
    - backend/app/db/database.py
    - backend/app/agent/brain.py
    - backend/app/agent/loop.py
    - backend/app/api/routes.py
    - backend/app/atlas/client.py
    - docker-compose.yml
---

## What system/approach is used

Waypoint uses a **plain environment-variable configuration system** — no config files, no YAML/JSON loaders, no typed settings framework. Every runtime switch is read via `os.environ.get(...)` (or `os.environ.setdefault`) at import or call time. Secrets for external providers (`DASHSCOPE_API_KEY`, `SANDBOX_ACCESS_KEY`, `SANDBOX_SECRET_KEY`) are stored in a local `.env` file under `backend/.env` and loaded by the process environment; there is no explicit `python-dotenv` loader in the codebase, so they must be sourced externally (e.g. by the container/runtime). The `docker-compose.yml` is the authoritative deployment manifest that injects all operational env vars into containers.

## Key files and packages

- `backend/.env` — plaintext secrets for Atlas sandbox and DashScope LLM.
- `backend/app/main.py` — defines `WAYPOINT_CORS_ORIGIN` and merges it with hardcoded localhost defaults to configure FastAPI CORS origins.
- `backend/app/atlas/config.py` — single source of truth for the Atlas mode switch (`WAYPOINT_ATLAS_MODE`), exposing `MODE_LIVE` / `MODE_RECORDED` constants and a strict parser.
- `backend/app/db/database.py` — reads `WAYPOINT_DATABASE_URL` (defaults to `sqlite:///./waypoint.db`) and conditionally applies SQLite-only connect args.
- `backend/app/agent/brain.py` — reads `DASHSCOPE_BASE_URL` (with a project-specific default) and checks `DASHSCOPE_API_KEY` presence to decide whether to use the live LLM transport or deterministic fallback.
- `backend/app/agent/loop.py` — reads `WAYPOINT_LIVE_BOOKING` to gate write execution.
- `backend/app/api/routes.py` — reads `WAYPOINT_INJECT_SCENARIO` and `WAYPOINT_ESCALATION_WAIT` to toggle demo scenarios and escalation timing.
- `backend/app/atlas/client.py` — sets `PYTHON_KEYRING_BACKEND` via `os.environ.setdefault` to force plaintext keyring for the Atlas CLI, and relies on `atlas-flight` being on PATH.
- `docker-compose.yml` — the canonical place where `WAYPOINT_ATLAS_MODE=recorded`, `WAYPOINT_LIVE_BOOKING=1`, `WAYPOINT_ESCALATION_WAIT=5`, `WAYPOINT_DATABASE_URL`, and `WAYPOINT_CORS_ORIGIN` are injected for containerized deployments.

## Architecture and conventions

1. **Per-feature flag variables, all prefixed `WAYPOINT_`**: Operational switches consistently use the `WAYPOINT_` namespace (`WAYPOINT_ATLAS_MODE`, `WAYPOINT_LIVE_BOOKING`, `WAYPOINT_INJECT_SCENARIO`, `WAYPOINT_ESCALATION_WAIT`, `WAYPOINT_CORS_ORIGIN`, `WAYPOINT_DATABASE_URL`, `WAYPOINT_BASE_URL`, `WAYPOINT_FRONTEND_URL`). This makes them easy to grep and distinguishes them from third-party keys like `DASHSCOPE_*`.

2. **Strict parse with fail-open defaults**: Each feature has a dedicated reader function or inline check that treats unset, empty, unknown, or misspelled values as a safe default. For example, `read_atlas_mode()` in `app/atlas/config.py` only recognizes the exact value `"recorded"` (case-normalized); anything else — including whitespace-padded typos — falls through to `live`. The comment explicitly states this is intentional: "fail-to-live is the safe default here" because money safety rests on separate write gates.

3. **Defaults are documented in code comments, not in a central schema**: There is no centralized config registry. Defaults live next to the variable name alongside explanatory comments describing what happens when the var is absent (e.g. CORS defaults to `http://localhost:3000` and `http://localhost:3001`; database defaults to an in-process SQLite file).

4. **Secrets vs. flags are separated**: Provider credentials (`DASHSCOPE_API_KEY`, `SANDBOX_ACCESS_KEY`, `SANDBOX_SECRET_KEY`) are treated as required secrets checked for presence; feature toggles (`WAYPOINT_*`) are optional and have explicit defaults. Missing secrets cause graceful degradation (e.g. brain falls back to deterministic prior-band rules) rather than crashing.

5. **Recorded vs. live rail is a deploy-time switch**: `WAYPOINT_ATLAS_MODE=recorded` selects the replay client that reads from recorded envelopes instead of invoking the `atlas-flight` CLI over the network. The docker-compose stack explicitly sets this to `recorded` and intentionally omits all provider credentials, so the recorded container runs with zero secrets.

6. **Configuration is layered**: Local development uses `backend/.env` + default Python behavior; containerized deployment overrides everything via `docker-compose.yml` environment blocks. There is no precedence resolution between multiple env sources — the OS environment is the single source of truth.

## Conventions and constraints

- **Every configurable switch lives in one place**: A constant holding the env-var name (e.g. `CORS_ORIGIN_ENV = "WAYPOINT_CORS_ORIGIN"`, `ATLAS_MODE_ENV = "WAYPOINT_ATLAS_MODE"`, `LIVE_BOOKING_ENV = "WAYPOINT_LIVE_BOOKING"`) is defined at module top, then referenced by `os.environ.get(...)`. New flags should follow this pattern rather than hardcoding string literals.
- **Unknown values never crash**: The atlas mode parser, CORS origin merger, and other readers treat unexpected input as their safe default rather than raising. This is enforced by the code's control flow and documented in docstrings/comments.
- **Write gates are independent from mode switches**: `WAYPOINT_ATLAS_MODE` controls *where* data comes from (live vs recorded), while `WAYPOINT_LIVE_BOOKING` controls *whether* writes execute. Both must be considered together; the compose file documents that disarming the live booking gate would downgrade the demo to comparison mode.
- **SQLite path must be a directory in containers**: `WAYPOINT_DATABASE_URL` is set to a volume-mounted directory path (`sqlite:////app/db/waypoint.db`) because mounting a named volume onto a file path initializes as a directory and breaks SQLite. This constraint is enforced by the compose file and the `init_db()` guarded drop-and-recreate logic.
- **Keyring backend is forced non-interactively**: `PYTHON_KEYRING_BACKEND` is pinned to `keyrings.alt.file.PlaintextKeyring` via `setdefault` so the Atlas CLI can run without interactive prompts; this is documented as an accepted tradeoff for sandbox-only credentials.
- **Frontend build-time config is separate**: The Next.js frontend receives its API URL via the `NEXT_PUBLIC_API_URL` Docker build arg, baked into the client bundle at build time — distinct from the backend's runtime env approach.