"use client";

// Screen 1 — Trip disrupted (mockup 01).
// Static demo choreography in Slice 1: the passenger + cancelled leg render
// from fixed constants. It becomes data-driven (POST /api/trips) when the
// real webhook trigger lands in Slice 7.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { reportDisruption } from "@/lib/api";

export default function TripDisruptedPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function recover() {
    setBusy(true);
    setError(null);
    try {
      const tripId = await reportDisruption();
      router.push(`/recovering/${tripId}`);
    } catch (err) {
      setBusy(false);
      setError(
        err instanceof Error ? err.message : "Could not reach the Waypoint backend"
      );
    }
  }

  return (
    <main className="wrap">
      <div className="brand">WAYPOINT</div>
      <div className="sub">Your trip · SIN → NRT · departing today</div>

      <div className="pax">
        ✈ Traveler: <b>TEST/TRAVELER</b> · Passport: <b>India 🇮🇳</b>
      </div>

      <div className="leg dead">
        <div className="route">SIN → NRT</div>
        <div className="meta">Scoot TR866 · 01:00 → 11:15 · nonstop</div>
        <span className="badge dead">CANCELLED</span>
      </div>

      <div className="leg">
        <div className="route">NRT → hotel · airport transfer booked</div>
        <div className="meta">Downstream plans depend on landing 11:15</div>
        <span className="badge ok">AT RISK</span>
      </div>

      <button className="cta" onClick={recover} disabled={busy}>
        {busy ? "Contacting Waypoint…" : "Recover my trip →"}
      </button>
      {error && <div className="status err">{error}</div>}
      <div className="note">
        Waypoint reads your passport before it rebooks. No gate surprises.
      </div>
    </main>
  );
}
