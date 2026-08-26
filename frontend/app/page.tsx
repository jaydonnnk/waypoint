"use client";

// Screen 1 — start a booking run (Slice 8 design refit, step 4).
//
// TRIMMED per 07-design-refit.md: one line, one human sub-line, one button,
// three tiny reassurances. The old four-point builder essay is gone; its
// honesty points survive as the reassurance chips (plain words) and the
// demoted footnote. The mandate itself is still seeded SERVER-SIDE — this
// card is display copy only, and the start action (seedDesk + navigate) is
// unchanged from Slices 1–7. Nothing here fabricates API numbers.

import { useRouter } from "next/navigation";
import { useState, useRef } from "react";

import gsap from "gsap";
import { useGSAP } from "@gsap/react";

import { seedDesk } from "@/lib/api";

gsap.registerPlugin(useGSAP);

export default function MandatePage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- ops-manager budget constraints (set BEFORE "Start booking") ----
  const [budgetTotal, setBudgetTotal] = useState(12000);
  const [authorityCap, setAuthorityCap] = useState(1500);
  const [contingencyPct, setContingencyPct] = useState(5);

  // ---- Screen 1's one signature moment (step 6): a gentle staggered
  // entrance for the start-card contents — transform/opacity only, ~0.76s
  // total (0.4s tween + 6 × 0.06s stagger over the 7 animated elements:
  // title, sub, chips, the 3 constraint fields, button), once on mount.
  // gsap runs only inside useGSAP (client, post-mount), selectors stay
  // inside this page's <main> via scope, and reduced motion skips the
  // tween entirely.
  const scopeRef = useRef<HTMLElement>(null);
  useGSAP(() => {
    gsap.matchMedia().add(
      { reduceMotion: "(prefers-reduced-motion: reduce)" },
      (ctx) => {
        if (ctx.conditions?.reduceMotion) return; // content just renders
        gsap.from(
          ".start-title, .start-sub, .assure, .constraint-field, .start-btn",
          {
            y: 10,
            autoAlpha: 0,
            duration: 0.4,
            ease: "power2.out",
            stagger: 0.06,
          }
        );
      }
    );
  }, { scope: scopeRef });

  // ---- constraint validity — NaN means the field was cleared (typing);
  // ranges mirror the inputs' min/max and the backend's SeedRequest bounds.
  const constraintsValid =
    Number.isFinite(budgetTotal) &&
    budgetTotal >= 1000 &&
    Number.isFinite(authorityCap) &&
    authorityCap >= 100 &&
    Number.isFinite(contingencyPct) &&
    contingencyPct >= 0 &&
    contingencyPct <= 25;

  async function openDesk() {
    // Belt-and-braces guard — the start button is disabled while invalid.
    if (!constraintsValid) return;
    setBusy(true);
    setError(null);
    try {
      // Contingency convention: the form takes a PERCENT (e.g. 5); the
      // wire format is the FRACTION the backend expects (pct / 100),
      // matching Mandate.contingency_pct / SeedRequest (0.05 default).
      const deskId = await seedDesk({
        budget_total: budgetTotal,
        authority_cap: authorityCap,
        contingency_pct: contingencyPct / 100,
      });
      router.push(`/desk/${deskId}`);
    } catch (err) {
      setBusy(false);
      setError(
        err instanceof Error
          ? `${err.message} — the booking service may not be running`
          : "Could not reach the booking service"
      );
    }
  }

  return (
    <main className="wrap" ref={scopeRef}>
      {/* ---- header — same shape as the run screen -------------------- */}
      <div className="top">
        <div className="brand">
          <span className="beacon" />
          Waypoint
        </div>
      </div>

      {/* ---- the start card: one line, one button --------------------- */}
      <div className="start">
        <h1 className="start-title">
          Book your team's flights, on budget.
        </h1>
        <p className="start-sub">
          Waypoint books your team's trips, keeps an eye on the fares, and
          asks you first whenever a call is too big to make on its own.
        </p>

        {/* three tiny reassurances — the honesty points, in plain words */}
        <div className="assure">
          <span className="assure-chip">
            <span className="pin g" />
            Stays under budget
          </span>
          <span className="assure-chip">
            <span className="pin w" />
            Asks before overspending
          </span>
          <span className="assure-chip">
            <span className="pin g" />
            Never invents a fare
          </span>
        </div>

        {/* budget constraints — the ops manager's numbers, set up front */}
        <style>{`
          .constraints { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }
          .constraint-field {
            flex: 1; min-width: 140px; display: flex; flex-direction: column; gap: 6px;
            background: var(--paper); border: 1px solid var(--line);
            border-radius: var(--r-bar); padding: 10px 12px;
          }
          .constraint-k {
            font-family: var(--mono); font-size: 10px; letter-spacing: 0.8px;
            text-transform: uppercase; color: var(--mut);
          }
          .constraint-field input {
            width: 100%; border: 1px solid var(--line2); border-radius: 8px;
            background: var(--card); color: var(--ink);
            font: 600 15px var(--mono); padding: 8px 10px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
          }
          .constraint-field input:focus {
            outline: none; border-color: var(--brand);
            box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
          }
        `}</style>
        <div className="constraints">
          <label className="constraint-field">
            <span className="constraint-k">Total budget ($)</span>
            <input
              type="number"
              value={Number.isNaN(budgetTotal) ? "" : budgetTotal}
              min={1000}
              step={100}
              onChange={(e) => setBudgetTotal(e.target.valueAsNumber)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">Per-booking cap ($)</span>
            <input
              type="number"
              value={Number.isNaN(authorityCap) ? "" : authorityCap}
              min={100}
              step={50}
              onChange={(e) => setAuthorityCap(e.target.valueAsNumber)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">Contingency (%)</span>
            <input
              type="number"
              value={Number.isNaN(contingencyPct) ? "" : contingencyPct}
              min={0}
              max={25}
              step={1}
              onChange={(e) => setContingencyPct(e.target.valueAsNumber)}
            />
          </label>
        </div>

        <button
          className="btn primary start-btn"
          onClick={openDesk}
          disabled={busy || !constraintsValid}
          title={constraintsValid ? undefined : "Fill in all three budget constraints"}
        >
          {busy ? "Starting…" : "Start booking →"}
        </button>
        {error && <div className="status err">{error}</div>}

        {/* demoted footnote — how starting works, visibly secondary */}
        <div className="note-soft">
          Starting sets up your budget and booking limits, then runs it all
          live — you'll watch every check and booking as it happens.
        </div>
      </div>
    </main>
  );
}
