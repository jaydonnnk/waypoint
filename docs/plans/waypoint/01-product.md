# Product: Waypoint

## Problem
"My flight got cancelled. The airline app rebooked me on the cheapest alternative — but it connected through a country my passport can't transit. I found out at the gate, when they refused to board me. Now I'm stranded, re-buying a ticket I can't afford, missing the reason I traveled."

Travelers on passports with limited transit rights — many Indian, Chinese, African, and Southeast Asian passports — cannot tell which cheap connections are actually boardable for them. Every mainstream tool (Google Flights, airline apps, existing auto-rebookers) ignores the passenger's passport entirely. When a flight breaks, the cheapest reroute is often the one they legally cannot take, and nobody warns them until the gate.

## What Waypoint is (in plain words)
Waypoint is a rebooking agent that **checks the rules of your trip, not just the price.** Under the hood it's a small **rules engine**: given a broken trip and who you are (your passport), it re-plans across the real alternatives and, before it books anything, checks the rules that decide whether you can *actually take* each option. Price and time are table stakes — the rules are the part nobody else checks.

**Visa/transit is the first and sharpest rule.** But the engine is general: a "rule" is just a check that reads your itinerary + your passport and says allowed or blocked, with a reason. That generality is the product — visa is simply where it bites hardest.

## v1 — the engine, shipping with 2 live rules
v1 is the general rules engine, live with **two rules** (both read data we already have, so both are real, not mocked):
1. **Transit-visa eligibility** — can this passport legally pass through each connecting airport? (airside vs. must-clear-immigration distinguished.) *The hero rule.*
2. **Passport 6-month validity** — will this passport be rejected at entry for expiring too soon? *(Expiry is already in the Atlas booking payload — near-free, and it proves the engine isn't a one-trick visa lookup.)*

The engine is built so more rules plug into the same check interface. Rules it is designed to hold (named, not built in v1): onward-ticket / proof-of-return, health/vaccination entry, airline-legal minimum connection time, loyalty/alliance protection, corporate policy/budget, carbon budget. Naming them is the roadmap; the engine that makes them cheap to add is the v1 deliverable.

## Success metric
**Primary:** share of disrupted trips recovered to a confirmed, **rule-legal, boardable** option — with **zero gate-denial traps booked** — vs. a naive cheapest-first baseline. Demo target: across the seeded disruption set (N routes × the hero passport), 100% boardable recovery, and the agent never books an option the rules forbid but the baseline would have taken.

**Secondary:** *time-to-recovery* (seconds vs. hours on hold) and the *honest price gap* the agent pays to stay legal. Measured by comparing the agent's choice to the baseline on boardability / time / price.

## Announcement — the blog post before the feature
Your flight just got cancelled. Normally that means an hour on hold, or an app that rebooks you onto the cheapest seat it can find — even if that seat routes you through a country your passport can't legally enter, or your passport expires too soon to land. **Waypoint is the rebooking agent that reads the rules of your trip, not just the price.** The moment a leg breaks, it re-plans your whole journey and checks every alternative against the rules that decide whether you can actually take it — starting with your passport, the thing nobody else checks. Then it rebooks you and settles the fare difference on its own. No gate surprises. No stranding. You land where you meant to.

## Screens
- `mockups/01-trip-disrupted.html` — the booked itinerary with one leg flagged CANCELLED; the traveler's passport shown in context.
- `mockups/02-agent-recovering.html` — the agent working live: alternatives found, each checked against the rules, the cheap-but-illegal option struck out, the chosen legal reroute highlighted.
- `mockups/03-recovery-confirmed.html` — before/after: the rejected cheap-but-illegal option vs. the confirmed legal reroute, the fare difference auto-settled, and the new PNR/ticket issued. (Stretch: a one-line "also caught: passport expires too soon" beat, proving the engine.)
