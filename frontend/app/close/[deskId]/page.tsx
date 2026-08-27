"use client";

// Screen 3 — the wrap-up (Slice 8 design refit, step 5).
// GET /api/desk/{id}/close, branching on result.status — NEVER on the HTTP
// code alone: 200 carries DeskResult for every logical outcome
// (closed / escalated / budget_exhausted / failed), 504 = still running
// (retry), 500 = the cycle CRASHED (no result exists — disclose honestly).
//
// REFIT: same tokens/tone as Screens 1–2, plain ops-manager copy. Every
// figure still comes from the close response — nothing invented. Honesty
// elements survive: the mode banner, the fail-closed statuses, the
// losses/steps/breaches counts (demoted into a record zone), and the
// auditor second opinion with its fallback disclosure.

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import gsap from "gsap";
import { useGSAP } from "@gsap/react";

import { getDeskClose, getDeskSnapshot, type CloseOutcome } from "@/lib/api";
import { money } from "@/lib/format";
import type { DeskResult, DeskStatus } from "@/lib/types";

gsap.registerPlugin(useGSAP);

type Phase =
  | { kind: "waiting" }
  | { kind: "outcome"; outcome: CloseOutcome };

const STATUS_COPY: Record<DeskStatus, { call: string; cls: string; sub: string }> = {
  closed: {
    call: "All set",
    cls: "good",
    sub: "Every booking is done."
  },
  escalated: {
    call: "Almost done",
    cls: "warn",
    sub: "One trip still needs your approval before it can be booked."
  },
  budget_exhausted: {
    call: "Budget reached",
    cls: "bad",
    sub: "The remaining trips couldn't be booked within your budget."
  },
  failed: {
    call: "Stopped early",
    cls: "bad",
    sub: "Something went wrong — nothing was booked past that point."
  },
};

