---
kind: error_handling
name: Error Handling — Atlas CLI Error Codes and Recovery Agent Flow
category: error_handling
scope:
    - '**'
source_files:
    - .agents/skills/atlas-flight-booking/references/error-handling.md
    - backend/app/models.py
    - backend/app/agent/loop.py
    - backend/app/fixture.py
---

## What system/approach is used

This repository does not implement a general-purpose Python exception hierarchy or middleware-based error handling. Instead, error handling is defined in two complementary layers:

1. **Agent-facing error model** — a stable, normalized set of string `code` values consumed by the agent when calling the external Atlas CLI. The agent branches on these codes rather than parsing free-form messages.
2. **Recovery agent orchestration** — a Pydantic-driven domain model (`app/models.py`) that serializes outcomes (including failure states) over an SSE-like event stream via an `emit` callback. Errors are surfaced to the frontend as typed events, not as HTTP exceptions.

The authoritative source for the Atlas error surface is `.agents/skills/atlas-flight-booking/references/error-handling.md`, which enumerates every code the agent must handle and prescribes the exact recovery behavior per code.

## Key files and packages

- `.agents/skills/atlas-flight-booking/references/error-handling.md` — canonical reference mapping each Atlas error `code` to agent behavior (routing rule, retry policy, user messaging).
- `backend/app/models.py` — defines the Gate 3 contract types, including `RecoveryStatus = Literal["recovered", "no_legal_option", "needs_override", "failed"]`, which encodes terminal error states returned to the client.
- `backend/app/agent/loop.py` — the `RecoveryAgent.run` loop that emits typed events (`meta`, `step`, `options`, `decision`, `result`) and enforces a bounded step budget; failures are represented by emitting a `result` with a non-`recovered` status.
- `backend/app/fixture.py` — Slice-1 mock data that builds a successful `RecoveryResult`; future slices will replace this with real Atlas calls where errors would be routed per the reference doc.

## Architecture and conventions

### Normalized error codes, never parsed messages

The routing rule in the reference doc is explicit: *branch on `code`; never parse `message`. Keep internal causes out of user-facing output.* All error handling downstream of the Atlas CLI call must use only the normalized fields and stable codes documented there.

### Code-to-behavior table

Errors are grouped into categories, each with prescribed agent behavior:

- **Authorization and access**: `AUTHORIZATION_REQUIRED`, `AUTH_PENDING`, `AUTH_EXPIRED`, `AUTH_SESSION_MISSING`, `AUTH_SERVICE_UNAVAILABLE`, `SUBSCRIPTION_REQUIRED`, `SECURE_STORE_UNAVAILABLE`, `CREDENTIAL_REJECTED` — drive login flows, session restarts, or stop conditions.
- **Search and verification**: `SEARCH_NO_RESULTS`, `SEARCH_LIMIT_REACHED`, `OFFER_EXPIRED`, `BOOKING_EXPIRED`, `PRICE_CONFIRMATION_REQUIRED`, `PRICE_VERIFIED`, `PRICE_VERIFICATION_UNAVAILABLE`, `FLIGHT_UNAVAILABLE`, `BOOKING_INPUT_INVALID` — control search replay, input correction, and continuation.
- **Optional services and passengers**: `BAGGAGE_UNAVAILABLE`, `SEAT_UNAVAILABLE`, `ANCILLARY_SELECTION_INVALID`, `PASSENGER_INFO_REQUIRED`, `PASSENGER_INFO_INVALID`, `CONTACT_INFO_INVALID`, `PASSENGER_COMBINATION_UNSUPPORTED` — skip optional services or correct specific fields from `details.fields`.
- **Order, payment, ticketing**: `PAYMENT_CONFIRMATION_REQUIRED`, `PAYMENT_CONFIRMATION_INVALID`, `PRICE_CHANGED`, `ORDER_CREATION_UNAVAILABLE`, `PAYMENT_METHOD_UNAVAILABLE`, `PAYMENT_DEADLINE_EXPIRED`, `PAYMENT_BALANCE_CHECK_REQUIRED`, `ORDER_CREATION_UNKNOWN`, `DUPLICATE_BOOKING_SUSPECTED`, `PAYMENT_STATUS_UNKNOWN`, `PAYMENT_PROCESSING`, `TICKETED`, `TICKETING_PENDING`, `ORDER_CANCELLED`, `ORDER_NOT_FOUND`, `ORDER_STATUS_UNAVAILABLE`, `UNSUPPORTED_BOOKING_FLOW`, `BOOKING_STATE_INVALID`, `ORDER_STATE_INVALID` — govern order lifecycle, forbid idempotent retries on payment/order creation, and require query-only follow-ups.
- **General failures**: `INVALID_ARGUMENT`, `SERVICE_TEMPORARILY_UNAVAILABLE`, `SERVICE_REQUEST_FAILED`, `SERVICE_RESPONSE_INVALID` — limited single retries when `retryable=true`, otherwise stop.

### Retry policy

A strict invariant is enforced: `retryable=true` never authorizes a different command and never authorizes a second order creation or payment attempt. For read-only commands, the identical request may be retried at most once; for write operations (order creation, payment), no automatic retry is permitted.

### User-facing output constraints

- Internal service codes and numeric HTTP statuses (e.g., upstream `411`) must never be exposed to users; they are normalized to stable codes like `PAYMENT_BALANCE_CHECK_REQUIRED`.
- When authorization is required, the agent runs `atlas-flight auth login --json`, presents the `data.authorization_url` as a clickable link, and stops polling until the user confirms completion.
- When booking state is invalid, the agent reports it and stops without reconstructing or guessing state.

### Failure encoding in the domain model

The backend models encode terminal error states through the `RecoveryStatus` literal:
- `recovered` — success path.
- `no_legal_option` — all offers blocked by rules.
- `needs_override` — requires human intervention.
- `failed` — unrecoverable error.

These statuses are serialized into the final `result` event emitted by `RecoveryAgent.run`, so the frontend can branch on a stable field instead of parsing text.

### Bounded execution as error containment

The `RecoveryAgent` constructor takes a `step_budget` (default 12) and the loop comments note that Slice 6 enforces giving up when the budget is exceeded. This bounds runaway loops caused by repeated recoveries or retries.

## Conventions and constraints

Observed conventions (descriptive):
- Errors are modeled as structured codes + optional `details` payloads, not as thrown exceptions in the agent layer.
- The agent never auto-retries writes; retries are limited to read-only commands and capped at one attempt.
- User-facing messages are constructed from normalized CLI fields; raw service responses are never shown.
- Terminal outcomes flow through `RecoveryResult.status`, a closed enum-like literal, keeping the API contract stable across slices.

Enforced rules (from the reference doc):
- Branch on `code`; never parse `message`.
- `retryable=true` never authorizes a different command and never authorizes a second order creation or payment attempt.
- Upstream payment status `411` is normalized to `PAYMENT_BALANCE_CHECK_REQUIRED`; do not expose the numeric status.
- Do not call ticketing pending a failure; report it as continuing.
- Never create another order on `PRICE_CHANGED` or `ORDER_CREATION_UNKNOWN` / `DUPLICATE_BOOKING_SUSPECTED` — query existing state instead.