# 0007 — Recorded container deployment (zero credentials)

## Status
Accepted — 2026-08-26

## Context
S9 (ADR 0005) proved the recorded rail: the desk can replay the real Slice-0 capture through the identical inherited parsers, deterministically, with no CLI, no keyring, and no network. S10 packages that rail into a deployable artifact. The question deployment has to answer is the one Orkestr's deployment plan already answered for the same Atlas integration — and we quote its core conclusion (orkestr-travel `docs/DEPLOYMENT_PLAN.md`, "The Atlas question, answered"):

> The live Atlas integration **cannot** work in a serverless deployment, and should not be made to: the `atlas-flight` CLI is a Python tool installed via `uv`; it authorises through a **browser flow** and stores credentials in the operating system's secure store. Making that work on a host would mean exporting a credential out of a secure store and into a hosting provider's environment — which is exactly the thing the credential boundary exists to prevent. **So: do not try.** The live Atlas proof is a *local* capability, demonstrated on this machine and recorded. The deployed application serves the recorded result and says so on screen. That is both the reliable choice and the honest one.

Waypoint inherits that finding whole: the live rail is a subprocess against a browser-OAuth + OS-keyring CLI — state that cannot exist inside a container, against a sandbox that demonstrably flaps. Orkestr's enabling finding is also inherited: **the application deploys and runs correctly with zero credentials** — not by accident, but because every external rail degrades to a *labelled* recording or fallback, never a crash and never a silent pretence.

## Decision
Deploy Waypoint as two containers (FastAPI backend, Next.js frontend) that start and serve with **zero credentials**, on the recorded rail:

- **The backend image is credential-free by construction.** `python:3.11-slim` + `requirements.txt` + `app/` + `scripts/` + `data/recorded/` — and nothing else: **no `atlas-flight` CLI, no `uv`, no keyring packages**, because `RecordedAtlasClient` never spawns a subprocess; the recording (`booking_envelopes.json` + `manifest.json`) ships inside the image and a deployment that claims the recording carries the recording (ADR 0005's fail-closed rule).
- **`WAYPOINT_ATLAS_MODE=recorded` is set explicitly in compose.** The mode switch defaults live (S9); the deployment opts in. With recorded selected there is no transport to any provider at all — unmatched calls fail closed with `NO_RECORDING`.
- **`WAYPOINT_LIVE_BOOKING=1` is set, and is safe.** The write gates (ADR 0003/0004) decide WHETHER writes execute; the rail decides WHERE envelopes come from. With the recorded rail armed, the execute wall runs the full two-gate write path against recorded envelopes and replays the capture to its honest end (the captured pay TIMEOUT — `ticketed_captured=false`), never reaching a provider. Disarming it would downgrade the demo to comparison mode; arming it cannot order anything, because there is no transport.
- **No `DASHSCOPE_API_KEY`.** The desk brain and the risk auditor run their disclosed deterministic fallbacks (the Qwen rail labels itself fallback — never live by omission).
- **One uvicorn worker, one SQLite file.** `--workers 1` with a named volume holding `waypoint.db` (mounted as a DIRECTORY at `/app/db` via the env-overridable `WAYPOINT_DATABASE_URL`; a named volume mounted on a file path initializes as a directory and breaks SQLite); the existing `asyncio.to_thread` DB access is the concurrency model. No multi-process SQLite.
- **Boot seeds via the prewarm pattern.** A one-shot `prewarm` service (same image, `scripts/prewarm.py` against the backend container) seeds a desk, runs its recorded cycle to settle, and verifies the SSE replay buffer — a fresh `docker compose up` lands on a ready desk whose cold-open replays from event 0. One deployment knob makes that true: `WAYPOINT_ESCALATION_WAIT` (routes-level, default unchanged) shortens the bounded wait for the one human escalation click — inside a container nobody clicks, and the 300 s demo default would stretch boot seeding past five minutes; the short wait expires the escalation and the cycle gives up gracefully on the loop's fail-closed bounded-wait path (money never moves on silence).
- **CORS origin is env-overridable** (`WAYPOINT_CORS_ORIGIN`, comma-separated) with the localhost list as the unchanged default, so a VPS deployment can admit its public origin without a code change.
- **The honesty register rides the wire.** The wire label says "recorded ticketing (replay)", the manifest's composite-recording disclosure rides the meta event, and recorded is never labeled live anywhere (ADR 0005). The deployed app serves the recording and says so.

## Consequences
- A fresh VPS needs only Docker: `docker compose up` builds and serves with zero secrets, zero keyrings, zero browser flows. Demo and submission decouple completely from sandbox health and credential custody.
- The live rail is untouched and stays the local default; the container is an explicit opt-in to recorded. Nothing in `backend/app/atlas/*` or the loop changed for deployment.
- The deployment's write path is the REAL two-gate execute wall replaying real envelopes — just with no TICKETED tail, because none was captured; the cycle ends the way the capture ended, and the UI discloses that (manifest `composite=true`, `ticketed_captured=false`).
- Single-worker + SQLite keeps the concurrency model honest; horizontal scaling would require moving off SQLite and is out of scope.
- The zero-credential property is asserted, not assumed: `docs/plans/waypoint/DEPLOYMENT.md` carries the credential table and the post-deploy smoke checklist (seed → SSE replay → recorded label → zero outbound provider calls).