export default function ClosePage() {
  const params = useParams<{ deskId: string }>();
  const deskId = params.deskId;

  const [phase, setPhase] = useState<Phase>({ kind: "waiting" });
  const [attempt, setAttempt] = useState(0);
  // "See the full record" panel — same pattern as the run page: pure local
  // UI state, collapsed on every page load, toggling only flips a boolean
  // and `hidden` keeps the content mounted.
  const [recordOpen, setRecordOpen] = useState(false);
  // DeskResult carries no currency; read it from the mandate snapshot
  // (existing endpoint). Stays undefined if the snapshot is unreachable —
  // amounts then render without a symbol rather than a guessed one.
  const [currency, setCurrency] = useState<string | undefined>(undefined);
  // Step 6: the bar's spent-vs-budget ratio. DeskResult carries neither
  // figure, so both come from the SAME snapshot call above (real endpoint
  // numbers, never invented). Stays null if the snapshot is unreachable or
  // the figures don't parse — the bar then simply doesn't render. The
  // `phase` tag records WHICH phase fired the fetch: only figures fetched
  // for the post-outcome snapshot may drive the dry-run ("Would have…")
  // wording — a waiting-phase snapshot could predate the final buys.
  const [figures, setFigures] = useState<{
    spent: number;
    budget: number;
    phase: Phase["kind"];
  } | null>(null);

  const load = useCallback(() => {
    setPhase({ kind: "waiting" });
    getDeskClose(deskId).then((outcome) => {
      setPhase({ kind: "outcome", outcome });
    });
  }, [deskId]);

  useEffect(() => {
    load();
  }, [load, attempt]);

  useEffect(() => {
    let cancelled = false;
    const fetchedIn = phase.kind; // which phase fired THIS snapshot fetch
    getDeskSnapshot(deskId)
      .then((snap) => {
        if (cancelled) return;
        setCurrency(snap.mandate.currency);
        // Strict summation (mirrors the desk page's extractBudgetFigures):
        // any unparseable figure makes the WHOLE sum non-finite and the
        // figures stay null — never coerce garbage to 0, which could wrongly
        // flip the page into the dry-run "Would have…" wording.
        const budget = Number(snap.mandate.budget_total);
        const spent = snap.budgets.reduce(
          (sum, b) => sum + Number(b.spent),
          0
        );
        if (Number.isFinite(budget) && budget > 0 && Number.isFinite(spent)) {
          setFigures({ spent, budget, phase: fetchedIn });
        }
      })
      .catch(() => {
        /* snapshot is cosmetic here — the close result stands without it */
      });
    return () => {
      cancelled = true;
    };
    // Fetches once on mount, then again whenever phase.kind changes — in
    // particular when the outcome RESOLVES: a user who lands here mid-cycle
    // would otherwise bar against a stale pre-finish snapshot and understate
    // final spend. The normal flow (mount → one outcome) stays two fetches.
  }, [deskId, phase.kind]);

  // ---------- step 6: the signature moment (gsap) --------------------------
  // Display-only. The hero figure counts up from 0 to the REAL close-response
  // pnl (money()-formatted every frame, onComplete lands on the exact real
  // figure); the bar fills via scaleX to the REAL spent-vs-budget ratio from
  // the snapshot — no ratio, no fill, never an invented percentage.
  const scopeRef = useRef<HTMLElement>(null);
  const heroRef = useRef<HTMLDivElement>(null);
  const fillRef = useRef<HTMLDivElement>(null);
  const countedRef = useRef(false);
  const filledRef = useRef(false);
  // The once-guards key off the OUTCOME OBJECT itself: they reset only when
  // a genuinely new outcome arrives — NEVER on dep-only re-runs (the
  // snapshot resolving AFTER the close result must not replay the count-up
  // or the bar from zero).
  const seenOutcomeRef = useRef<CloseOutcome | null>(null);

  useGSAP(
    () => {
      if (phase.kind !== "outcome" || phase.outcome.kind !== "result") {
        // waiting / retrying — a fresh outcome may animate again.
        seenOutcomeRef.current = null;
        countedRef.current = false;
        filledRef.current = false;
        return;
      }
      if (seenOutcomeRef.current !== phase.outcome) {
        seenOutcomeRef.current = phase.outcome;
        countedRef.current = false;
        filledRef.current = false;
      }
      const result: DeskResult = phase.outcome.result;
      const pnlNum = Number(result.pnl);
      if (!Number.isFinite(pnlNum)) return;
      // Dry-run distinction (bug 5b): when the snapshot's REAL spend across
      // every budget is zero, nothing was actually bought — the figure is
      // what the decisions WOULD have saved. Only figures fetched for the
      // POST-OUTCOME snapshot qualify — a waiting-phase snapshot could
      // predate the final buys and flash the wrong wording. No figures (or
      // waiting-phase figures) falls back to the plain wording, never a
      // guess. Identical to the JSX condition below.
      const nothingSpent =
        figures !== null && figures.phase === "outcome" && figures.spent === 0;
      const label = nothingSpent
        ? pnlNum >= 0
          ? "Would have saved "
          : "Would have been over by "
        : pnlNum >= 0
          ? "Saved "
          : "Over by ";
      const magnitude = Math.abs(pnlNum);
      // Identical to the JSX render — the tween only ever counts TOWARD it.
      const finalText = `${label}${money(result.pnl, currency).replace("−", "")}`;
      const ratio = figures
        ? Math.min(1, Math.max(0, figures.spent / figures.budget))
        : null;

      gsap.matchMedia().add(
        { reduceMotion: "(prefers-reduced-motion: reduce)" },
        (ctx) => {
          const reduce = Boolean(ctx.conditions?.reduceMotion);

          // Hero count-up (once per outcome).
          const heroEl = heroRef.current;
          if (heroEl) {
            if (countedRef.current) {
              heroEl.textContent = finalText; // settle after any re-run
            } else {
              countedRef.current = true;
              if (reduce) {
                heroEl.textContent = finalText; // real figure, no tween
              } else {
                const counter = { v: 0 };
                gsap.to(counter, {
                  v: magnitude,
                  duration: 0.9,
                  ease: "power2.out",
                  onUpdate: () => {
                    const shown = Math.min(Math.round(counter.v), magnitude);
                    heroEl.textContent = `${label}${money(String(shown), currency)}`;
                  },
                  onComplete: () => {
                    heroEl.textContent = finalText; // always the real figure
                  },
                });
              }
            }
          }

          // Bar fill — scaleX only (transformOrigin left center), once,
          // and only because a real ratio exists. The settled state is
          // ALWAYS the real ratio, forced via gsap.set: at ratio 0 a
          // fromTo(0 → 0) would have identical ends and could leave the
          // element unstyled, so a zero ratio is SET (collapsed), never
          // tweened — the fill can never overstate spend.
          const fillEl = fillRef.current;
          if (fillEl && ratio !== null) {
            if (filledRef.current) {
              gsap.set(fillEl, { scaleX: ratio, transformOrigin: "left center" });
            } else {
              filledRef.current = true;
              if (reduce || ratio === 0) {
                gsap.set(fillEl, { scaleX: ratio, transformOrigin: "left center" });
              } else {
                gsap.fromTo(
                  fillEl,
                  { scaleX: 0, transformOrigin: "left center" },
                  {
                    scaleX: ratio,
                    transformOrigin: "left center",
                    duration: 0.7,
                    ease: "power2.out",
                    // belt-and-braces: land EXACTLY on the real ratio
                    onComplete: () => {
                      gsap.set(fillEl, {
                        scaleX: ratio,
                        transformOrigin: "left center",
                      });
                    },
                  }
                );
              }
            }
          }
        }
      );
      // NO cleanup here: resetting the once-guards on dep-only re-runs
      // (snapshot or currency arriving after the result) would replay the
      // count-up and the bar from zero. Guards reset only above, when the
      // outcome itself changes; unmount cleanup stays automatic via useGSAP.
    },
    { scope: scopeRef, dependencies: [phase, figures, currency] }
  );

  // Shared header — same shape as Screens 1–2.
  const header = (
    <div className="top">
      <div className="brand">
        <span className="beacon" />
        Waypoint
      </div>
      <div className="sub">
        The wrap-up<span className="run-id"> · {deskId}</span>
      </div>
    </div>
  );

  // ---------- waiting -----------------------------------------------------
  if (phase.kind === "waiting") {
    return (
      <main className="wrap">
        {header}
        <div className="run close-status-card">
          <p className="close-call">Wrapping up…</p>
          <p className="close-sub">
            We're waiting for the run to finish before showing the summary —
            up to 60 seconds a try.
          </p>
        </div>
      </main>
    );
  }

  const { outcome } = phase;

  // ---------- 504: still running (retry) -----------------------------------
  if (outcome.kind === "still_running") {
    return (
      <main className="wrap">
        {header}
        <div className="run close-status-card">
          <p className="close-call warn">Still working</p>
          <p className="close-sub">
            The booking run is still going — give it a moment.
          </p>
          <div className="close-actions">
            <button className="btn primary" onClick={() => setAttempt((a) => a + 1)}>
              Wait a little longer →
            </button>
            <Link className="btn ghost" href={`/desk/${deskId}`}>
              Back to the run
            </Link>
          </div>
        </div>
      </main>
    );
  }

  // ---------- 500: crashed --------------------------------------------------
  if (outcome.kind === "crashed") {
    return (
      <main className="wrap">
        {header}
        <div className="run close-status-card">
          <p className="close-call bad">Something went wrong</p>
          <p className="close-sub">
            The run couldn't finish. No final numbers to show.
          </p>
          <div className="close-actions">
            <Link className="btn ghost" href={`/desk/${deskId}`}>
              Back to the run
            </Link>
          </div>
        </div>
      </main>
    );
  }

  // ---------- 404: desk not found (no retry — it won't appear) ---------------
  if (outcome.kind === "not_found") {
    return (
      <main className="wrap">
        {header}
        <div className="run close-status-card">
          <p className="close-call bad">Booking not found</p>
          <p className="close-sub">
            We couldn't find this booking.
          </p>
          <div className="close-actions">
            <Link className="btn ghost" href="/">
              Start a new booking →
            </Link>
          </div>
        </div>
      </main>
    );
  }

  // ---------- unreachable ----------------------------------------------------
  if (outcome.kind === "unreachable") {
    return (
      <main className="wrap">
        {header}
        <div className="run close-status-card">
          <p className="close-call bad">Couldn't load the summary</p>
          <p className="close-sub">{outcome.detail}</p>
          <div className="close-actions">
            <button className="btn primary" onClick={() => setAttempt((a) => a + 1)}>
              Try again
            </button>
          </div>
        </div>
      </main>
    );
  }

  // ---------- 200: branch on result.status -----------------------------------
  const result: DeskResult = outcome.result;
  const report = outcome.report;
  const copy = STATUS_COPY[result.status] ?? STATUS_COPY.failed;
  const pnl = Number(result.pnl);
  const pnlCls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
  // Bug 5b: same REAL condition as the count-up tween — snapshot spend
  // across every budget is 0, so nothing was actually bought, AND the
  // figures came from the post-outcome snapshot (waiting-phase figures
  // could predate the final buys — keep the plain wording, never guess).
  // The figure itself is unchanged (result.pnl); only the words around it
  // change.
  const nothingSpent =
    figures !== null && figures.phase === "outcome" && figures.spent === 0;
  // Hero figure: "Saved X" / "Would have saved X" / "Over by X" — the bound
  // pnl, magnitude-only sign handling on the negative side (same
  // display-only trick as Screen 2).
  const heroLabel = nothingSpent
    ? pnl >= 0
      ? "Would have saved "
      : "Would have been over by "
    : pnl >= 0
      ? "Saved "
      : "Over by ";
  const hero = `${heroLabel}${money(result.pnl, currency).replace("−", "")}`;

  return (
    <main className="wrap" ref={scopeRef}>
      {header}

      <div className="run close-status-card">
        <p className={`close-call ${copy.cls}`}>{copy.call}</p>
        <p className="close-sub">{copy.sub}</p>

        {/* the one number that matters — bound to result.pnl, never invented */}
        <div className="budget">
          <div className="left">On your team's bookings</div>
          <div ref={heroRef} className={`big num ${pnlCls}`}>
            {hero}
          </div>
          {/* bug 5b: when real spend across every budget is zero (per the
              post-outcome snapshot), say what the figure really is —
              nothing was bought. Pnl-aware copy: saved vs. cost extra.
              The figure above is unchanged. */}
          {nothingSpent && (
            <div className="hero-note">
              Nothing was bought in this run — this is what the decisions
              would have {pnl >= 0 ? "saved" : "cost extra"}.
            </div>
          )}
        </div>

        {/* step 6: spent-vs-budget, scaleX fill — rendered ONLY when both
            real figures exist (snapshot); no ratio, no bar, never a guess.
            The note states spent / budget / left — deterministic arithmetic
            on the two real endpoint values, nothing invented. */}
        {figures && (
          <>
            <div className="bar">
              <div ref={fillRef} className="fill" />
            </div>
            <div className="bar-note num">
              spent {money(String(figures.spent), currency)} of{" "}
              {money(String(figures.budget), currency)} ·{" "}
              {money(String(figures.budget - figures.spent), currency)} left
            </div>
          </>
        )}

        {/* demoted record zone — the counts live here, small and secondary */}
        <div className="record close-record">
          <div className="close-stats">
            <div className="close-stat">
              <div className="cs-k">Price drops</div>
              <div className="cs-v num">{result.losses_admitted}</div>
            </div>
            <div className="close-stat">
              <div className="cs-k">Fare checks</div>
              <div className="cs-v num">{result.step_count}</div>
            </div>
            {typeof report?.policy_breaches === "number" && report.policy_breaches > 0 ? (
              <div className="close-stat">
                <div className="cs-k">Over your limit</div>
                <div className="cs-v num">{report.policy_breaches}</div>
              </div>
            ) : null}
          </div>
        </div>

        {/* Mode banner — unconditional: plain words for WHICH mode ran. */}
        {result.comparison_mode ? (
          <div className="close-note">
            Dry run — no real bookings were made.
          </div>
        ) : (
          <div className="close-note live">
            These bookings are confirmed.
          </div>
        )}

        {/* S7 risk-officer verdict slot (bug 5a): the plain-language label
            stays on the happy path; the VERBATIM auditor line and its
            fallback disclosure moved into the collapsed "full record" panel
            below — raw reviewer jargon belongs in the record, not here.
            Nothing renders if the line is absent. */}
        {typeof report?.auditor_line === "string" && report.auditor_line ? (
          <div className="close-room filled">
            <div className="auditor-k">
              Second opinion on this run
            </div>
            {/* Task #8: plain-English line when the backend supplied one;
                falls back to the verbatim auditor line if absent. The full
                record below keeps the verbatim line either way. */}
            <p className="auditor-line">{report.auditor_plain || report.auditor_line}</p>
            {report.auditor_source === "deterministic-fallback" ? (
              <div className="auditor-src">Automated review — no manual reviewer ran.</div>
            ) : null}
          </div>
        ) : (
          <div className="close-room" aria-hidden="true" />
        )}

        {/* Bug 5a: the full record — collapsed by default, mirroring the run
            page's step-3 panel exactly: same toggle copy, same aria-expanded
            / aria-controls / hidden pattern (a native <button> carries the
            keyboard semantics; no gsap reveal — the run page toggles via
            `hidden` too). The auditor line renders VERBATIM inside — never
            rewritten — with its fallback disclosure beside it. The panel is
            omitted entirely when there is no auditor line to hold. */}
        {typeof report?.auditor_line === "string" && report.auditor_line ? (
          <div className="record">
            <button
              type="button"
              className="record-toggle"
              aria-expanded={recordOpen}
              aria-controls="close-full-record"
              onClick={() => setRecordOpen((o) => !o)}
            >
              {recordOpen ? "Hide the full record ↑" : "See the full record →"}
            </button>
            <div id="close-full-record" className="fineprint" hidden={!recordOpen}>
              <div className="sec fineprint-k">The full record</div>
              <div className="close-room filled">
                <div className="auditor-k">Reviewer note</div>
                <p className="auditor-line">{report.auditor_line}</p>
                {report.auditor_source === "deterministic-fallback" ? (
                  <div className="auditor-src">
                    Automated review — no manual reviewer ran.
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="close-actions">
        <Link className="btn ghost" href="/">
          Start another booking →
        </Link>
      </div>
    </main>
  );
}
