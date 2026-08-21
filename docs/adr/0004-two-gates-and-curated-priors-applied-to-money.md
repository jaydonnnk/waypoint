# 0004 — Two gates and curated priors, applied to money

## Status
Accepted — 2026-08-21

## Context
Waypoint pivoted from visa-aware recovery to the corporate travel treasury: an agent that makes discretionary book-now-vs-hold timing calls over a portfolio of trips and settles them on the Atlas sandbox rail. The two hardest lessons from the visa era generalize directly:

1. **0003's split** — open advice vs fail-closed execution — was built for rule legality, but the same contradiction exists for money: the AI must see and reason over everything (marks, priors, meter, remaining budget) while never being allowed to free-form inside settlement. The rubric's x0.5 penalty targets exactly "free-form generation inside a funds-settlement step".
2. **0002's precedent** — a curated approximation stated openly — was built for transit-visa data, but fare forecasting has the same shape: no honest, free, live source of future fares exists, and dressing a guess as an ML prediction would be dishonest.

## Decision
Apply both patterns to money, unchanged in shape:

- **Advise gate — open.** Qwen via DashScope owns **only judgment**: the book / hold / escalate call per position, the absorb-from-contingency vs re-quote call on `PRICE_CHANGED`, and the escalation recommendation. It sees every position, mark, prior, meter state, and budget remainder, and narrates each call — including the ones it lost.
- **Execute gate — walled, fail-closed.** Deterministic code owns **everything mechanical**: ledger arithmetic, authority-cap checks (re-checked *after* the LLM picks), reconciliation math, and the full Atlas write path (`offer verify` → conditional `booking confirm-price` → `order create` → `order pay` → `order status`). Over-cap picks escalate to the one human click; nothing settles around it. Writes are never retried; `order pay` is single-use.
- **Curated priors, disclosed.** Per-route-type volatility priors are a **curated approximation with provenance**, exactly per 0002's standard — labeled as disclosed approximation in the UI, **no ML model** of any kind. Live market input is the real re-query fan-out ("re-read the world before every write"), not a forecast.

## Consequences
- The x2 case rests on visible discretionary judgment (the advise gate), while the x0.5 trap is structurally closed: the LLM never touches ledger math, caps, order, or pay.
- Honesty is load-bearing: priors are stated as curated, losses are admitted on the blotter, and sandbox money is disclosed — the 0002 credibility play, now applied to forecasting.
- Supersedes nothing in 0002/0003 — it generalizes them; both remain accepted and cited.
- Keeps the backend's existing transport realities: subprocess CLI per ADR 0001's amendment; sandbox-only autonomy, never production.
