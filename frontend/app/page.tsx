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
import { useEffect, useState, useRef } from "react";

import gsap from "gsap";
import { useGSAP } from "@gsap/react";

import { getWaybotUsername, seedDesk } from "@/lib/api";
import type { SeedResult } from "@/lib/types";
import WaypointField from "./WaypointField";

gsap.registerPlugin(useGSAP);

// Waybot gate (S3/M8): seed gated unless NEXT_PUBLIC_WAYBOT_GATED is exactly
// "false" (the recorded/scripted pitch, which runs bot-less and must book
// demo pax byte-safe rather than hold on an empty roster). Inlined at build
// time by Next — a module-level const so the value is fixed per build.
const WAYBOT_GATED = process.env.NEXT_PUBLIC_WAYBOT_GATED !== "false";

export default function MandatePage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Waybot share card (S1). Once a gated seed lands, we DON'T navigate —
  // we show the share link + confirmation code + a static 0/N progress
  // line here, so the manager can drop the link in the team chat.
  const [share, setShare] = useState<SeedResult | null>(null);

  // Waybot identity (task 6): the share link's bot username comes from the
  // backend's GET /api/waybot (derived from WAYPOINT_BOT_TOKEN via getMe)
  // — NEVER hardcoded. null = bot-less (no token / backend down): the
  // invite-link field is then hidden rather than pointing at a wrong bot.
  // Fetched once on mount; the bot initializes at backend startup, long
  // before a gated seed can land, so one fetch settles it.
  const [waybotUsername, setWaybotUsername] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    getWaybotUsername().then((name) => {
      if (!cancelled) setWaybotUsername(name);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Copy-to-clipboard for the share fields. `copied` holds the key of the
  // field whose button was last pressed; it clears after 1.5s (or if the
  // component unmounts first). Falls back silently when the Clipboard API
  // is unavailable — the fields stay selectable/readOnly as before.
  const [copied, setCopied] = useState<string | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);
  function copyField(text: string, key: string) {
    if (!text || !navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(key);
        if (copyTimer.current) clearTimeout(copyTimer.current);
        copyTimer.current = setTimeout(() => setCopied(null), 1500);
      },
      () => {},
    );
  }

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

        // The ambient field (rings, pins, sheen, aurora, route glints) lives
        // in <WaypointField/> now — shared across every screen.
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

  // Pass 10: per-field validity, for the inline affordance. A field is
  // only marked INVALID once it holds a value that is actually out of
  // bounds — a cleared field (NaN, mid-typing) is "not yet filled in",
  // never "wrong", so the form never scolds someone who has not finished.
  const budgetInvalid = Number.isFinite(budgetTotal) && budgetTotal < 1000;
  const capInvalid = Number.isFinite(authorityCap) && authorityCap < 100;
  const contingencyInvalid =
    Number.isFinite(contingencyPct) &&
    (contingencyPct < 0 || contingencyPct > 25);

  async function openDesk() {
    // Belt-and-braces guard — the start button is disabled while invalid.
    if (!constraintsValid) return;
    setBusy(true);
    setError(null);
    try {
      // Contingency convention: the form takes a PERCENT (e.g. 5); the
      // wire format is the FRACTION the backend expects (pct / 100),
      // matching Mandate.contingency_pct / SeedRequest (0.05 default).
      const result = await seedDesk({
        budget_total: budgetTotal,
        authority_cap: authorityCap,
        contingency_pct: contingencyPct / 100,
        // team_size is deliberately NOT in constraintsValid — blank/NaN or
        // an out-of-range typed value normalizes back to the default 1 here,
        // and anything else clamps to the 1–50 Mandate bounds (never send NaN).
        team_size: Number.isInteger(teamSize) ? Math.min(Math.max(teamSize, 1), 50) : 1,
        destination_label: destination.trim(),
        trip_purpose: tripPurpose.trim(),
        // Waybot: seed with the invite gate so we get a share link + code
        // and the cycle waits for the manager's confirm. S3/M8: the
        // recorded/scripted pitch runs bot-less, so NEXT_PUBLIC_WAYBOT_GATED
        // ("false") seeds UNGATED — the desk books demo pax byte-safe instead
        // of holding forever on an empty roster. Default (unset/true) keeps
        // the gated capture flow for the live G1 bot demo.
        gated: WAYBOT_GATED,
      });
      // Backward-compat guard (M2) / ungated recorded pitch: an ungated seed
      // (or a pre-S1 backend) returns only { desk_id } with no invite_token —
      // there's nothing to share and the cycle is already running, so navigate
      // straight to the desk instead of showing a blank share card.
      if (!result.invite_token) {
        router.push(`/desk/${result.desk_id}`);
        return;
      }
      // Show the share card instead of navigating — the manager shares the
      // link, then opens the desk to enter the code once travelers are in.
      setBusy(false);
      setShare(result);
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
      {/* the living waypoint field spans the WHOLE screen, behind both panes */}
      <WaypointField />

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

      {/* ---- RIGHT: the start card (or the share card after a gated seed) - */}
      <section className="hero-form">
        {share ? (
          <div className="start share-card">
            <h2 className="start-title">Share with your team</h2>
            <p className="start-sub">
              Drop this link in your team chat. Each traveler opens it and
              sends their passport — nothing to type by hand.
            </p>

            {waybotUsername && share.invite_token ? (
              <label className="constraint-field share-link">
                <span className="constraint-k">Invite link</span>
                <div className="copy-row">
                  <input
                    type="text"
                    readOnly
                    value={`https://t.me/${waybotUsername}?start=${share.invite_token}`}
                    onFocus={(e) => e.currentTarget.select()}
                  />
                  <button
                    type="button"
                    className="copy-btn"
                    onClick={() =>
                      copyField(
                        `https://t.me/${waybotUsername}?start=${share.invite_token}`,
                        "invite",
                      )
                    }
                  >
                    {copied === "invite" ? "Copied ✓" : "Copy"}
                  </button>
                </div>
              </label>
            ) : (
              <div className="note-soft">
                The Telegram bot isn't live on this deployment — share the
                release code below instead.
              </div>
            )}

            <label className="constraint-field share-code">
              <span className="constraint-k">Your release code (keep private)</span>
              <div className="copy-row">
                <input
                  type="text"
                  readOnly
                  value={share.confirmation_code ?? ""}
                  onFocus={(e) => e.currentTarget.select()}
                />
                <button
                  type="button"
                  className="copy-btn"
                  onClick={() =>
                    copyField(share.confirmation_code ?? "", "code")
                  }
                >
                  {copied === "code" ? "Copied ✓" : "Copy"}
                </button>
              </div>
            </label>

            <div className="note-soft">
              Travelers verified:{" "}
              <b>
                0 /{" "}
                {Number.isInteger(teamSize)
                  ? Math.min(Math.max(teamSize, 1), 50)
                  : 1}
              </b>{" "}
              — you'll enter the code on the desk once everyone's in.
            </div>

            <button
              className="btn primary start-btn"
              onClick={() => router.push(`/desk/${share.desk_id}`)}
            >
              Open the desk →
            </button>
          </div>
        ) : (
        <div className="start">
        <h2 className="start-title">
          Set your limits
        </h2>
        <p className="start-sub">
          Your budget and booking caps — Waypoint holds to these on every fare.
        </p>

        {/* budget constraints — the ops manager's numbers, set up front.
            Pass 10: each numeric field states its own bound up front as a
            hint, and only turns amber once the value is ACTUALLY outside
            it — a field is never marked wrong before it has been filled
            in. The bounds shown are the same ones enforced by
            constraintsValid and by the backend's SeedRequest, so the hint
            cannot drift from the rule. */}
        <div className="constraints">
          <label
            className={`constraint-field${budgetInvalid ? " invalid" : ""}`}
          >
            <span className="constraint-k">
              Total budget
              <span className="constraint-hint">min $1,000</span>
            </span>
            <input
              type="number"
              inputMode="decimal"
              value={Number.isNaN(budgetTotal) ? "" : budgetTotal}
              min={1000}
              step={100}
              aria-invalid={budgetInvalid || undefined}
              onChange={(e) => setBudgetTotal(e.target.valueAsNumber)}
            />
            {budgetInvalid && (
              <span className="constraint-err">
                <i className="pip pip-open" aria-hidden="true" />
                Needs to be $1,000 or more
              </span>
            )}
          </label>
          <label className={`constraint-field${capInvalid ? " invalid" : ""}`}>
            <span className="constraint-k">
              Per-booking cap
              <span className="constraint-hint">min $100</span>
            </span>
            <input
              type="number"
              inputMode="decimal"
              value={Number.isNaN(authorityCap) ? "" : authorityCap}
              min={100}
              step={50}
              aria-invalid={capInvalid || undefined}
              onChange={(e) => setAuthorityCap(e.target.valueAsNumber)}
            />
            {capInvalid && (
              <span className="constraint-err">
                <i className="pip pip-open" aria-hidden="true" />
                Needs to be $100 or more
              </span>
            )}
          </label>
          <label
            className={`constraint-field${contingencyInvalid ? " invalid" : ""}`}
          >
            <span className="constraint-k">
              Contingency
              <span className="constraint-hint">0–25%</span>
            </span>
            <input
              type="number"
              inputMode="decimal"
              value={Number.isNaN(contingencyPct) ? "" : contingencyPct}
              min={0}
              max={25}
              step={1}
              aria-invalid={contingencyInvalid || undefined}
              onChange={(e) => setContingencyPct(e.target.valueAsNumber)}
            />
            {contingencyInvalid && (
              <span className="constraint-err">
                <i className="pip pip-open" aria-hidden="true" />
                Needs to be between 0 and 25
              </span>
            )}
          </label>
          {/* optional trip context — same styling, deliberately NOT part
              of constraintsValid; leaving these blank changes nothing */}
          <label className="constraint-field">
            <span className="constraint-k">
              Team size
              <span className="constraint-hint">optional</span>
            </span>
            <input
              type="number"
              inputMode="numeric"
              value={Number.isNaN(teamSize) ? "" : teamSize}
              min={1}
              max={50}
              step={1}
              onChange={(e) => setTeamSize(e.target.valueAsNumber)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">
              Destination
              <span className="constraint-hint">optional</span>
            </span>
            <input
              type="text"
              value={destination}
              placeholder="e.g. London, UK"
              autoComplete="off"
              onChange={(e) => setDestination(e.target.value)}
            />
          </label>
          <label className="constraint-field">
            <span className="constraint-k">
              Trip purpose
              <span className="constraint-hint">optional</span>
            </span>
            <input
              type="text"
              value={tripPurpose}
              placeholder="e.g. Q4 client visits"
              autoComplete="off"
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
          {busy ? "Starting…" : "Start booking"}
          {!busy && <span className="cta-arrow" aria-hidden="true">→</span>}
        </button>
        {error && (
          <div className="status err">
            <i className="pip pip-cross" aria-hidden="true" />
            {error}
          </div>
        )}

        {/* demoted footnote — how starting works, visibly secondary */}
        <div className="note-soft">
          Starting sets up your budget and booking limits, then runs it all
          live — you'll watch every check and booking as it happens.
        </div>
        </div>
        )}
      </section>
    </main>
  );
}
