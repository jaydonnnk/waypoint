# 0002 — Transit-visa rules are a curated approximation, stated openly

## Status
Accepted — 2026-08-20

## Context
Waypoint's hero rule checks whether a passport can legally transit each connecting airport. Open datasets (e.g. `ilyankou/passport-index-dataset`) cover **tourist/destination** visa requirements, not **transit** rules. Transit is genuinely different: airside transit is often visa-free where tourist entry is not; self-transfer (separate tickets, clear immigration + recheck bags) usually requires a visa; and many hubs have hour-gated waivers (TWOV 24/72/144h). No clean, free, global transit-visa API exists.

## Decision
Two data layers:
1. **Base layer** — the passport-index tourist matrix, used only as the *entry* fallback when a hub has no airside transit zone.
2. **Authoritative layer** — a hand-curated table keyed by **`(hub × passport-nationality)`**, not per-hub. Airside transit rules are nationality-specific (e.g. Frankfurt airside is fine for a Japanese passport but needs an ATV for others), so the unit must include nationality. Each cell records `airside_ok: yes|no|unknown`, `max_hours`, and **provenance (`source`, `last_checked`)**. Each hub also carries a coarse `has_airside_zone` flag.

**Fail-closed default:** a missing hub or missing nationality cell resolves to `unknown`, and `unknown` (like `no`) is **blocked from autonomous execution** — the agent will not auto-book it. Ticket structure (same-ticket vs self-transfer) is a *secondary messaging hint only* and never flips a verdict.

**Freshness window:** a cell is trusted for auto-execution only while `last_checked` is recent — **6 months** for curated airside cells, **3 months** for the shakier entry-fallback path (distrust the weakest data faster). Past the window → treated as `unknown` → fail-closed. This window is an explicit **proxy** for the guide's "re-read before write": price/availability gets a real live re-read via Atlas `verify`, but no live transit-visa source exists, so curated-table + freshness is the honest stand-in. The demo states this plainly and never implies live visa verification.

The curated table wins where it has a cell. Coverage and its "approximation, not legal advice" nature are **stated openly in the UI and the demo**, with per-cell provenance shown — never presented as global production-grade accuracy.

## Consequences
- The demo is accurate for its hubs and honest about its edges — turns a data limitation into a credibility signal.
- Does not scale past curated hubs without more curation — a known Operating-Scale ceiling, acknowledged rather than hidden.
- The airside-vs-self-transfer distinction is real reasoning, not a flat lookup — this is what keeps the rule non-trivial and the engine defensible.
- Never claim a boarding guarantee; the rule flags risk and legality, and the passenger/airline remain the authority.
