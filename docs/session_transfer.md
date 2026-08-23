# Waypoint — Session Transfer

Background handoff for a fresh session. The authoritative, always-current spec lives in `docs/plans/waypoint/00-status.md`, `01-product.md`–`04-slices.md`, the per-slice handoff files `S5-HANDOFF.md`/`S6-HANDOFF.md`/`S7-HANDOFF.md`, the ADRs in `docs/adr/`, and `docs/external/atlas-integration.md`. **Read those first.** This document adds only what those don't capture: what recent sessions actually did, why, the traps hit, and what's still unresolved.

Project: **Waypoint** — a corporate travel treasury agent (mandate → book/hold judgment → P&L → weekly close) for the Alibaba Cloud × Atlas Agentic Hackathon (submission **30 Aug 2026**, deliverable = 3-min demo). Supersedes the archived visa/passport-recovery concept in `docs/plans/waypoint/_archive-visa-pivot/`. No project-level `CLAUDE.md` exists in this repo.

Build workflow (unchanged, load-bearing for the rubric): **Qoder (Qwen) builds all implementation code; Claude Code writes grounded per-slice handoff prompts, cross-checks Qoder's output against source, and makes only surgical/blocker fixes when explicitly authorized.** The ≥80%-Qoder-built rule gates 20% of the score.

---

## Completed

