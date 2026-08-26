# Deployment — Waypoint recorded-rail container stack (S10)

The stack deploys and serves with **zero credentials** (ADR 0007, quoting
Orkestr's deployment finding: *"The application deploys and runs correctly
with zero credentials… a missing credential produces a labelled fixture,
never a crash and never a silent pretence."*). The live Atlas CLI cannot be
deployed — browser OAuth + OS keyring — so the deployed app serves the
recording and says so.

## Run it

Any host with Docker + Docker Compose (a VPS works unchanged):

```sh
git clone <repo> && cd waypoint
docker compose up --build
# wait for the prewarm service to exit 0 (it logs "replay verified …")
```

Then open:

- `http://localhost:3000` — Screen 1 (mandate) → seeds a fresh desk.
- The prewarm desk is already warm: its URL is printed in the `prewarm`
  service logs (`desk:` / `close:` lines); the cold open replays the whole
  settled recorded cycle from event 0.

VPS notes:

- Frontend build-arg: the browser calls the backend directly (REST + SSE),
  so build the frontend with the PUBLIC backend origin:
  `docker compose build --build-arg NEXT_PUBLIC_API_URL=https://api.your-host frontend`.
- Backend CORS: admit the public frontend origin via
  `WAYPOINT_CORS_ORIGIN` (comma-separated) on the `backend` service; unset
  keeps `http://localhost:3000, http://localhost:3001`.
- Ports: 3000 (frontend), 8000 (backend). TLS/termination is host-side
  (nginx/caddy in front), unchanged by this stack.
- Desk state persists in the `waypoint-db` named volume (one SQLite file,
  one uvicorn worker; the volume mounts as a directory at `/app/db` and
  `WAYPOINT_DATABASE_URL` parks `waypoint.db` there — a volume mounted on a
  file path initializes as a directory and breaks SQLite).
  `docker compose down` keeps it; `docker compose down -v` resets to a
  fresh desk.

## Zero-credential table — what runs where, what is absent

| Rail | Container behavior | Credential / secret | Present? |
|---|---|---|---|
| Atlas ticketing | `RecordedAtlasClient` replays `backend/data/recorded/booking_envelopes.json` through the identical inherited parsers; no subprocess, no network; wire label **"recorded ticketing (replay)"** | `SANDBOX_ACCESS_KEY` / `SANDBOX_SECRET_KEY`, `atlas-flight` CLI, OS keyring | **ABSENT — not installed, not in env, not in the image** |
| Desk brain (Qwen) | disclosed deterministic fallback (no API key) | `DASHSCOPE_API_KEY` | **ABSENT** |
| Risk auditor | disclosed deterministic fallback line | `DASHSCOPE_API_KEY` | **ABSENT** |
| Storage | SQLite `waypoint.db` on a named volume, single uvicorn worker | none | n/a |
| Frontend | Next.js standalone server serving the client bundle | none | n/a |

Env set in compose: `WAYPOINT_ATLAS_MODE=recorded`, `WAYPOINT_LIVE_BOOKING=1`,
`WAYPOINT_ESCALATION_WAIT=5`, `WAYPOINT_DATABASE_URL=sqlite:////app/db/waypoint.db`.
`WAYPOINT_LIVE_BOOKING=1` is deliberately ARMED and still safe: the two write
gates decide *whether* writes execute; the rail decides *where envelopes come
from*. The recorded client has no transport to any provider — an unscripted
call fails closed with `NO_RECORDING` — so the execute wall replays the
capture to its honest end and can order nothing.
`WAYPOINT_ESCALATION_WAIT=5` shortens the bounded wait for the one human
escalation click — inside a container nobody clicks, and the 300s demo
default would stretch boot seeding past five minutes; the cycle expires the
escalation and gives up gracefully (fail-closed path; money never moves on
silence). Locally, unset keeps the demo default.

## Composite-recording disclosure

The shipped recording is **composite** and has **no TICKETED tail**
(`manifest.json`: `composite=true`, `ticketed_captured=false`). Slice 0's live
capture reached `PAYMENT_CONFIRMATION_REQUIRED` (order
`TESTA20260825233427052`) and then a captured pay-transport TIMEOUT; the
sandbox flapped through 17 `TICKETING_PENDING` polls with no TICKETED
envelope ever captured. Per ADR 0005's honesty rule, no TICKETED envelope is
fabricated: every scripted step in the manifest is `captured`, the manifest's
`wire_disclosure` rides the meta event, and the replayed cycle honestly ends
the way the capture ended. Recorded is never labeled live.

## Post-deploy smoke checklist

Run against the deployed stack; every box is a real-world outcome, not a
200-OK.

1. **Seed.** `docker compose logs prewarm` shows
   `pre-warm: seeded desk <id> — running its cycle...` and exits 0.
   Equivalently: `POST /api/desk/seed` returns a `desk_id`.
2. **SSE replay.** `GET /api/desk/{desk_id}/stream` (late connect) replays
   from event 0 and ends with a terminal `result` event; the prewarm log
   says `replay verified — N buffered events, terminal result present`.
3. **Honest recorded label.** The stream's `meta` event carries the
   recorded mode label + the manifest disclosure ("recorded Atlas replay —
   composite capture with NO TICKETED envelope …"); the UI shows **"recorded
   ticketing (replay)"**, never "live ticketing".
4. **Zero outbound provider calls.** With no CLI, no keyring and no
   credentials in the container there is no transport to observe — verify
   anyway: in the browser network tab the only remote origin is your backend
   (REST + one EventSource); on the host, `docker compose logs backend`
   shows no subprocess/HTTP egress, and `docker exec <backend> sh -c 'command -v atlas-flight || echo absent'` prints `absent`.
5. **Zero credentials needed.** `docker compose config` shows no
   `DASHSCOPE_API_KEY` / `SANDBOX_*` entries; the stack came up from a
   clean checkout with no `.env`.
6. **Close.** `GET /api/desk/{desk_id}/close` returns a CloseReport (200)
   with a code-computed breach count and an auditor line whose source is
   `fallback` — no key, no pretence.

## Teardown

`docker compose down` (keeps the desk DB volume) or
`docker compose down -v --rmi local` (full reset).
