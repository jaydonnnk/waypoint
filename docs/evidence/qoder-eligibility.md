# Qoder Eligibility — 5-Minute Reading Path

- **Purpose:** let a judge verify Waypoint's Qoder-eligibility claim (core functionality built with Qoder; all-or-nothing category) in under five minutes.
- **Date:** 2026-08-28
- **Prepared by:** Person B (Evidence & Technical Owner), for hackathon submission.
- **Rule:** per the Alibaba Agentic Hackathon brief (`docs/alibaba_agentic_hackathon_guide.md`), core functionality must be 80%+ built with Qoder (the AI IDE). This category is all-or-nothing.

## Honesty statement

We do not cite a fabricated percentage. The application code was developed through Qoder agentic sessions end-to-end; the artifacts committed in this repository are the audit trail. What follows is the shortest path through that trail. Where a number is stated, it was measured from this repository on the date above.

Measured supporting context (2026-08-28):

- 38 commits on `main`; slice work happened on branches named after Qoder sessions (`qoder/slice-2-atlas-search`, `qoder/slice-3-rules-engine`, `qoder/slice-4-alloc-live-gate`, `qoder/slice-5-frontend-refit`, `qoder/slice-6-hardening`, `qoder/slice-7-risk-agent`, `qoder/ui-polish`).
- Application code written through these sessions: ~3,700 lines of Python in `backend/app/`, ~2,500 lines of TypeScript/TSX in `frontend/app/` + `frontend/lib/`.
- 172 hermetic backend tests passing (`pytest` in `backend/`), plus a brain-eval harness (`backend/tests/evals/`, spec at `docs/plans/waypoint/BRAIN-EVAL.md`).
- ~90 captured verification screenshots across `.qoder/` (82 at the root, 3 under `.qoder/specs/`).

## The 5-minute tour

### 1. `.qoder/repowiki/` — Qoder indexed and drove this codebase (~1 min)

The repository wiki built by the Qoder IDE: 52 articles under `en/content/` (Project Overview, Architecture Overview, Atlas Integration, Rules Engine, Security & Safety, …) plus 22 knowledge cards under `knowledge/en/`. This is IDE-generated tooling evidence: Qoder read, indexed, and maintained a model of this codebase throughout development. Regenerable by the IDE — presented as tooling evidence, not as unique proof.

### 2. `.qoder/specs/` — verification screenshots from spec-driven runs (~30 sec)

Three screen captures (`verify-screen1.png` … `verify-screen3.png`) taken by Qoder while verifying spec-driven changes against the three demo screens.

### 3. The ~82 E2E session screenshots at `.qoder/` root (~2 min) — the core proof

These are the unique, non-regenerable record of iterative Qoder-driven sessions: each screenshot was captured by the agent mid-task, at the moment a behavior was verified or a bug was reproduced. The names encode the trace:

- `slice<N>-…` — captured during delivery of build slice N, per the sequence in `docs/plans/waypoint/04-slices.md`. Example: `slice7-screen2-desk-live.png` = slice 7, demo screen 2 (the live desk), state captured at completion.
- `task<N>-step<M>-…` / `vr-task<N>-…` / `task8f-…` — captured during a numbered fix/verification task within a handoff doc. Example: `task4-step1-start-screen.png` = task 4, step 1, start screen.
- Descriptive names (`smoke-desk-settled.png`, `verify-screen2-scroll1.png`, `fix4-screen2-decide-card.png`) follow the same logic: scenario + screen + state.
- `step<N>-…` names correspond to numbered steps in slice handoff plans (see stop 4).

To trace any screenshot: read the slice/task from its name, open the matching plan or handoff doc in `docs/plans/waypoint/`, and read the step it verifies.

### 4. The spec/handoff paper trail (~1 min)

Development was structured as gated sessions handed to and executed by Qoder:

- `docs/plans/waypoint/00-status.md` — gate approvals and slice sequencing (the plan the screenshots verify).
- Handoff docs that drove Qoder sessions: `S5-HANDOFF.md`, `S6-HANDOFF.md`, `S7-HANDOFF.md`, `QODER-HANDOFF.md`, `WRITE-PATH-PAX-FIX-HANDOFF.md` (all in `docs/plans/waypoint/`).
- `docs/session_transfer.md` — the session-transfer structure used between agent sessions.
- `docs/adr/` — decision records stamped into the same flow.

### 5. Gate-test counts (~30 sec)

- **172 hermetic tests passing** — `backend/` pytest suite, including recorded-replay determinism, injection containment, provenance, and brain-eval tests.
- **~90 captured verification screenshots** — stops 2 and 3 above.
- **Brain-eval harness** — `backend/tests/evals/` with spec `docs/plans/waypoint/BRAIN-EVAL.md`.

## Evidence taxonomy (what each artifact proves)

| Artifact | Class | What it proves |
| --- | --- | --- |
| `.qoder/repowiki/` (wiki + knowledge cards) | IDE-regenerable tooling evidence | Qoder indexed this codebase and was the development environment; regenerable, so weighted as tooling evidence only. |
| `.qoder/` root screenshots (82) + `.qoder/specs/` | Unique session evidence | Non-regenerable captures from real iterative Qoder sessions; traceable to slices/tasks via naming convention. |
| `docs/plans/waypoint/` handoffs + `docs/session_transfer.md` | Paper trail | Development was structured as Qoder-executed, gated sessions. |
| `.qoder/_archive/` | Excluded | Superseded backups and diff dumps from scratch work; kept out of the repository (gitignored) to avoid confusing reviewers. Nothing in it changes any claim above. |

No secret values appear anywhere in the committed `.qoder/` content (audited 2026-08-28 before inclusion).
