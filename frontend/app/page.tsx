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

  // ---- Screen 1's one signature moment (step 6): a gentle staggered
  // entrance for the start-card contents — transform/opacity only, under
  // ~0.6s total, once on mount. gsap runs only inside useGSAP (client,
  // post-mount), selectors stay inside this page's <main> via scope, and
  // reduced motion skips the tween entirely.
  const scopeRef = useRef<HTMLElement>(null);
  useGSAP(() => {
    gsap.matchMedia().add(
      { reduceMotion: "(prefers-reduced-motion: reduce)" },
      (ctx) => {
        if (ctx.conditions?.reduceMotion) return; // content just renders
        gsap.from(".start-title, .start-sub, .assure, .start-btn", {
          y: 10,
          autoAlpha: 0,
          duration: 0.4,
          ease: "power2.out",
          stagger: 0.06,
        });
      }
    );
  }, { scope: scopeRef });

  async function openDesk() {
    setBusy(true);
    setError(null);
    try {
      const deskId = await seedDesk();
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

        <button className="btn primary start-btn" onClick={openDesk} disabled={busy}>
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
