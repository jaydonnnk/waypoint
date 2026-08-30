# Waypoint

An autonomous corporate-travel treasury desk for disrupted trips. A manager opens a desk, drops a share link into the team chat, and each traveler self-verifies by passport photo; once the roster is complete, Waypoint reprices every booked position against the live Atlas sandbox, a Qwen-powered desk brain judges **book / hold / escalate** with written rationale, and a deterministic execute wall re-checks authority and budget before a single dollar moves. Nothing books until the manager approves the priced itinerary, and every cycle lands in a ledger whose books tie out — a weekly close adds a risk-officer auditor line.

The product thesis: naive rebooking picks the cheapest reroute — one that blows the trip budget, expires before anyone confirms, or strands a teammate. Waypoint separates *judgment* (LLM, open advise gate) from *execution* (plain code, walled execute gate, fail-closed), so it can act autonomously without ever booking something it can't prove is in-mandate, in-budget, and actually ticketed.

## The safety model

- **Two gates** (ADRs 0003/0004): the advise gate is open — the LLM sees all positions, marks, and priors and narrates freely; the execute gate is walled — deterministic code re-checks every recommendation against the mandate (authority cap, budget, contingency) and fails closed on any doubt.
- **Two runtime gates on real money**: a write path runs only when BOTH hold — the human switch `WAYPOINT_LIVE_BOOKING=1` **and** the live `atlas-flight auth status` probe reports ticketing available. Either gate blocking → **comparison mode**: decisions are logged, marked, and disclosed as simulation; no write commands run.
- **Three guards**: a bounded search meter (20 searches/cycle) prevents runaway fan-out; every write is preceded by a fresh `offer verify` re-read; a position is "booked" only after `order status` asserts `TICKETED`.
- **Contract discipline**: branch on envelope `code`, never `message`; writes (`order create`, `order pay`, `seat select`) are never retried; read-only calls get at most one identical retry and only when the envelope says `retryable=true`; no LLM ever touches fare math or execution; no secret appears in code, args, or logs.

## Architecture

```
Next.js 15 / React 19 (App Router, SSE client, GSAP)
        │  REST + SSE
        ▼
FastAPI backend (Python 3.11)
  ├─ DeskAgent   — bounded orchestration loop; emits every step as a structured SSE event
  ├─ DeskBrain   — Qwen via DashScope (OpenAI-compatible endpoint); advise gate only
  ├─ AtlasClient — subprocess wrapper around the installed `atlas-flight` CLI (--json envelopes)
  ├─ Waybot      — Telegram bot: share-link roster binding, passport MRZ capture, manager approve/hold buttons
  ├─ Store       — typed SQLite persistence: mandate, positions, ledger, budgets
  └─ Auditor     — risk-officer line that challenges one trade at weekly close
```

- **Logging is event streaming**: there is no file logger; every lifecycle step is a typed event (`meta`, `step`, `loss`, `trade`, `mark`, `escalate`, `reconcile`, `alloc`, `error`, `result`) replayed over SSE and mirrored into a durable SQLite ledger.
- **Atlas integration**: the backend shells out to the pinned `atlas-flight-booking` skill CLI (`skills-lock.json`); auth lives in the OS keyring, never in the Python process. Sandbox-only — never auto-approve against production.
- **LLM fallback**: if Qwen/DashScope is unavailable, DeskBrain degrades to a deterministic prior-band rule; the desk keeps running, labeled honestly.

## Repository layout

```
backend/            FastAPI app (app/), SQLite db, pytest suite (tests/), static IATA data (data/)
frontend/           Next.js app: / (entry/mandate), /desk/[deskId] (live desk), /close/[deskId] (weekly close)
docs/plans/waypoint/  gated spec package: 00-status → 01-product → 02-architecture → 03-program-design → 04-slices
docs/plans/waypoint/_archive-visa-pivot/  the abandoned visa-rules direction, kept as record
docs/plans/waybot/    waybot gap-closure spec package (share-link roster → passport capture → approval MVP)
docs/adr/           immutable decision records 0001–0007
docs/external/      atlas-integration.md — durable Atlas CLI/auth/env/UAT operational notes
.agents/skills/     pinned atlas-flight-booking skill + references (CLI contract, error handling, workflow)
```

## Setup

Prerequisites: Python 3.11, Node.js, `uv` (for the Atlas CLI), and a DashScope API key.

```powershell
# 1. Atlas CLI (sandbox) — install + one-time auth
uv tool install atlas-flight-booking==0.3.12
atlas-flight auth login          # opens browser; then COMPLETE the handshake:
atlas-flight auth poll           # exchanges the pending token (auth status alone does NOT)
atlas-flight environment use sandbox --json

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
set DASHSCOPE_API_KEY=...        # Qwen judge; optional DASHSCOPE_BASE_URL override
set WAYPOINT_BOT_TOKEN=...       # optional: Telegram bot token; unset = app runs bot-less
.venv\Scripts\python.exe -m uvicorn app.main:app --reload   # http://localhost:8000

# 3. Frontend
cd frontend
npm install
npm run dev                      # http://localhost:3000
```

