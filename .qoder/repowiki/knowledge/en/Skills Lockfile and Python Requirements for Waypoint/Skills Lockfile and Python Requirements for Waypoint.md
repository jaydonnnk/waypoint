---
kind: dependency_management
name: Skills Lockfile and Python Requirements for Waypoint
category: dependency_management
scope:
    - '**'
source_files:
    - skills-lock.json
    - backend/requirements.txt
---

## What system/approach is used

The repository uses two distinct dependency-management mechanisms:

1. **Skills lockfile (`skills-lock.json`)** — a custom, versioned manifest that pins external *skills* (reusable skill definitions consumed by the agent runtime) to exact GitHub sources and content hashes. This is analogous to a lockfile for third-party code but scoped to skill artifacts rather than Python packages.
2. **Python `requirements.txt`** — a flat requirements file in `backend/requirements.txt` declaring runtime and test dependencies with minimum-version constraints (`>=`).

There is no `go.mod`, `package.json`, `vendor/`, or other language-specific lockfiles present at the repository root; the project appears to be primarily Python-based with skills sourced externally.

## Key files and packages

- `skills-lock.json` — the single source of truth for pinned skill dependencies. It declares:
  - `version`: schema version of the lockfile itself (`1`).
  - `skills`: a map keyed by skill name (e.g. `atlas-flight-booking`) containing:
    - `source`: the upstream identifier (`atlas-doc/atlas-flight-booking-skill`).
    - `sourceType`: the origin type (`github`).
    - `skillPath`: the path within the source repo where the skill definition lives (`skills/atlas-flight-booking/SKILL.md`).
    - `computedHash`: a SHA-256 hash of the resolved skill content, ensuring bit-for-bit reproducibility.
- `backend/requirements.txt` — lists five top-level dependencies with lower-bound version pins:
  - `fastapi>=0.115`
  - `uvicorn[standard]>=0.30`
  - `sqlalchemy>=2.0`
  - `pytest>=8.0`
  - `httpx>=0.27`

## Architecture and conventions

- **Skills are treated as first-class dependencies.** The `skills-lock.json` structure mirrors conventional lockfiles: it records not just a package name and version, but also the source repository, the exact subpath within that source, and a content hash. This allows the agent to fetch and verify skill definitions from GitHub without ambiguity.
- **Hash pinning for skills.** Each skill entry includes a `computedHash`, which acts as an integrity check. When resolving skills, the runtime can compare the fetched content against this hash to detect tampering or drift.
- **Loose versioning for Python packages.** The `requirements.txt` uses `>=` constraints rather than exact pins or ranges like `~=`, meaning installs will accept any newer compatible version. There is no `requirements.lock`, `Pipfile.lock`, `poetry.lock`, or `pyproject.toml` lockfile observed in the repository.
- **No vendoring.** Dependencies are not vendored into the repository; they are declared declaratively and resolved at install/build time.

## Conventions and constraints

Observed conventions (descriptive):
- Skills are referenced by a `<org>/<repo>` source string and a relative `skillPath` inside that source, rather than by tag or branch alone.
- Every pinned skill has a corresponding `computedHash`; adding a new skill requires computing and recording this hash.
- Python dependencies use minimum-version pins (`>=X.Y`) so that upgrades are allowed unless explicitly constrained later.

Constraints enforced by the manifests themselves:
- A skill cannot be considered pinned unless it has all four fields (`source`, `sourceType`, `skillPath`, `computedHash`) populated in `skills-lock.json`.
- The lockfile schema version (`version: 1`) must match the reader's expected format; changing it would require migration.

No additional dependency-management tooling (e.g., Dependabot, Renovate, private PyPI registry configuration, `pip.conf`, `setup.py`, `pyproject.toml`) was found in the repository.