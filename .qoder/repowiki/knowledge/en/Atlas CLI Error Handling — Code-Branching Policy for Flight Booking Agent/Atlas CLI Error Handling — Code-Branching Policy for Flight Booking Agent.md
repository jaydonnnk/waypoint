---
kind: error_handling
name: Atlas CLI Error Handling — Code-Branching Policy for Flight Booking Agent
category: error_handling
scope:
    - '**'
source_files:
    - .agents/skills/atlas-flight-booking/SKILL.md
    - .agents/skills/atlas-flight-booking/references/error-handling.md
    - .agents/skills/atlas-flight-booking/references/cli-contract.md
    - .agents/skills/atlas-flight-booking/references/booking-workflow.md
---

## What system/approach is used

Error handling in this repository is defined as a **policy document** consumed by an autonomous agent skill rather than as runtime code. The `atlas-flight-booking` skill (`.agents/skills/atlas-flight-booking/`) instructs the agent to operate exclusively through the external Atlas Flight Booking CLI and to branch on stable, normalized response `code` values — never parsing free-form `message` strings. Internal service codes are explicitly excluded from user-facing output; only normalized CLI fields and stable codes from the reference are presented.

The policy is centralized in `.agents/skills/atlas-flight-booking/references/error-handling.md`, which enumerates every non-success `code` the agent may encounter across authorization, search/verification, optional services/passengers, order/payment/ticketing, and general failures, and prescribes the exact agent behavior per code (branch, stop, poll once, retry at most once, etc.).

## Key files and packages

- `.agents/skills/atlas-flight-booking/SKILL.md` — entrypoint that mandates reading `references/error-handling.md` for every non-success code and enforces the rule "branch on response `code`, never `message`, and present only normalized CLI fields".
- `.agents/skills/atlas-flight-booking/references/error-handling.md` — the canonical error-code table defining agent behavior per code, including retry limits, authorization flows, payment safeguards, and the global constraint: `retryable=true` never authorizes a different command and never authorizes a second order creation or payment attempt.
- `.agents/skills/atlas-flight-booking/references/cli-contract.md` — referenced alongside error-handling for constructing commands and interpreting responses.
- `.agents/skills/atlas-flight-booking/references/booking-workflow.md` — cross-references error-handling for terminal codes returned after side-effecting steps.

## Architecture and conventions

1. **Code-centric branching**: All error paths are keyed on stable string codes (`AUTHORIZATION_REQUIRED`, `SEARCH_NO_RESULTS`, `OFFER_EXPIRED`, `PAYMENT_BALANCE_CHECK_REQUIRED`, `SERVICE_TEMPORARILY_UNAVAILABLE`, etc.). Messages are never parsed.
2. **Normalized presentation**: Only normalized CLI fields (e.g., `data.authorization_url`, `data.order_url`, `details.ticketing_blocker`, `details.fields`) are surfaced to users; internal service codes and numeric HTTP statuses (e.g., upstream `411`) are normalized into domain codes and never exposed raw.
3. **Retry discipline**: Read-only failures with `retryable=true` permit at most one identical retry; side-effecting operations (order creation, payment) are never retried automatically. Authorization retries follow explicit user confirmation.
4. **Stop-on-exhaustion**: Codes such as `CREDENTIAL_REJECTED`, `SECURE_STORE_UNAVAILABLE`, `ORDER_NOT_FOUND`, `UNSUPPORTED_BOOKING_FLOW`, `PASSENGER_COMBINATION_UNSUPPORTED`, and `BOOKING_STATE_INVALID` direct the agent to report and stop — no recovery is attempted.
5. **User-in-the-loop checkpoints**: Authorization, price increases, seat fallback, and payment require explicit user approval before proceeding; the agent stops its turn at these gates and resumes only after the user confirms completion.
6. **Idempotency & query-only fallback**: When uncertainty exists after a side effect (payment/order), the agent queries status using the returned `order_no` instead of replaying the mutating command.
7. **Optional-service tolerance**: Unavailability of baggage, seats, or ancillary services does not block verification, order creation, payment, or ticketing — the flow continues without those options.

## Conventions and constraints

- **Never parse messages**: Branch strictly on `code`; do not interpret `message` text.
- **Keep internal causes out of user-facing output**: Do not expose internal service codes, stack traces, or raw HTTP status codes.
- **Use only normalized CLI fields and stable codes** from the reference when presenting errors to users.
- **One retry maximum for read-only operations** flagged `retryable=true`; never retry order creation or payment under any circumstance.
- **Do not reconstruct or guess state**: On `BOOKING_STATE_INVALID` / `ORDER_STATE_INVALID`, report saved state cannot continue — do not rebuild it.
- **Do not invent URLs or IDs**: If an order link is not returned, report uncertainty without fabricating one.
- **Passenger/contact field correction is scoped**: Correct only fields named in `details.fields`; never repeat rejected values.
- **Price-change handling requires new confirmation**: On `PRICE_CHANGED`, do not create another order — re-search and re-verify before asking for a fresh decision.
- **Authorization flows are bounded**: After presenting an auth URL, stop the turn; poll only after user confirmation, and resume only when `AUTHORIZED`.

These rules are enforced by the skill's own instructions (the SKILL.md references the error-handling reference for every non-success code) and by the explicit behavioral tables in `error-handling.md`; there is no separate runtime error library in this repo — the policy itself is the contract the agent must follow.