"use client";

// Screen 1 — the mandate (confirm-and-go).
// The mandate is seeded SERVER-SIDE; this card is display copy only — the
// real mandate (holder, budget, authority cap) arrives via the stream's
// `meta` event on Screen 2. Nothing here fabricates API numbers.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { seedDesk } from "@/lib/api";

export default function MandatePage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
          ? `${err.message} — the desk backend may not be running`
          : "Could not reach the desk backend"
      );
    }
  }

  return (
    <main className="wrap">
      <div className="brand">
        WAYPOINT<span className="tick">.</span>
      </div>
      <div className="sub">the travel treasury desk</div>

      <div className="mandate-intro">
        <h1>
          Corporate travel, run like a <em>trading desk</em>.
        </h1>
        <p>
          A mandate seeds the desk: a budget, an authority cap, and a book of
          held travel positions. The agent marks them to market, judges
          hold-vs-book, executes behind a code wall, and admits losses —
          honestly, in the open.
        </p>

        <div className="card">
          <ul className="mandate-points">
            <li>
              <span className="pt-k">mandate</span>
              <span>
                seeded on the server — holder, budget and authority cap appear
                on the desk the moment the stream opens
              </span>
            </li>
            <li>
              <span className="pt-k">search meter</span>
              <span>
                bounded spend: every live query is metered, and the meter is
                always on screen
              </span>
            </li>
            <li>
              <span className="pt-k">one human click</span>
              <span>
                anything over the authority cap escalates to two priced
                options and a recommendation
              </span>
            </li>
            <li>
              <span className="pt-k">honesty</span>
              <span>
                every disclosure rides in-frame — comparison mode, stale
                marks, ledger-only allocations, all labeled
              </span>
            </li>
          </ul>
        </div>

        <button className="cta" onClick={openDesk} disabled={busy}>
          {busy ? "Seeding the mandate…" : "Open the desk →"}
        </button>
        {error && <div className="status err">{error}</div>}
        <div className="note">
          Opening the desk seeds the mandate and starts the cycle server-side;
          you watch it live on one SSE stream.
        </div>
      </div>
    </main>
  );
}
