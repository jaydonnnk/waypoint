---
kind: logging_system
name: Structured Event Streaming as the Logging System (SSE + Ledger)
category: logging_system
scope:
    - '**'
source_files:
    - backend/app/agent/loop.py
    - backend/app/api/routes.py
    - backend/app/models.py
    - backend/app/db/store.py
    - backend/app/main.py
---

## What system/approach is used

The Waypoint backend does not use a traditional file-based or framework logger. Instead, it implements logging as **structured event streaming**: every meaningful step in the agent's lifecycle is emitted as a JSON-serializable dict through an `emit` callback, which is then buffered and replayed to clients via a FastAPI Server-Sent Events (SSE) endpoint (`GET /api/desk/{desk_id}/stream`). The same events also drive the frontend UI in real time.

In addition to the live SSE stream, durable audit/log entries are persisted to the SQLite database via `LedgerInput` records written by `DeskStore.settle`. This gives two complementary outputs:
- **Live operational log** — the SSE event stream consumed by the Next.js frontend.
- **Durable audit log** — the ledger table, which records trades, losses, reconciliations, and allocations with deterministic amounts and notes.

There is no `logging` module import, no log levels, no structured-log library (structlog, loguru, etc.), and no console/file sink. All "logging" is application-level event emission.

## Key files and packages

- `backend/app/agent/loop.py` — the single source of all emit calls. Every phase of the desk cycle emits typed events: `meta`, `step`, `loss`, `trade`, `mark`, `escalate`, `reconcile`, `alloc`, `error`, and `result`.
- `backend/app/api/routes.py` — owns the SSE transport. `_emit` appends each event into an in-memory `DeskState.events` list guarded by an `asyncio.Condition`, and `/desk/{desk_id}/stream` replays buffered events plus streams new ones as `data: {json}` lines with `text/event-stream` media type.
- `backend/app/models.py` — defines the domain shapes that appear in events (e.g. `DeskResult`, `Position`, `VerifyResult`, `PaymentResult`) and the `comparison_mode` flag that labels comparison-mode output.
- `backend/app/db/store.py` — persists ledger entries (`LedgerInput`) for durable audit logging; `settle` writes one transaction per cycle containing trade/loss/reconcile/alloc rows.
- `backend/app/main.py` — FastAPI bootstrap; no logging middleware or global configuration is installed.

## Architecture and conventions

### Event schema
Every emitted dict has a required `type` field that acts as the event kind discriminator. Observed types in `loop.py`:
- `meta` — mandate, meter, mode label, disclosures (emitted once at cycle start).
- `step` — human-readable progress line with an incrementing `n` counter.
- `loss` — admitted loss with position_id, amount, note, and disclosure.
- `trade` — brain judgment pick with position_id, kind, rationale.
- `mark` — reprice outcome with old/new price, search_ref, meter_used, optional stale/disclosure.
- `escalate` — human-in-the-loop prompt with esc_id, reason, options, recommendation, disclosures.
- `reconcile` — PRICE_CHANGED resolution with delta, resolution (`absorb`/`re-quote`), disclosure.
- `alloc` — seat allocation attempt with seat_ref and disclosure.
- `error` — normalized error envelope with `code` and `position_id`; raw exception messages never leave the server.
- `result` — terminal `DeskResult` serialized via Pydantic `model_dump(mode="json")`.

### Comparison mode labeling
When ticketing is blocked, the loop switches into comparison mode. In this mode, decisions are still logged (ledger entries with zero amounts and notes like `"comparison mode \u2014 decision 'book' logged, not executed"`) but no write commands run. The `mode` field on `meta` and the `comparison_mode` flag on `DeskResult` explicitly label this behavior so consumers can distinguish simulation from live execution.

### Error handling convention
Errors are emitted as structured `error` events with stable string codes (e.g. `DESK_STATE_INVALID`, `OFFER_EXPIRED`, `BUDGET_EXCEEDED`, `TICKETING_PENDING`, `DESK_CYCLE_FAILED`). Raw exception details are intentionally kept server-side; the comment in `routes.py` states: "Code-only error event (fix 3): the raw exception detail stays server-side and never rides the wire." Unhandled exceptions in the background task surface as a single `DESK_CYCLE_FAILED` error event.

### Audit trail via ledger
All financial actions are recorded as `LedgerInput(kind=..., amount=..., position_id=..., note=...)` objects collected during a cycle and flushed in one `store.settle` call. Kinds include `trade`, `loss`, `reconcile`, and `alloc`. This ledger is the durable log; the SSE stream is the live log.

### No global logger configuration
The FastAPI app in `main.py` installs only CORS middleware and includes the router. There is no request/response logging middleware, no access log, and no process-wide log level setting. Observability is entirely event-driven rather than infrastructure-observed.

## Conventions and constraints

- **Structured over free-form**: every emit is a dict with a `type` discriminator; consumers branch on `type`, not on string parsing.
- **Normalized error codes**: errors use stable string `code` values instead of exception messages, making them safe to display and test against.
- **No secrets in logs**: comments in `atlas/client.py` state that "no secret ever appears in code, args, or logs"; the pattern extends to the emit model where sensitive data is excluded from event payloads.
- **Comparison-mode parity**: comparison mode emits the same event shape as live mode (just with zero amounts and explanatory notes), so the consumer path is identical regardless of ticketing availability.
- **Deterministic P&L**: the loop computes P&L in code (`_pnl`) and emits it via `DeskResult.pnl`; LLM output is never trusted for financial figures.
- **Step budget guard**: the `step_count` field on `DeskResult` and the `step` events cap how many emit cycles run before graceful give-up.
- **Frontend contract**: `models.py` is documented as the API/frontend contract; changes to event shapes should preserve backward compatibility since the Next.js screens depend on them.