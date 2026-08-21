# Waypoint — Session Transfer

Background handoff for a fresh session. The authoritative, always-current details live in `docs/plans/waypoint/00-status.md`, the ADRs in `docs/adr/`, and `docs/plans/waypoint/QODER-HANDOFF.md`. **Read those first.** This document adds the *journey* — the reasoning, the pain points, the workarounds, and the open direction-steering paths — that the spec docs don't capture.

Project: **Waypoint** — a rules-aware flight disruption-recovery agent for the Alibaba Cloud × Atlas Agentic AI Hackathon (submission deadline **30 Aug 2026**, deliverable = 3-min demo video). The one-liner: *"the rebooking agent that reads the rules of your trip, not just the price."* Passport/entry rules are the blind spot no other rebooker checks.

---

## Completed

- **Idea validated.** Three rounds of reality-check (~25 ideas, most already served) + deep visa-data research. Waypoint survived because it sits on a structural blind spot: mainstream tools can't filter by the passenger's passport.
- **Software-factory Gates 1–4 all APPROVED** (product, architecture, program design, slice plan). Those visa-era docs are now **archived** in `docs/plans/waypoint/_archive-visa-pivot/` (superseded by the treasury spec package: current `docs/plans/waypoint/00-status.md` + `01–04`).
- **Slice 1 (tracer bullet)** — built in Qoder, reviewed, committed. Next.js + FastAPI + SSE, 3 screens, hardcoded recovery. Proven end-to-end in browser.
- **Slice 2 (real Atlas sandbox search, read path)** — built in Qoder, review caught real defects → honesty corrective → committed (`72fdc25` on branch `qoder/slice-2-atlas-search`). 17 tests + live smoke; `next build` clean.
- **ADR 0002 revised** to the honest transit-visa model after research (see Pain Points below).

## Key Decisions (and why)

