---
kind: configuration_system
name: Waypoint Skill & Environment Configuration (Skills-Lock + Env Vars)
category: configuration_system
scope:
    - '**'
source_files:
    - skills-lock.json
    - .agents/skills/atlas-flight-booking/agents/openai.yaml
    - .agents/skills/atlas-flight-booking/SKILL.md
    - .agents/skills/atlas-flight-booking/references/cli-contract.md
    - .agents/skills/atlas-flight-booking/references/booking-workflow.md
    - .agents/skills/atlas-flight-booking/references/passenger-input.md
    - .agents/skills/atlas-flight-booking/references/error-handling.md
    - docs/plans/waypoint/02-architecture.md
    - docs/adr/0001-fork-atlas-skill-sandbox-auto-approve.md
---

## What system/approach is used

This repository is a **top-level workspace** that does not contain application source code; it hosts shared skill definitions and centralized documentation for the Waypoint autonomous flight disruption-recovery agent. The only runtime configuration present is:

1. **Skill registration via `skills-lock.json`** — pins the `atlas-flight-booking` skill from the `atlas-doc/atlas-flight-booking-skill` GitHub source, records its `skillPath`, and stores a `computedHash` for integrity verification.
2. **Agent-facing skill metadata in `.agents/skills/atlas-flight-booking/agents/openai.yaml`** — declares the skill's display name, short description, and default prompt consumed by the host agent framework.
3. **Environment variables documented in architecture docs** — external service credentials are loaded from environment at runtime: `DASHSCOPE_API_KEY` (Alibaba DashScope / Qwen) and `WAYPOINT_PUBLIC_URL` (Atlas webhook callback URL). These are explicitly called out as "value never in repo" and resolved at runtime by the backend (Python FastAPI), which is described in `docs/plans/waypoint/02-architecture.md`.
4. **Bundled data files** — passport-index CSV, curated transit-hub YAML (~6 hubs), and IATA→country map CSV are shipped with the app as static data sources rather than fetched at runtime.

There is no dedicated config module, `.env` file, YAML/TOML app config, or feature-flag system in this repository.

## Key files and packages

- `skills-lock.json` — skill lockfile pinning the Atlas Flight Booking skill source and hash.
- `.agents/skills/atlas-flight-booking/agents/openai.yaml` — agent framework skill interface metadata.
- `.agents/skills/atlas-flight-booking/SKILL.md` — skill behavior spec (also documents CLI bootstrap via `uv tool install --force --python 3.12 atlas-flight-booking==0.3.12`, minimum CLI version `0.3.12`, and OS-specific `uv` installation paths).
- `.agents/skills/atlas-flight-booking/references/cli-contract.md`, `booking-workflow.md`, `passenger-input.md`, `error-handling.md` — referenced contracts governing how the skill invokes the `atlas-flight` CLI.
- `docs/plans/waypoint/02-architecture.md` — documents the two halves (Next.js frontend, Python FastAPI backend), lists all REST + SSE endpoints, SQLite schema, and the env vars consumed by the backend (`DASHSCOPE_API_KEY`, `WAYPOINT_PUBLIC_URL`).
- `docs/adr/0001-fork-atlas-skill-sandbox-auto-approve.md` — ADR describing sandbox-only auto-approval of price/payment checkpoints in the forked skill.

## Architecture and conventions

- **Skills are declarative, not imperative.** The `SKILL.md` describes *what* the agent should do when invoked; the actual implementation lives in the forked `atlas-flight-booking` package pinned by `skills-lock.json`. This repo composes skills rather than implementing them.
- **External secrets are environment-based and excluded from the repo.** The architecture doc explicitly states `DASHSCOPE_API_KEY` value is never committed; the Atlas integration uses an OS keyring for auth (no env var) per the same doc.
- **Version pinning is explicit.** The skill enforces a minimum `atlas-flight` CLI version (`0.3.12`) and installs/upgrades via `uv tool install --force --python 3.12 atlas-flight-booking==0.3.12`; the lockfile also carries a `computedHash` for the skill source.
- **Data is bundled, not configured.** Passport, visa, and IATA lookup tables are shipped as CSV/YAML assets alongside the app rather than loaded from a configurable path.

## Conventions and constraints

- Secrets (`DASHSCOPE_API_KEY`, `WAYPOINT_PUBLIC_URL`) must be provided as environment variables at runtime; they are not stored in any config file in this repo.
- The `atlas-flight` CLI must be available on PATH at version `0.3.12` or newer; the skill bootstraps it automatically via `uv` if missing.
- Skill content under `.agents/skills/...` follows a fixed layout: `SKILL.md` (behavior), `references/` (contracts), and `agents/<provider>.yaml` (agent framework metadata).
- The lockfile format is `{ version, skills: { <name>: { source, sourceType, skillPath, computedHash } } }` and is the single source of truth for which skill versions are active.