- **S4 merged to `main`** (commit `b170252`, fast-forward). Amended to fold `session_transfer.md` in and drop the co-author trailer. `WAYPOINT_LIVE_BOOKING` arm-switch (armed only on exact `"1"`, default OFF) + ledger-only seat-alloc (Branch B) confirmed against source.
- **S5 — treasury frontend refit — built by Qoder, cross-checked, committed `7797d9d`, merged to `main` (ff).** The entire visa-concept frontend was replaced: `types.ts`/`api.ts` rewritten to the real desk contract, three screens (mandate seed → live desk/blotter → weekly close), replay-safe wipe-and-rebuild SSE, meter set-only, gated escalation, live-from-real-events cold open. `next build` clean, zero visa survivors. Every claim in Qoder's report was verified against source (suite, `DeskStatus="closed"` not `"done"`, live-mode gate string, etc.).
- **S6 — hardening — built by Qoder, cross-checked, committed `cbe306b`, pushed to `origin/qoder/slice-6-hardening` (NOT yet merged to main).** `budget_exhausted` now genuinely emitted; all give-up paths route through one `_give_up` helper that flushes the settle ledger and carries real P&L/losses (the "books tie out" fix, GAP 2b); meter hard-stop at 20 tested. Suite 83 passed. Verified-and-kept (not rebuilt): code-only error routing, single-retry-site discipline (`client.py` byte-identical), meter hard-stop.
- **`00-status.md` slice checklist corrected** — S1–S6 now ticked `[x]` (previously all stale `[ ]`); S2 ticked but flagged honestly as the DAY-4-GATE comparison-mode fallback, not the live proof; the 82→83 test-count typo fixed.
- **Three per-slice handoff docs authored and grounded in real `file:line`** (not the slice doc's wishlist): `S5-HANDOFF.md`, `S6-HANDOFF.md`, `S7-HANDOFF.md`. Each folds in the decisions below.
- **Re-confirmed the Atlas outage fresh (2026-08-23):** `search` returns terminal `INTERNAL_ERROR` (Atlas maps to status 9999) on multiple routes while `auth status`/`doctor` read fully healthy. Diffed the prior session's probe artifacts, deleted them from the tree.

## Decisions

- **S5 cold-open = live from real events** (over a static "BOOKED" replay card) — the demo P&L/marks are genuinely mark-to-market and real even in comparison mode; only the literal word "BOOKED" is dishonest, so the toast reads "book decision logged" and the counter animates off real events.
- **S5 escalation = gated + annotated** (over live-but-always-410 buttons) — in comparison mode the backend auto-resolves to "B" and registers no decision slot, so any decision POST is *guaranteed* to 410. Real buttons would be theater; instead the two priced options + recommendation render annotated "auto-resolved to B — comparison mode; in live mode this is your one click." The real POST path is built but gated behind live mode.
- **S6 `budget_exhausted` = option (b): `budget_left <` cheapest still-held position's mark** (strict `<`, post-settle, last-known marks, zero extra Atlas calls). Distinct from the live per-booking guard (which uses the real verified price right before a write) — both kept; they answer different questions.
- **S6 meter-exhaustion = keep the disclosed degrade** (over a give-up) — degrading to a stale mark with a disclosed reason is more honest than aborting; it is NOT a `DeskStatus` value and never becomes one.
- **S6 GAP 2b = flush settle on every give-up path** — a researcher found give-ups returned before the settle flush, silently dropping ledger rows; combined with real `losses_admitted` this made result-vs-DB disagree. The escalated give-up path already had this bug live. Chosen over "document the quirk" because the product's entire thesis is reconciliation — a result claiming losses the DB lacks is disqualifying.
- **S7 auditor placement = at the routes/`/close` layer, NOT on `DeskAgent`** — the auditor runs once at close reading the settled blotter; wiring it into `DeskAgent.__init__` (as the 03 spec's DI list implies) would be dead plumbing since nothing in the live loop calls it.
- **S7 human-waiver marker = option A: disclose the gap, do not complete it this round** — `count_policy_breaches` excludes over-cap trades carrying a "human waiver" marker, but the write path never emits that marker, so the branch is dead. In comparison mode nothing books over cap, so the breach count is structurally 0 regardless. Completing it (writing the marker) was rejected because it can't be end-to-end tested without live booking AND because branching a breach count on a free-text-note substring is the exact "branch on message, never code" anti-pattern banned everywhere in this repo. The proper fix (a *structured* waiver field on the ledger row) is recorded in `S7-HANDOFF.md` for the future live-booking slice.

## Traps

- **Never trust a single `ticketing_available` read.** It has flapped `true`/`false` across same-day probes by different sessions. Always re-probe immediately before any decision depending on it.
- **The Atlas blocker is now the `search` step itself, not just the ticketing flag.** As of 2026-08-23, `search` returns terminal `INTERNAL_ERROR`/9999 on multiple routes while auth/doctor read healthy — a *worse and different* failure than the flapping flag. This is an Atlas-side outage, re-confirmed fresh, not our code.
- **"S6 done" / "S4 done" does NOT mean S2's Day-4 gate is proven.** The S2 acceptance test (`verify → order create → pay → status == TICKETED` on one live route) has **never once succeeded** in this repo's history. Downstream slices all run identically on the disclosed comparison-mode fallback; nothing is blocked, but the live write path remains unproven.
- **`DEMO_PAX_JSON` in `loop.py` is still the original hand-guessed passenger shape**, never validated against a real Atlas envelope. Expect `BOOKING_INPUT_INVALID` if a live order create is ever attempted.
- **Do NOT rubber-stamp Qoder's self-reports.** Every session this pattern held: Qoder's cross-check claims were verified against source before endorsing, and each time it surfaced at least one real correction (my own handoff's `"done"`-vs-`"closed"` error; the escalated-give-up settle-drop being live, not introduced; the waiver-marker branch being dead). Verify load-bearing claims with a Read/Grep, don't trust the report.
- **S6/S7 are "verify-and-fill", not build-from-scratch.** Most of what `04-slices.md` lists under them was already built in S3/S4. Rebuilding regresses tested behavior. Ground every handoff in what actually exists at `file:line`.

## Working Agreements

- **The Qoder loop:** Claude writes a grounded handoff → Qoder proposes a plan, surfaces decisions → the user (or Claude, with reasoning) answers → Claude folds the answer into the handoff → Qoder builds → Claude cross-checks against source. Genuine decisions are surfaced via structured questions with a stated recommendation, never pre-decided silently.
- **The user wants a copy-paste reply block for Qoder** after most exchanges — plain-text (no rich markdown), self-contained, ready to paste into Qoder.
- **User controls all `git push` personally**, but this session explicitly authorized Claude to commit + publish the S6 branch. Commits carry no AI co-author trailer.
- **Merge style:** S4 and S5 were fast-forward-merged to `main` locally; S6 was committed + pushed as a branch (not merged). The user decides per-slice whether to merge.
- **Plain language, honesty over agreeableness, no opening praise** — the user is learning while building and wants reasoning, not just conclusions.

## Files Changed

Claude-authored this session (docs only — all code is Qoder's):
- `docs/plans/waypoint/00-status.md` — S1–S6 checklist ticked; S2 flagged as fallback-not-proof; 82→83 fix.
- `docs/plans/waypoint/S5-HANDOFF.md`, `S6-HANDOFF.md`, `S7-HANDOFF.md` — created; each carries its slice's decisions inline.

Qoder-built, committed: S5 (`7797d9d`, frontend refit), S6 (`cbe306b`, hardening — see `git show` for exact ranges).

Uncommitted working tree (branch `qoder/slice-7-risk-agent`) = Qoder's S7 build in progress: new `backend/app/agent/auditor.py` + `backend/tests/test_auditor.py`; `routes.py` (`CloseReport`, `count_policy_breaches`, `/close` wiring); `models.py` (`CloseReport`); `fixture.py`; frontend close-screen render of breach count + auditor line. One unrelated uncommitted edit to `backend/app/agent/brain.py` exists that is **outside this handoff's scope** (a separate DashScope endpoint episode).

## Open Work

- **S7 — in progress on branch `qoder/slice-7-risk-agent`, uncommitted.** Auditor, `CloseReport`, and the code-owned breach count are built; the waiver-marker decision is made (option A). Remaining status: frontend close-screen rendering of the auditor line + breach count, final cross-check, and the injection one-flag + cold-open pre-warm items. Not yet committed or reviewed to completion. The auditor's real-LLM path depends on a working `DASHSCOPE_API_KEY` in the backend's launch shell (no dotenv loading exists); absent that it silently degrades to a deterministic fallback line.
- **S6 branch not merged to `main`** — committed and pushed, awaiting a merge decision.
- **S8 — not started** — rehearsal + 3-min video recording; Jaydon's, no code.
- **The live write path (S2 Day-4 gate) remains unproven** and blocks nothing downstream; it is gated on the Atlas-side `search`/ticketing outage clearing, which must be re-probed fresh.

---

## Prompt for New Chat

This continues the build of **Waypoint**, a corporate travel treasury agent for the Alibaba Cloud × Atlas hackathon (3-min demo due 30 Aug 2026). The concept is locked; Gates 1–4 re-approved. The authoritative state is in `docs/plans/waypoint/00-status.md`, `01`–`04-slices.md`, the per-slice `S5/S6/S7-HANDOFF.md` files, the ADRs, and `docs/external/atlas-integration.md` — read those plus this document's sections above before acting.

Division of labor: **Qoder (Qwen) builds all implementation code** (required for the ≥80%-Qoder rubric gate); **Claude Code writes grounded per-slice handoff prompts, cross-checks Qoder's built code against source and the rubric, and fixes only surgical/blocker issues when explicitly authorized.** No AI co-author trailer on any commit. The user (Jaydon) controls all `git push` and all scope/posture decisions; Claude proposes, explains trade-offs plainly, and waits for a decision on anything beyond a surgical fix. After exchanges, the user typically wants a plain-text copy-paste reply block to hand back to Qoder.

Slices 1–6 are built and cross-checked. S1–S5 are merged to `main` (through commit `7797d9d`). S6 is committed (`cbe306b`) and pushed as branch `qoder/slice-6-hardening` but not yet merged. S7 (risk officer + demo choreography) is in progress on branch `qoder/slice-7-risk-agent` with an uncommitted working tree — the auditor, `CloseReport`, and code-owned policy-breach count are built, the human-waiver-marker gap was decided (disclose-and-defer, option A), and the remaining S7 status is the frontend close-screen render, injection one-flag, cold-open pre-warm, and final cross-check. S8 (rehearsal + video) has not started and is the user's own work, no code.

The single most important standing fact: **Waypoint has never once completed a real end-to-end sandbox booking** (`verify → order create → pay → status == TICKETED`) — S2's own acceptance test, blocked every attempt. The current Atlas blocker is a platform-side `search`-step `INTERNAL_ERROR`/9999 outage (re-confirmed 2026-08-23) while auth/doctor read healthy; it must be re-probed fresh, never assumed. Everything downstream runs identically on the disclosed comparison-mode fallback, so nothing is blocked, but the live write path and the hand-guessed `DEMO_PAX_JSON` passenger shape remain unproven.

Wait for instructions before taking any action.
