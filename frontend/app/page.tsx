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

  // ---- optional trip context (task #2) — display-only, never gates the
  // start button; blank defaults keep today's behavior identical.
  const [teamSize, setTeamSize] = useState(1);
  const [destination, setDestination] = useState("");
  const [tripPurpose, setTripPurpose] = useState("");

  // ---- Screen 1's one signature moment (step 6): a gentle staggered
  // entrance for the start-card contents — transform/opacity only, ~0.94s
  // total (0.4s tween + 9 × 0.06s stagger over the 10 animated elements:
  // title, sub, chips, the 6 constraint fields, button), once on mount.
  // gsap runs only inside useGSAP (client, post-mount), selectors stay
  // inside this page's <main> via scope, and reduced motion skips the
  // tween entirely.
  const scopeRef = useRef<HTMLElement>(null);
  useGSAP(() => {
    // Runs ONLY when the user has no reduced-motion preference — so the whole
    // ambient scene is skipped for reduce-motion users, and matchMedia reverts
    // it cleanly on unmount.
    gsap.matchMedia().add(
      "(prefers-reduced-motion: no-preference)",
      () => {
        // (1) one-shot staggered entrance for the form + hero copy.
        gsap.from(
          ".hero-brand, .hero-title, .hero-lead, .assure, " +
            ".start-title, .start-sub, .constraint-field, .start-btn",
          {
            y: 14,
            autoAlpha: 0,
            duration: 0.5,
            ease: "power3.out",
            stagger: 0.05,
          }
        );

        // (2) the "waypoint field" — a continuous, cinematic ambient drift on
        // the left hero (transform/opacity only, GPU-cheap, loops forever).
        // Concentric beacon rings breathe outward; floating pins drift on
        // their own phase; a soft sheen sweeps across. Higgsfield-style
        // living background, no video/asset weight.
        gsap.to(".wp-ring", {
          scale: 1.35,
          autoAlpha: 0,
          duration: 4.2,
          ease: "sine.out",
          stagger: { each: 1.4, repeat: -1 },
        });
        gsap.utils.toArray<HTMLElement>(".wp-pin").forEach((pin, i) => {
          gsap.to(pin, {
            y: `+=${16 + i * 6}`,
            x: `+=${i % 2 ? -10 : 12}`,
            duration: 3.5 + i * 0.6,
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
        });
        gsap.fromTo(
          ".wp-sheen",
          { xPercent: -120 },
          {
            xPercent: 120,
            duration: 9,
            ease: "sine.inOut",
            repeat: -1,
            yoyo: true,
          }
        );

        // (3) aurora blobs — big, soft, slow parallax drift for depth.
        gsap.utils.toArray<HTMLElement>(".wp-aurora").forEach((blob, i) => {
          gsap.to(blob, {
            xPercent: i % 2 ? -18 : 22,
            yPercent: i % 2 ? 16 : -14,
            scale: 1.15,
            duration: 14 + i * 4,
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
        });

        // (4) the flights — a bright dash glides down each normalized arc
        // (dasharray = short glint + full-length gap; offset sweeps it end to
        // end), staggered and with a breather between passes, like routes
        // being plotted live.
        gsap.utils.toArray<SVGPathElement>(".glint").forEach((glint, i) => {
          gsap.fromTo(
            glint,
            { strokeDashoffset: 1030 },
            {
              strokeDashoffset: 30,
              duration: 6.5,
              ease: "power1.inOut",
              repeat: -1,
              repeatDelay: 1.6,
              delay: i * 2.4,
            }
          );
        });
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
        // team_size is deliberately NOT in constraintsValid — blank/NaN or
        // an out-of-range typed value normalizes back to the default 1 here,
        // and anything else clamps to the 1–50 Mandate bounds (never send NaN).
        team_size: Number.isInteger(teamSize) ? Math.min(Math.max(teamSize, 1), 50) : 1,
        destination_label: destination.trim(),
        trip_purpose: tripPurpose.trim(),
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
    <main className="mandate-screen" ref={scopeRef}>
      {/* the living waypoint field spans the WHOLE screen (behind both panes)
          so the teal atmosphere blends across into the form side — drifting
          rings, floating pins, a sheen sweep. aria-hidden: pure ambience,
          all motion added by gsap on mount. */}
      <div className="wp-field" aria-hidden="true">
        {/* slow-drifting light — gives the teal depth and life */}
        <span className="wp-aurora a1" />
        <span className="wp-aurora a2" />
        <span className="wp-aurora a3" />

        {/* the routes: faint great-circle arcs with a glint travelling each,
            like flights being plotted across a map. Arcs drawn in a sliced
            viewBox so they span the whole screen; the travelling dots follow
            the same paths via CSS offset-path (see globals.css). */}
        <svg className="wp-routes" viewBox="0 0 1280 760"
             preserveAspectRatio="xMidYMid slice">
          {/* faint base arcs */}
          <path className="route" d="M -120 640 Q 420 240 1400 180" />
          <path className="route" d="M -120 300 Q 560 740 1400 500" />
          <path className="route" d="M -120 480 Q 700 380 1400 320" />
          {/* bright glints travelling each arc (pathLength=1000 → normalized
              so the dash maths is resolution-independent) */}
          <path className="glint" pathLength={1000} d="M -120 640 Q 420 240 1400 180" />
          <path className="glint" pathLength={1000} d="M -120 300 Q 560 740 1400 500" />
          <path className="glint" pathLength={1000} d="M -120 480 Q 700 380 1400 320" />
        </svg>

        {/* breathing beacon rings + steady waypoints */}
        <span className="wp-ring" />
        <span className="wp-ring" />
        <span className="wp-ring" />
        <span className="wp-pin p1" />
        <span className="wp-pin p2" />
        <span className="wp-pin p3" />
        <span className="wp-pin p4" />
        <span className="wp-pin p5" />
        <span className="wp-sheen" />
      </div>

      {/* brand — pinned top-left over the whole screen */}
      <div className="hero-brand">
        <span className="beacon" />
        Waypoint
      </div>

      {/* ---- LEFT: the hero pitch ------------------------------------------ */}
      <section className="hero">
        <div className="hero-inner">
          <h1 className="hero-title">
            Book your team's flights, on budget.
          </h1>
          <p className="hero-lead">
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
        </div>
      </section>

      {/* ---- RIGHT: the start card --------------------------------------- */}
      <section className="hero-form">
        <div className="start">
        <h2 className="start-title">
          Set your limits
        </h2>
        <p className="start-sub">
          Your budget and booking caps — Waypoint holds to these on every fare.
        </p>

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
          {/* optional trip context — same styling, deliberately NOT part
              of constraintsValid; leaving these blank changes nothing */}
          <label className="constraint-field">
            <span className="constraint-k">Team size</span>
            <input
              type="number"
              value={Number.isNaN(teamSize) ? "" : teamSize}
              min={1}
              max={50}
              step={1}
              onChange={(e) => setTeamSize(e.target.valueAsNumber)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">Destination</span>
            <input
              type="text"
              value={destination}
              placeholder="e.g. London, UK"
              onChange={(e) => setDestination(e.target.value)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">Trip purpose</span>
            <input
              type="text"
              value={tripPurpose}
              placeholder="e.g. Q4 client visits"
              onChange={(e) => setTripPurpose(e.target.value)}
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
      </section>
    </main>
  );
}
