---
kind: dependency_management
name: Skill Lockfile-Based Dependency Management for Atlas Skills
category: dependency_management
scope:
    - '**'
source_files:
    - skills-lock.json
    - .agents/skills/atlas-flight-booking/SKILL.md
---

This repository is a top-level workspace for the Waypoint autonomous flight disruption-recovery agent and contains no traditional code dependency manifests (no `go.mod`, `package.json`, `requirements.txt`, `Cargo.toml`, or `pyproject.toml` files). The only dependency management mechanism present is a **skill lockfile** that pins external AI skill definitions consumed by the project.

### What system/approach is used
- A JSON-based lockfile (`skills-lock.json`) pins third-party skills sourced from GitHub repositories. This is analogous to a package lockfile but scoped to *skills* rather than software libraries.
- Each entry records the skill's source repository, source type, the path within that repo where the skill lives, and a computed hash of the resolved content — ensuring reproducible skill resolution across environments.

### Key files and packages
- `skills-lock.json` — the single source of truth for pinned skill dependencies. It declares:
  - `version`: schema version of the lockfile format.
  - `skills.atlas-flight-booking.source`: `atlas-doc/atlas-flight-booking-skill` (GitHub owner/repo).
  - `skills.atlas-flight-booking.sourceType`: `github`.
  - `skills.atlas-flight-booking.skillPath`: `skills/atlas-flight-booking/SKILL.md` — the relative path inside the source repo that is resolved.
  - `skills.atlas-flight-booking.computedHash`: a SHA-like hash (`c8235f0e2881961fbcd979fbf0ca65af5fed981ce158a17213e642510fca8309`) that binds the lockfile to a specific content revision of the skill.
- `.agents/skills/atlas-flight-booking/` — the locally checked-out/resolved copy of the pinned skill, containing an `openai.yaml` agent definition and reference documents (`booking-workflow.md`, `cli-contract.md`, `error-handling.md`, `passenger-input.md`).

### Architecture and conventions
- **External skills are treated as immutable, versioned dependencies.** The lockfile pins both the source location and a content hash, so updating a skill requires regenerating the lockfile with the new hash.
- **Skills are resolved from GitHub** using an owner/repo + internal path convention (`source` + `skillPath`), rather than being vendored inline in this repo.
- **The local `.agents/skills/...` tree is the resolved artifact**, not the source of truth — the authoritative state lives in `skills-lock.json`. Changes to the local skill files should be driven by updates to the lockfile, not edited ad hoc.

### Conventions and constraints
- Every skill dependency must be declared under `skills.<name>` in `skills-lock.json`; there is no implicit discovery of skills outside the lockfile.
- The `computedHash` field acts as an integrity check: any drift between the locked hash and the actual fetched content would indicate a mismatch, enforcing deterministic builds.
- No private registry or GOPRIVATE configuration is present; skills are pulled from public GitHub sources.
- Because this repo contains no application code, there are no language-specific dependency managers (Go modules, npm, pip, Cargo) to coordinate with — the skill lockfile is the sole dependency surface.