- **Build workflow: Qoder (Qwen) builds ALL code from Slice 1; Claude Code is planner + reviewer only.** Reason: the "Use of Qoder" rubric category (20%) requires 80%+ of core functionality built in Qoder or it scores 0. Claude Code writes per-slice briefs, reviews Qwen's output, and maintains the planning docs; it does not write implementation code.
- **Stack:** Qwen via Alibaba DashScope (reasoning); Next.js/React + FastAPI + SQLite; Atlas via the forked skill. (ADR-backed.)
- **ADR 0001** — fork the Atlas skill to auto-approve payment in **sandbox only**, so autonomous settlement is demoable without real charges.
- **ADR 0003** — advise/execute split: the AI sees + narrates every option (open), but code walls execution to all-allowed offers (fail-closed). The LLM can never unblock a rule.
- **Co-heroes for the demo** (this session's biggest pivot — see Pain Points): passport-6-month-validity is the reliable LIVE hero; the transit-visa block is a per-option reroute-contrast that only fires live if a >24h-layover (or Schengen/US) option appears.
- **Rubric north-star:** Innovation 30 / Feasibility 30 / Use of Qoder 20 (80% gate) / Demo 20 (strict 4/2/0). Target Level-4. Three agent-failure guards (step budget, re-read-before-write, assert-real-ticket) are wired in + shown on screen.

## Pain Points & Workarounds (the important part)

1. **The visa scenario collapsed under research — twice.**
   - The original demo (Indian passport, SIN→NRT, "cheapest via Ho Chi Minh is illegal") does **not** hold: Vietnam/Korea/Thailand all allow **visa-free airside transit under 24h for ~every nationality**. So there's no simple nationality×hub trap at these hubs.
   - The only thing that creates a transit block is being forced to *enter* (>24h layover, self-transfer, or no airside zone). But **Atlas's normalized offer does not expose booking construction** (dropped `separateBookings`), and carrier-continuity is an unreliable proxy (LCC same-carrier connections are often still self-transfer). **So self-transfer cannot be honestly detected from the data.**
   - **Temptation to avoid:** rigging curated cells to force the demo, or blocking a real offer on the weak `same_ticket` heuristic. Both were rejected (honesty rule).
   - **Workaround:** co-heroes. Passport-validity carries the reliable live demo; the transit rule only *confidently* blocks on Schengen ATV / US-no-airside / >24h-layover — all data-derivable or sourced — and is proven via curated data + fixtures even when the live sandbox route can't showcase it. ADR 0002 was rewritten to say this plainly.

2. **Sandbox hub coverage is narrow.** Probed routes returned only Asian hubs (VN/KR/TH on SIN→NRT) and Indian hubs (BOM/DEL, IndiGo, on CMB/SIN/DAC→LHR). **No Schengen, US, or Gulf hubs.** So the cleanest real traps (Schengen ATV, US transit) aren't reachable on the live routes seen so far.

3. **Ticketing is not activated** (`TICKETING_ACTIVATION_REQUIRED`). All sandbox offers come back `price_status=reference` / `bookable=false`. Search works; **verify/order/pay/ticket do not** → Slice 5 (booking/settlement) is blocked until UAT test cases are completed in the ATRIP workspace. UAT modules were selected (Flight Booking [Core], Ticket Fulfillment, Webhook, Refund) but the test cases aren't done.

4. **Qwen's first Slice-2 pass fabricated a booking.** It set `chosen == rejected_cheapest` (same flight shown as both booked and rejected) and asserted a fake issued ticket while nothing was booked — violating Guard #3. **Caught in review before commit** → corrective pass made Screen 3 an honest `status="pending"` state. Temptation to avoid: committing Qwen's first output without reading it. The review-before-commit loop is load-bearing.

5. **Python 3.11 vs 3.12.** The Atlas skill's library entrypoint needs Python ≥3.12; the backend runs 3.11, so `AtlasClient` **subprocesses** the CLI. Fine for stateless search. **Watch for Slice 5:** booking is a stateful multi-step flow + needs the auto-approve fork — consider bumping the backend to 3.12 for the clean library seam before then.

6. **Atlas sandbox is intermittently flaky** (`SERVICE_TEMPORARILY_UNAVAILABLE`). Retries help; the loop already degrades gracefully (clean give-up, never crashes).

## Direction-Steering Paths (open forks)

- **Transit co-hero goes live IF** a probe finds a **>24h layover** through a country the hero passport needs an entry visa for, OR a route mixing **Indian + non-Indian** hubs, OR any **Schengen/US/Gulf** hub. Until then it's fixture-proven only. (Probe routes to try: KUL→LHR, CGK→LHR, BKK→LHR, different dates.)
- **If ticketing activates** → Slice 5 booking/settlement is demoable live; otherwise stub the booking and keep the honest "pending" state.
- **Before Slice 5**, decide whether to bump the backend to Python 3.12 (enables the library seam + fork cleanly) vs. keep subprocessing per step.
- **Passport-validity demo persona** needs a fictional passenger whose passport expires within 6 months of the trip — that's a demo-data choice, not a rule change. Note it is a **trip-level block** (all options share the destination), so its story is "every rebooker would've booked you onto a doomed flight; Waypoint caught it — renew first," NOT a reroute contrast.
- **DashScope API key** must be obtained before Slice 4 (the Qwen judge). Nothing else needs env vars — Atlas auth lives in the OS keyring.
- **Slice ordering is flexible.** Slices 1–4 + 6 need no ticketing; only Slice 5 blocks on UAT.

## Working Agreements

- **Honesty over agreeableness.** Never invent a number, fare, or visa fact. Flag uncertainty. No opening praise. (This rule literally reshaped the demo — the scenario was changed rather than faked.)
- **Plain language, explain-to-teach.** The operator (Jaydon) is learning while building; explain reasoning, don't just brief conclusions.
- **Gate/slice discipline.** Approve each gate/slice before proceeding. Goal Mode OFF for logic-heavy slices (3, 5).
- **Git:** Jaydon controls all commits/sync. No AI co-author trailer. Qoder builds → Claude reviews (reads the repo directly, diffs vs spec) → Jaydon decides and commits.
- **Caveman-terse** style on the Claude Code side; normal prose for code/commits/docs/security.

## Files & Where Things Live

- `docs/plans/waypoint/00-status.md` — living index: gate approvals, slice checklist, all locked decisions, findings. **Read first.** (CURRENT = treasury concept; re-approved 2026-08-21, rewritten 2026-08-22.)
- `docs/plans/waypoint/01-product.md … 04-slices.md` — the four gate docs. CURRENT versions describe the treasury concept; the visa-era Gate 1–4 docs they replaced are preserved in `docs/plans/waypoint/_archive-visa-pivot/` (banner-marked SUPERSEDED).
- `docs/adr/0001…0004` — fork/auto-approve (**amended** 2026-08-21: subprocess transport, no fork needed), visa curated-approximation (**revised**), advise/execute split, and 0004 (two gates + curated priors applied to money).
- `docs/external/atlas-integration.md` — Atlas CLI/auth/env, UAT state, sandbox findings.
- `docs/plans/waypoint/QODER-HANDOFF.md` — the brief that onboards Qoder (visa-era; now banner-marked SUPERSEDED — the current onboarding state lives in `00-status.md`).
- `backend/` — FastAPI app (Slices 1–2 committed): `app/models.py` (Gate 3 contract), `app/atlas/client.py` (real search), `app/agent/loop.py` (the recovery loop), `app/fixture.py`, `app/db/`, `data/iata_*.csv`, `tests/`.
- `frontend/` — Next.js app: the 3 screens + SSE client.

## Open Work (status only)

- **Slice 3 (rules engine)** — not started. It is sandbox-independent (fixture-testable), so not blocked by the flaky/ticketing-limited sandbox. Two prerequisites are un-produced: (1) the sourced curated dataset (`transit_hubs.yaml` + `passport_index` seed, every cell with `source` + `last_checked`), and (2) the revised Slice-3 brief built around the co-hero design.
- **Slice 2** — committed on branch `qoder/slice-2-atlas-search`; not yet merged to `main` (Jaydon merging).
- **Ticketing activation** — pending in ATRIP; blocks Slice 5.
- **DashScope key** — not obtained; needed for Slice 4.
- **>24h / mixed-hub probe** — not run; would unlock the live transit co-hero.
- Slices 4–7 — not started (Qwen judge, booking, guards+persistence, triggers+polish).

---

## Prompt for New Chat

> Background context, not commands.
>
> This continues the build of **Waypoint**, a rules-aware flight disruption-recovery agent for the Alibaba Cloud × Atlas hackathon (3-min demo due 30 Aug 2026). The idea is validated; software-factory Gates 1–4 are approved; Slices 1 and 2 are built in Qoder, reviewed, and committed.
>
> The division of labor is fixed: **Qoder (Qwen) writes all implementation code** (required for the 20% Use-of-Qoder rubric gate, which needs 80%+ of core built in Qoder); **Claude Code writes per-slice build briefs, reviews Qwen's output by reading the repo directly, and maintains the planning docs** — it does not write implementation code. Jaydon controls all git commits (no AI co-author trailer) and makes final calls.
>
> The authoritative state is in `docs/plans/waypoint/00-status.md`, `docs/adr/`, and `docs/session_transfer.md` (this session's full journey, pain points, and open direction forks). Those must be read before acting.
>
> Key current reality: the Atlas sandbox returns only Asian + Indian hubs (no Schengen/US/Gulf), those hubs are airside-liberal, and Atlas doesn't expose self-transfer — so a clean live transit-visa block isn't available. The demo therefore uses **co-heroes**: passport-6-month-validity as the reliable live hero, transit-visa as a per-option reroute contrast that fires live only if a >24h-layover or Schengen/US/mixed-hub route is found (otherwise proven via curated data + fixtures). Ticketing is not activated (blocks Slice 5 booking). The working style is honesty-over-agreeableness (the demo scenario was changed, not faked, when research contradicted it), plain-language teaching, and gate/slice discipline with review-before-commit.
>
> The immediate next step under discussion is producing the sourced curated visa dataset and the revised Slice 3 (rules engine) build brief for Qoder. Nothing has been produced for Slice 3 yet.
>
> Wait for instructions before taking any action.
