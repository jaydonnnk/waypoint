---
kind: error_handling
name: Atlas CLI Envelope Error Codes with Typed Exceptions and Query-Only Signals
category: error_handling
scope:
    - '**'
source_files:
    - backend/app/atlas/client.py
    - backend/app/agent/loop.py
    - backend/app/agent/brain.py
    - backend/app/agent/auditor.py
    - .agents/skills/atlas-flight-booking/references/error-handling.md
    - backend/app/models.py
---

## What system/approach is used

Waypoint treats the external `atlas-flight` CLI as an untrusted remote service and normalizes every response through a **JSON envelope** (`status`, `code`, `data`, optional `retryable`). The backend never parses free-form error messages; it branches exclusively on stable, documented `code` values defined in `.agents/skills/atlas-flight-booking/references/error-handling.md`. Errors are surfaced to callers via a small hierarchy of typed Python exceptions under `app/atlas/client.py`, and the orchestration loop (`app/agent/loop.py`) translates those exceptions into normalized `{"type": "error", "code": ...}` events emitted over the streaming API.

There is no middleware framework for error handling — the pattern is **exception-driven per-call-site**, with retry logic centralized in one helper.

## Key files and packages

- `backend/app/atlas/client.py` — defines `AtlasError`, `AtlasNoResults`, `AtlasQueryOnly`, `AtlasUnknownOrder`; implements `_run_json`, `_run_read_only`, and all Atlas method wrappers (`search`, `verify`, `confirm_price`, `create_order`, `pay`, `order_status`, `poll_until_ticketed`, `follow_up_query_only`, `seat_list`, `seat_select`).
- `backend/app/agent/loop.py` — `DeskAgent.run/_write_position/_reconcile/_give_up` catch `AtlasError` / `AtlasQueryOnly` / `AtlasUnknownOrder` and emit normalized error events; also catches generic `Exception` at guard points (reload desk, reprice fan-out, comparison-mode probe) and converts them to safe codes like `DESK_STATE_INVALID`.
- `backend/app/agent/brain.py` and `backend/app/agent/auditor.py` — use `try/except Exception` with comments `# noqa: BLE001 — degrade, never crash the cycle/close`; errors are swallowed so the LLM judgment path degrades gracefully rather than aborting.
- `.agents/skills/atlas-flight-booking/references/error-handling.md` — authoritative reference table mapping each upstream `code` (e.g. `AUTHORIZATION_REQUIRED`, `PRICE_CHANGED`, `PAYMENT_BALANCE_CHECK_REQUIRED`, `SERVICE_TEMPORARILY_UNAVAILABLE`) to agent behavior, including whether a single identical retry is allowed only when `retryable=true`.
- `backend/app/models.py` — carries normalized result types (`VerifyResult`, `PaymentResult`, `OrderStatus`, `SeatSelection`) that encode query-only semantics (e.g. `PaymentResult.query_only`) instead of raw codes downstream.

## Architecture and conventions

### 1. Single retry policy in `_run_read_only`
Read-only calls go through `_run_read_only(args, timeout)`, which runs the command once and retries **exactly once** with the **identical arguments** only if the envelope reports `status != success` AND `retryable == true`. Writes (`create_order`, `pay`, `seat_select`) bypass this helper and call `_run_json` directly — they are **never retried**, even when the upstream says `retryable=true`. This enforces idempotency guarantees for side-effecting operations.

### 2. Typed exception hierarchy
```
RuntimeError
 └── AtlasError(code, message)
      ├── AtlasNoResults(reason)
      ├── AtlasQueryOnly(code, order_no=None)
      │    └── AtlasUnknownOrder
```
- `AtlasError` wraps any non-success envelope code; its `code` attribute is what gets emitted to the client.
- `AtlasQueryOnly` signals that a write may have partially succeeded but the outcome is uncertain; the **only legal follow-up** is `follow_up_query_only(signal)` → `order status` with the supplied `order_no`. It is raised for `PRICE_CHANGED`, `PAYMENT_STATUS_UNKNOWN`, `PAYMENT_PROCESSING`.
- `AtlasUnknownOrder` is a subclass for `ORDER_CREATION_UNKNOWN` / `DUPLICATE_BOOKING_SUSPECTED`: never create another order, query status only.

### 3. Per-position write wall with guards
`_write_position` executes a strict sequence per position: verify → (optional confirm-price) → budget/cap checks → create_order → pay → poll_until_ticketed. Each step is wrapped in its own try/except that emits `{"type": "error", "code": exc.code, "position_id": pos.id}` and returns without proceeding. Budget and authority-cap violations emit domain-specific codes (`BUDGET_EXCEEDED`, `AUTHORITY_CAP_EXCEEDED`) rather than propagating upstream errors.

### 4. Comparison mode as fail-closed gate
Before any write, `_comparison_mode(armed)` probes ticketing availability via `ticketing_live()`. Any exception from the probe falls through to comparison mode (no writes). When comparison mode is active, decisions are logged to the ledger but no write commands run, and the wire label explicitly states which gate blocked execution.

### 5. Graceful degradation of non-essential paths
The brain's `judge()` and auditor's `close()` wrap their work in `try/except Exception` with explicit `# noqa: BLE001` comments and comments stating the intent ("degrade, never crash the cycle/close"). Failures here do not abort the desk cycle — they fall back to deterministic defaults.

### 6. Transport-level normalization
Subprocess failures (`subprocess.TimeoutExpired`, missing binary, `OSError`, `UnicodeDecodeError`, invalid JSON envelope) are all converted to `AtlasError` with stable codes (`TIMEOUT`, `CLI_NOT_FOUND`, `BAD_ENVELOPE`, `BAD_TRANSPORT`). Internal causes stay server-side; only the normalized `code` leaves the process boundary.

### 7. No panics / no global error handlers
Python has no panic mechanism; there are no `sys.excepthook` or global exception handlers. Every failure path either raises a typed `Atlas*` exception up to the loop handler, or is caught locally and translated to an event. There is no HTTP middleware layer — the API surface is minimal and delegates to `DeskAgent.run`.

## Conventions and constraints

- **Branch on `code`, never parse `message`** — enforced by the reference doc and by every envelope parser in `client.py`.
- **Read-only calls get at most one identical retry, only when `retryable=true`** — implemented in exactly one place (`_run_read_only`); no other retry logic exists.
- **Writes are never retried** — `create_order`, `pay`, `seat_select` call `_run_json` directly; the comment in `client.py` states this as a contract rule.
- **Query-only signals require `order status` follow-up only** — `AtlasQueryOnly` and `AtlasUnknownOrder` carry `order_no` and are handled by `_reconcile` or `follow_up_query_only`; re-creating orders or re-paying is forbidden.
- **A position is booked only after `TICKETED`** — `poll_until_ticketed` asserts the real outcome; `TICKETING_PENDING` is treated as continuing, not failure.
- **Generic `Exception` catches are limited to guard/degrade points** and are annotated with `# noqa: BLE001` plus a comment explaining why swallowing is intentional.
- **Upstream numeric HTTP statuses (e.g. 411) are normalized** to stable codes (`PAYMENT_BALANCE_CHECK_REQUIRED`) before being exposed; raw status codes are never shown to users.