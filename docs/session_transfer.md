# Waypoint — Session Transfer

Background handoff for a fresh session. This document supersedes the previous S1–S7 hackathon-slice version of itself — that framing is stale; this session was entirely deployment/production-readiness work, not slice-building. No project-level `CLAUDE.md` exists in this repo.

Project: **Waypoint** — a corporate travel treasury agent (mandate → book/hold judgment → P&L → weekly close). Deployed live: frontend on Vercel (`waypoint-wheat-rho.vercel.app`), backend on Render (`waypoint-j3l2.onrender.com`, free tier — no shell, no persistent disk).

---

## Completed

- **Fixed the Vercel/Render CORS break.** Frontend was falling back to `http://localhost:8000` because `NEXT_PUBLIC_API_URL` was unset on Vercel (and, on a first attempt, set as **Secret** type — which doesn't inline at Next.js build time; had to be **Config** type). Backend's `WAYPOINT_CORS_ORIGIN` also needed the trailing slash removed.
- **Built the ops-manager budget constraints form** (`36d32d4`) — `budget_total`/`authority_cap`/`contingency_pct` inputs on the start screen, sent via `SeedRequest` body, defaults preserved for backward compat.
- **Built a live-rail Docker image** (`Dockerfile.live`, separate from the existing zero-credential `Dockerfile`) that installs the `atlas-flight` CLI (needs Python ≥3.12, via its own `uv`-managed venv) so the backend can shell out to real Atlas sandbox calls instead of only the recorded-replay rail.
- **Solved free-tier keyring persistence.** Render's free plan has no shell and no persistent disk (both paid-only), so OS-keyring auth can't survive a redeploy the normal way. Extracted a **minimal** keyring file (806 bytes — just the `api-credentials`/`credentials` entries, stripped of ~150KB of cached search-offer secrets), base64'd it into an `ATLAS_KEYRING_B64` env var, and wrote `restore_atlas_keyring.sh` to decode it to the right path before `uvicorn` starts.
- **Fixed three Docker build bugs** across iterations: PATH shadowing system `python` with the CLI's own venv (`955f8fc`), `uv tool install` venvs shipping no `pip` binary — needed `uv pip install --python <venv-python>` not bare pip (`fab9131`), and `keyrings.alt` needing to be installed into *both* the app's 3.11 env and the CLI's isolated 3.12 venv (`e9923bd`).
- **Diagnosed the live-booking blocker** using a temporary debug endpoint (`fa27209`, `GET /api/debug/atlas-status` — safe, exposes only codes/booleans) since there's no shell to inspect the container directly. Found `ticketing_blocker: "TICKETING_ACTIVATION_REQUIRED"` — an ATRIP UAT-not-passed state, not a Waypoint bug.
- **Passed Flight Booking UAT (2/2 cases)** by running the real sandbox flow (`search → verify → order create → order pay → order status`) via the CLI directly, producing genuine ticketed sandbox orders (`TESTA20260826224149512` / PNR `S23932`, `TESTA20260826224506093` / PNR `S24208`) and submitting them to the ATRIP UAT form.
- **Confirmed Ticket Fulfillment UAT is structurally unsatisfiable** on Waypoint's integration and correctly removed it from UAT scope. It checks for a `getOfferPrice.do` (step=25) log entry, which belongs to a completely separate "Fulfilment API" booking path (`getOfferPrice.do → order.do → pay.do`) — not a variant of the standard flow Waypoint uses (`search.do → verify.do → order.do → pay.do`). Confirmed by grepping the entire installed CLI package source (zero references to `getOfferPrice` anywhere) and cross-checking the GitHub skill repo (0.3.12 is the only version; no newer CLI adds it).
- **Built the trip-context feature** (`27bfe7a`) — optional `team_size`/`destination_label`/`trip_purpose` fields on the Mandate, display-only, rendered as a context line on the desk run screen when non-blank.
- **Fixed a DB migration problem mid-session**: OneDrive's file sync restored a deleted stale-schema `waypoint.db` before the app could recreate it. Pivoted from drop-and-recreate to an idempotent `ALTER TABLE`-based backfill shim in `init_db()` (`1aa9015`) that self-heals any stale SQLite DB (local or deployed) on every startup.
- **Cross-checked both Qoder-built features independently** before committing (syntax checks, runtime tests, TS typecheck, a live migration test against a simulated stale DB) rather than trusting the self-report at face value. Both were accurate — no bugs found.

## Decisions

- **Two Dockerfiles, not one modified** — `Dockerfile` (recorded-rail, zero-credential-by-construction) stays untouched; `Dockerfile.live` is new and separate. Preserves the documented "zero credentials" guarantee on the original image rather than compromising it for a live-rail need.
- **Minimal keyring extraction over shipping the raw file** — the raw plaintext keyring (154KB) is bloated with cached per-search offer secrets unrelated to auth. Extracted just the two credential keys (806 bytes), verified the minimal file authenticates identically before shipping it as an env var.
- **Temporary debug endpoint over guessing across redeploys** — Render free tier has no shell; each blind redeploy-and-check cycle costs minutes. One safe diagnostic endpoint (codes/booleans only, same "branch on code never message" discipline as the rest of this codebase) replaced several rounds of speculation.
- **Deselected Ticket Fulfillment from UAT scope rather than trying to satisfy it** — confirmed via CLI source grep and upstream docs that it's checking for a different, unused API path, not a config gap. Removing it is a scope correction, not a workaround.
- **Idempotent migration shim over drop-and-recreate** — chosen after drop-and-recreate demonstrably failed (OneDrive sync restored the deleted file mid-session). Self-healing on every `init_db()` call also covers the deployed Render DB, which never got a chance to recreate cleanly either.
- **Trip-context kept to 3 fields, display-only** — dynamic route/pricing changes were explicitly scoped out as too large/risky a change; this stays additive to the proven seeded-portfolio + live-search path.

## Traps

- **PowerShell's bare `bash` resolves to a broken WSL shim**, not Git Bash — throws `.wslconfig` parse errors and fails outright. Must call Git Bash's `bash.exe` by full path (`C:\Program Files\Git\usr\bin\bash.exe`) or open a Git Bash terminal directly.
- **`uv tool install` venvs ship no `pip` binary.** `<venv>/bin/pip` doesn't exist (exit 127). Use `uv pip install --python <venv-python-path> <package>` instead.
- **Never add a `uv tool`'s own isolated venv bin dir to PATH** — it shadows the base image's system `python`, silently breaking `python -m uvicorn` with "No module named uvicorn". Only `~/.local/bin` (where `uv tool install` shims the CLI entrypoint) belongs on PATH.
- **Atlas sandbox rejects passenger names containing digits** — a test name like `TESTER/PAX1` fails `PASSENGER_INFO_INVALID`; must be alphabetic-only (`TESTER/ALEX`, etc).
- **`DUPLICATE_BOOKING_SUSPECTED` is a query-only signal per the CLI's own contract** — the only legal follow-up is polling `order status`, never re-create or re-pay. Hit this when re-running an identical route/date search too soon after a prior booking attempt.
- **Sandbox ticketing can be genuinely async** (`TICKETING_PENDING` for several minutes on some orders) — this is normal settlement latency, not a failure requiring a retry-the-whole-flow response.
- **The local git checkout silently switched to a stray branch (`qoder/pre-constraints`)** mid-session — likely Qoder's IDE, not this session. A commit landed there instead of `main` and had to be recovered via `git checkout main && git cherry-pick`. Always confirm `git branch --show-current` before assuming a commit reached `main`.

## Working Agreements

- This session acted as **cross-checker + infra/ops hands** (Docker, Render/Vercel config, Atlas CLI diagnosis) — Qoder remains the primary code builder for feature work; this session verified Qoder's output against source before endorsing, and did the deployment/DevOps work directly.
- **No AI co-author trailer on any commit** — standing instruction (see `[no-coauthor-commits]` in this user's persistent memory).
- User relays Qoder's clarifying questions here for a recommendation; wants a clear pick + reasoning formatted for pasting into Qoder's custom-answer box.
- User runs commands themselves in PowerShell/Git Bash and pastes output back — this session has no direct Render shell access (free tier) and drove all remote diagnosis through the debug endpoint and CLI calls run locally against the same Atlas sandbox credentials.
- Terse/caveman response style was active most of this session; commit messages and technical explanations stayed in full normal prose (the mode's own carve-out).

## Files Changed

On `main`, chronological:
- `36d32d4` — `frontend/app/page.tsx`, `frontend/lib/api.ts`, `backend/app/api/routes.py`, `backend/app/fixture.py` — budget constraints form + `SeedRequest`.
- `c0a2773` — `backend/Dockerfile.live` (new), `backend/scripts/restore_atlas_keyring.sh` (new) — live-rail image + keyring boot restore.
- `955f8fc` — `backend/Dockerfile.live` — PATH fix.
- `e9923bd` → `fab9131` — `backend/Dockerfile.live` — `keyrings.alt` install fix (two-step correction).
- `fa27209` — `backend/app/api/routes.py` — temporary `GET /api/debug/atlas-status` (still present, not yet removed).
- `1aa9015` — `backend/app/db/database.py` — `_backfill_mandate_columns()` idempotent migration shim.
- `27bfe7a` — `backend/app/api/routes.py`, `backend/app/db/schema.py`, `backend/app/db/store.py`, `backend/app/fixture.py`, `backend/app/models.py`, `frontend/app/desk/[deskId]/page.tsx`, `frontend/app/page.tsx`, `frontend/lib/api.ts`, `frontend/lib/types.ts` — trip-context fields.

Untracked, never committed: `backend/scripts/uat_book.sh` — local dev helper that runs a full UAT sandbox booking (`search → verify → order create → pay → status`) and prints the order no. + PNR for the ATRIP form. Sandbox-only, no secrets.

**Operational state (not in git):**
- Render env vars: `WAYPOINT_ATLAS_MODE=live`, `WAYPOINT_LIVE_BOOKING=1`, `WAYPOINT_CORS_ORIGIN=https://waypoint-wheat-rho.vercel.app` (no trailing slash), `WAYPOINT_ESCALATION_WAIT=5`, `ATLAS_KEYRING_B64=<minimal keyring b64>`; Dockerfile Path set to `backend/Dockerfile.live`.
- Vercel env var: `NEXT_PUBLIC_API_URL=<Render backend URL>`, type **Config** (not Secret).

## Open Work

- **ATRIP account-level ticketing activation** — the single blocker on everything downstream. Flight Booking UAT passed (2/2, real ticketed orders). Ticket Fulfillment correctly removed from scope. Refund and Webhook Notification remain unstarted/pending in UAT scope. Per research surfaced late in the session, activation to LIVE status requires action by an ATRIP customer manager — not self-serve and, per that research, not gated on the remaining UAT modules — but this claim was not independently verified against ATRIP's own docs by this session. User was pointed at the **Service Requests** page / **My Profile** contact as the likely channel; no confirmation yet that a request was submitted.
- **Refund UAT case** (`7C PUS→CJU`) — the CLI reproducibly returns `INTERNAL_ERROR` on this exact reference route (tried 2 dates + an airline filter, all failed identically). Not yet confirmed whether this is CLI-specific or also fails in the ATRIP portal's own Fare Search UI — asked, not yet answered.
- **Webhook Notification UAT case** — not started. Needs a webhook receiver endpoint on Waypoint's backend (doesn't exist) and a registration point in ATRIP (location unconfirmed — asked about My Profile, no answer yet).
- **`/api/debug/atlas-status`** — still live on `main`. Offered for removal once live-booking is confirmed end-to-end; not removed yet since activation is still pending.
- **`backend/scripts/uat_book.sh`** — offered to commit or leave local-only; no decision made.
- **Live booking has never been observed end-to-end on the deployed app.** Search is confirmed genuinely live (real fluctuating prices seen in the SSE stream), but no desk cycle has completed with `comparison_mode: false` — entirely blocked on the ATRIP activation above.

---

## Prompt for New Chat

This continues work on **Waypoint**, a corporate travel treasury agent, deployed live: frontend on Vercel (`waypoint-wheat-rho.vercel.app`), backend on Render free tier (`waypoint-j3l2.onrender.com`, no shell, no persistent disk). The previous session (documented in this file's sections above) was entirely deployment/production-readiness work: fixing the Vercel/Render CORS misconfiguration, building an ops-manager budget-constraints form, standing up a separate live-rail Docker image (`backend/Dockerfile.live`) that installs the real `atlas-flight` CLI, solving free-tier OS-keyring persistence via a base64 env var + boot-time restore script, adding a temporary diagnostic endpoint (`GET /api/debug/atlas-status`) to work around having no shell access, and building an optional trip-context feature on the Mandate (team size / destination / purpose, display-only).

The critical finding: Waypoint's code, deploy pipeline, keyring auth, and live Atlas search are all confirmed working correctly. The one remaining blocker for live sandbox booking is `ticketing_blocker: "TICKETING_ACTIVATION_REQUIRED"` from Atlas's own `auth status` — an ATRIP account-level activation gate, not a code problem. Flight Booking UAT (2/2 cases) has been passed with real ticketed sandbox orders. Ticket Fulfillment was correctly removed from UAT scope after confirming it checks for an unrelated "Fulfilment API" path (`getOfferPrice.do`) that Waypoint's standard-flow integration structurally never calls — this was verified by grepping the entire installed CLI package source (zero matches) and cross-checking the CLI's GitHub repo. Refund UAT is blocked by the reference route (`7C PUS→CJU`) reproducibly failing with `INTERNAL_ERROR` via the CLI — not yet confirmed whether that's CLI-specific or a broader sandbox issue. Webhook Notification UAT hasn't been started; it needs a webhook receiver endpoint Waypoint doesn't have yet, plus finding where to register a callback URL in the ATRIP portal.

The single most important standing fact: **no live (non-comparison-mode) desk cycle has ever completed on the deployed app.** Everything is built and wired correctly for it — the block is entirely external, pending action from Atlas/ATRIP's side (likely their customer manager, via a Service Request or account contact) to flip ticketing activation. Real-money risk is a non-issue either way: this is sandbox-only throughout.

A temporary debug endpoint (`/api/debug/atlas-status`) and an untracked local helper script (`backend/scripts/uat_book.sh`) both still exist from the previous session with no decision yet on removing/committing them.

No AI co-author trailer belongs on any commit in this repo.

Wait for instructions before taking any action.
