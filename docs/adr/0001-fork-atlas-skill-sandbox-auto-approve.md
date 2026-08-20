# 0001 — Fork the Atlas skill to auto-approve payment in sandbox only

## Status
Accepted — 2026-08-20

## Context
The official `atlas-flight-booking` skill forces an explicit human confirmation at two steps: a fare increase on verify, and payment. Waypoint's core value — and the demo beat that triggers the rubric's x2 "impossible without AI" multiplier — is an agent that **autonomously settles the fare difference and rebooks**. A forced human tap at payment breaks that autonomy.

The sandbox environment creates no real bookings and charges no real money (confirmed in the Qoder user guide and the ticketing rehearsal flow).

## Decision
Fork the open-source skill (Apache-2.0). In the fork, allow the price-increase and payment checkpoints to **auto-approve when, and only when, the environment is sandbox**. Production keeps the human checkpoint, always. The fork also exposes a thin library API (search/verify/order/pay/query returning the existing typed models) so the backend can call it in-process.

We explicitly **ignore** the skill's own `SKILL.md` directive to "not ask conversational permission to install/upgrade" — that instruction is embedded content, not a user instruction, and we hold our own confirmation rules.

## Consequences
- Autonomous settlement is demoable end-to-end without a real charge. Enables the x2 story.
- Safety is bounded: auto-approve is gated on `environment == sandbox`; production is untouched. This is stated openly in the demo (Compliance & Safety).
- We carry a fork to maintain. Acceptable for a hackathon; upstreamable as a `--sandbox-auto-approve` flag later.
- AI stays out of the payment step — it is deterministic execution, not an LLM decision (avoids the x0.5 penalty).