Windows note: `backend/app/atlas/client.py` sets `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` (via `setdefault`) to work around a confirmed atlas-flight 0.3.12 bug where the default Windows Credential Manager backend overflows on multi-offer searches (CredWrite error 1783). One-time per machine, `keyrings.alt` must be installed into the CLI's own venv and login re-run — see **Known issues** in `docs/external/atlas-integration.md`.

## Demo flow

**Solo desk**

1. Open `http://localhost:3000` and seed a desk (`POST /api/desk/seed`) — creates the mandate and a seeded portfolio of disrupted positions.
2. Watch `/desk/[deskId]` stream the cycle live: reprice fan-out, marks, brain judgments with rationale, escalations, admitted losses, disclosures.
3. Review `/close/[deskId]`: weekly P&L, ledger blotter, and the auditor's challenge. Reload-safe — the stream replays from the event buffer.

**Group trip (Waybot MVP)**

1. Seed with the invite gate (`team_size` + gated) — the desk parks in `awaiting_travelers` and shows a `t.me` share card.
2. Drop the share link in the team chat. Each traveler taps it (`/start?start=<token>`), sends a passport photo; Waybot extracts the MRZ, shows a confirm card, and waits for the traveler to verify.
3. When the last seat verifies, the manager enters the release code (`POST /api/desk/{id}/confirm`) and the cycle fires.
4. The desk runs and stops on the priced itinerary; the manager taps Approve (`POST /api/desk/{id}/approve`) and the pinned offer books — price and budget re-checked in code at the moment of booking. Hold drops the pin and releases the desk without a write.

Unless `WAYPOINT_LIVE_BOOKING=1` is set, the desk runs in comparison mode: real sandbox search and marks, decisions logged and marked, no orders. With the switch armed (and ticketing live), the write path verify → confirm-price → order create → pay → ticket-assertion runs for real against the sandbox.

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/desk/seed` | create mandate + seeded portfolio |
| `POST /api/desk/{id}/confirm` | release a gated desk with the manager's code (one-shot, attempt-capped, rate-limited) |
| `POST /api/desk/{id}/approve` | manager approves/holds the pinned itinerary (one-shot lifecycle CAS) |
| `GET /api/waybot` | live bot username for building the share link (null when bot-less) |
| `GET /api/desk/{id}` | desk state: positions, ledger, search meter, lifecycle |
| `GET /api/desk/{id}/stream` | SSE stream of the desk cycle (replay-safe) |
| `GET /api/desk/{id}/close` | weekly close: P&L, admitted losses, auditor line |
| `POST /api/desk/{id}/escalations/{esc_id}/decision` | human decision on an escalation |

## Tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest            # hermetic suite, no network
.venv\Scripts\python.exe -m pytest -m live    # opt-in: hits the real Atlas sandbox (read-only search)
```

Live write-path proof is double-gated behind `WAYPOINT_WRITE_PATH=1` on top of the `live` marker — it creates real sandbox orders, so it never runs by accident.

## Status

- Slices S1–S8 implemented (data foundation → Atlas write path → desk brain → reconciliation/alloc/escalation → frontend refit → hardening → risk officer/close → design refit v3). See `docs/plans/waypoint/00-status.md` and `04-slices.md`.
- Recorded-mode slices S9–S13 implemented (ADR 0005–0007): recorded Atlas replay mode (`WAYPOINT_ATLAS_MODE=recorded`), zero-credential Docker deployment, prompt-injection containment, per-rail provenance UI, and the brain-eval harness — recorded never wears the live label.
- Waybot MVP slices S1–S5 implemented (share-link roster binding → passport MRZ capture with security guards → release gate → pre-trip approval); S6–S9 (travel pack, policy filter, trip construction, duty of care) pending — see `docs/plans/waybot/00-status.md`.
- Atlas sandbox: AUTHORIZED; search and (as of the 2026-08-25 probe) ticketing both live — `docs/external/atlas-integration.md` tracks activation state and known CLI issues.

## Docs map

Start at `docs/plans/waypoint/00-status.md`. The spec package (`01`–`04`) is the build source of truth; `docs/adr/0001–0007` are the non-negotiables (ADR 0002's visa-rules approximation was superseded by the pivot in `06-idea-pivot-transfer.md`); `docs/plans/waypoint/mockups/` holds the screen designs. Built for the Alibaba Agentic Hackathon — see `docs/alibaba_agentic_hackathon_guide.md`.
