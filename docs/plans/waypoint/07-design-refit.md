# Slice 8 — Design Refit (frontend only)

> Status: **PROPOSED — awaiting Jaydon's approval.** No behaviour changes. Pure
> visual + copy refit of the three screens. Every number, event, and API call
> stays exactly as built in Slices 1–7. Nothing here fabricates data or changes
> the contract.

## The reframe (this is the whole point)

The current UI is written for the *builder*, not the *user*. The person who
actually opens Waypoint is an **operations manager** who knows nothing about
trading desks. He just wants to **book flights for his team, stay under a
budget, and be told when he needs to make a call.** He does not know or care
what a "blotter", "mark-to-market", "authority cap", or "comparison mode" is.

So the refit has one rule: **say it the way the ops manager would say it.**

| Builder's word (now) | Ops manager sees |
|----------------------|------------------|
| mandate / desk | this booking run — "Q3 Tokyo offsite" |
| authority cap | your **auto-approve limit** ("we book under $700 on our own") |
| escalation | **needs your OK** |
| position | a **trip** (person + route + date) |
| loss admitted | "cost $X more than we expected" (or just fold into the fare) |
| blotter / mark-to-market P&L | **budget: spent vs left**, and **saved** per trip |
| comparison mode / live ticketing | a plain banner: "Preview — nothing booked yet" vs booking for real |
| search meter / query count | *hidden* — internal plumbing, ops manager doesn't care |
| reconcile / seat ref / stale mark | *hidden by default* — lives under "See the full record" |

The trading-desk machinery is **not deleted** — it's the honesty layer the
judges score, and it's real. It moves **behind a "See the full record" link**:
every price, check, and decision, disclosures and all. Two audiences, one build:
plain view on top, full receipts one click down.

## Design direction — "The team travel booker"

Calm, warm, trustworthy — closer to Navan/Ramp than a Bloomberg terminal. Light,
human, numbers where they matter (budget, fares), faces on the trips so it reads
as *people*, not positions.

### Palette (replace the current `:root`)

| Token | Hex | Role |
|-------|-----|------|
| `--paper` | `#FBFAF7` | warm off-white background |
| `--card` | `#FFFFFF` | cards |
| `--ink` | `#1B2430` | text |
| `--mut` | `#6B7684` | secondary text |
| `--line` | `#EAE7DF` | hairlines |
| `--brand` | `#0F766E` | deep teal — the "waypoint" beacon; CTAs, avatars (not office-blue, not slop-purple) |
| `--good` | `#15A66A` | on-budget / booked / saved |
| `--warn` | `#C8811E` | needs your OK |
| `--bad` | `#D64550` | over budget / couldn't book |

Color still encodes status, but gently — this is a tool you trust, not an alarm
board.

### Type
- **Figtree** for everything (400–800) — warm, rounded, friendly; reads as an
  approachable tool, not a terminal.
- **IBM Plex Mono** for figures only (fares, budget) — tabular so nothing jitters.
- No display serif. The warmth comes from copy + spacing, not a fancy face.

### Signature element
**The budget bar + the one decision.** At the top, a single clear line —
"Spent $4,240 of $5,000 · **$760 left**" — with a green bar. Directly under it,
if anything needs the human, **one amber card** that asks a plain yes/no. That
pairing (where's my budget / what do you need from me) is the whole product in
two glances.

## Screen-by-screen

### Screen 1 — Start a booking run
- Kill the essay entirely. One line: *"Book your team's flights, on budget."*
- A short, human sub-line: what Waypoint does in one sentence, no jargon.
- One button: **Start booking →**. (Seeds the mandate + opens the run —
  same flow, plain words.)
- Optional: three tiny reassurances as icon chips — "Stays under budget" ·
  "Asks before overspending" · "Never invents a fare."

### Screen 2 — The run (main screen — see `mockups/desk-v3.html`)
- **Run summary card:** the trip's name ("Q3 Tokyo offsite"), who/where/when,
  the **budget bar** (spent / left), and a status line
  ("4 booked · 1 needs your OK · 1 no seat in budget").
- **The decision** (only shows when needed): amber card, person's name, plain
  sentence — "$742, that's $42 over your $700 auto-approve limit. Book it or
  wait?" — two buttons. This is the "one human click", said in English.
- **The trips:** one card per person — avatar, name, route + time, fare, and a
  status badge (✓ Booked / Needs your OK / Couldn't book). "saved $72" shows
  when it beat the quote. The couldn't-book row says *why*, plainly: "No seat
  under your budget — Waypoint didn't guess or overspend."
- **See the full record →** at the bottom: opens the honest layer (marks,
  disclosures, meter, mode, auditor line, error codes). Nothing removed —
  demoted.
- While the run is still working, the summary reads "Booking 6 trips…" and the
  live narration becomes **one plain status line**, not a scrolling terminal.

### Screen 3 — Done / summary
- A plain headline for the outcome: "All set — 4 of 6 booked" /
  "Waiting on your approval" / "Stopped at the budget line".
- The numbers as friendly tiles: **spent**, **saved**, **left**, and (only in
  the record) losses/steps/breaches.
- "Preview — nothing was actually booked" vs "Booked for real" as a plain
  banner (the comparison/live disclosure, in English).
- The risk-officer/auditor line lives in the record, labeled as a
  second-opinion check — not on the happy path.

## Motion (GSAP — minimal, one moment per screen)

Free, all plugins. House rule: transform/opacity only, honor
`prefers-reduced-motion` (`gsap.matchMedia()`), clean up on unmount (`useGSAP`).

- **Budget bar fills** and the "$X left" number **counts up** as bookings land —
  the one signature moment.
- **Trip cards drop in** with a small stagger as each one resolves.
- The status line cross-fades between steps.
- Nothing else. No terminal, no ticker, no scroll effects — this is a calm tool.

## What must NOT change (guard rails for Qoder)

1. **No new numbers, ever.** Every fare, budget figure, and saving still comes
   from the SSE stream / close endpoint. The mockup values are placeholders.
2. **Replay-safety intact.** The wipe-and-rebuild reducer on stream `open` stays
   as is; any animation keys off arrival index, never appends on replay.
3. **Honesty stays reachable.** Comparison-mode banner, stale-price note,
   couldn't-book reason, auditor source, error rows — all still rendered, just
   in plain words and mostly behind "See the full record". **Demoting ≠ hiding.**
4. **Same routes, same API layer.** `lib/api.ts`, `lib/types.ts`,
   `lib/format.ts` untouched. This is `globals.css` + JSX + one animation lib.
5. **Fail-closed stays load-bearing.** "Couldn't book — didn't guess or
   overspend" and the over-limit approval wall keep their meaning; the moat is
   still visible, just in the ops manager's language.

## Build order (small, verifiable)
1. New `globals.css` tokens + Hanken/Plex fonts (no markup change) — verify.
2. Screen 2 run view (summary, decision, trip cards) — the mockup.
3. "See the full record" panel — move existing blotter/disclosure JSX here
   almost verbatim; it's already built, just relocated.
4. Screen 1 trim + Screen 3 summary.
5. GSAP pass (budget count-up, card stagger) last, behind reduced-motion.

## Snippets
- `mockups/desk-v3.html` — **the current proposal** (ops manager's run view).
- `mockups/desk-v2.html` — earlier trading-terminal take, **rejected** as too
  jargon-heavy; kept only to show the direction we moved away from.
