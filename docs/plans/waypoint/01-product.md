# Product: Waypoint

## Problem
"Our travel policy says book within the fare cap. Nobody owns *when* we buy. Fares move intraday, our TMC enforces policy and processes requests, and the gap between the price we could have paid and the price we did pay is never measured, never owned, never explained."

Corporate travel spend is managed as **policy enforcement** — caps, approvals, receipts. No tool treats the travel budget as a **portfolio with market timing risk**. The buyer has no counterparty that is accountable for beating the market, and no record of where timing calls were won or lost.

## What Waypoint is (in plain words)
Waypoint is **the corporate travel treasury**: a company's travel budget run like a trading desk. One desk agent holds a **mandate** (a budget plus a per-decision authority cap, set by a CFO-style user) and makes **discretionary book-now-vs-hold timing calls** across a portfolio of **5–6 upcoming trips**. It owns a **visible P&L that includes admitted losses** ("held too long, −$62, threshold adjusted"), **auto-reconciles sandbox payments against the budget ledger**, and **autonomously allocates realized savings** (e.g. auto-funding a seat upgrade via the real Atlas `booking seat select` command). A **risk-officer auditor agent** reads the blotter and challenges one trade at the weekly close (light multi-agent flavor).

## User & the mandate
- **User:** the CFO-style **mandate holder**. Sets the mandate once (budget, per-decision authority cap, contingency %); the only later intervention is a **one-click escalation decision** when a fare spike exceeds the cap.
- **Mandate:** budget + authority cap + contingency. The agent may act freely inside it; at its edge it must escalate with two priced options and a recommendation.
- Travelers are passive beneficiaries; there is no traveler-facing surface.

## Positioning
Verified competitive scan: Navan, Spotnana, Amex GBT, TravelPerk — all are **policy enforcement + chat + human approval**. None makes discretionary timing calls under an owned mandate, none owns a P&L, none admits losses.

> "Corporate travel tools enforce policy; Waypoint is hired to beat the market — a portfolio manager with a mandate, a P&L it must defend, and losses it has to explain."

## Propositions bridged
- **05 Payments & Fintech** — B2B reconciliation, budget management, agentic commerce (primary).
- **06 Data & Analytics** — predictive travel intelligence via disclosed volatility priors + live re-query microstructure (no fake ML).
- **07 AI Agent Ecosystem** — light: the risk-officer auditor challenge at weekly close.

## The 10-second wow
**Cold open mid-trade.** Blotter live, trade toast lands — *"BOOKED DAC→LHR now, +$220 vs hold model"* — week P&L counter ticks to **+$1,840**. No setup screen, no explanation needed: money, judgment, consequences.

## What is real vs disclosed-simulated (disclose loudly)
**Real:**
- Live sandbox fares via `atlas-flight search` (one search per date; agent-side fan-out) — shown on screen as *"re-read the world before every write"*.
- Real sandbox booking rail: `offer verify` → `booking confirm-price` (only on a verify-reported price increase) → `order create` → `order pay` → `order status` asserted `TICKETED`.
- Real ancillary execution: `booking seat select` funded by realized savings.
- Authority-cap enforcement, ledger arithmetic, reconciliation — deterministic code.

**Disclosed-simulated:**
- **Sandbox money only** — no real charges, stated on screen.
- **Seeded cost bases** and historical marks for the portfolio.
- **Curated per-route-type volatility priors** — disclosed approximation, **no ML model** (ADR 0002 precedent).
- **Injected fare-spike and loss scenarios** for the demo beats.
- **Risk-officer line** generated from the blotter — not a separate service.
- **Settlement/refund legs run on our own ledger** — Atlas has no refund/cancel/change commands; Atlas is market + booking rail only.

## Success metric
**Primary:** at weekly close — P&L beats the always-book-now baseline, **zero authority-cap breaches**, every sandbox payment reconciled against the ledger, every booked position asserted `TICKETED` (not 200 OK).
**Secondary:** search-budget adherence (≤ 20 searches/cycle, meter visible), escalations always carry two priced options + a recommendation.

## Out of scope
- Real money / production payments. ML models of any kind.
- Refund, cancel, change legs on Atlas (commands do not exist).
- Flex-date search (no flex-date API; fan-out = one `atlas-flight search` per date).
- Hotels, cars, rail; multi-currency treasury/FX; RBAC/multi-tenant auth; traveler self-service.
- Portfolio capped at 5–6 trips. The escalation click is the **only** human checkpoint.
