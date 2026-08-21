"use client";

// Screen 3 — Recovery outcome (mockup 03).
// Fetches GET /api/trips/{id}/recovery and renders it honestly for the
// current slice:
//   - Slice 2: status "pending" — real search found a top candidate, but
//     rules (Slice 3), ranking (Slice 4) and booking (Slice 5) haven't
//     run yet: no rejection column, no fare settlement, no PNR/ticket.
//   - Slice 5+: status "recovered" — the before/after with the rejected
//     cheapest vs booked legal, fare diff settled, ticket asserted.

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { getRecovery } from "@/lib/api";
import { COUNTRY_NAMES, formatHours, viaAirport } from "@/lib/format";
import type { Layover, RecoveryResult } from "@/lib/types";

export default function RecoveredPage() {
  const params = useParams<{ tripId: string }>();
  const tripId = params.tripId;

  const [result, setResult] = useState<RecoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRecovery(tripId)
      .then((r) => {
        if (!cancelled) setResult(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tripId]);

  if (error) {
    return (
      <main className="wrap">
        <div className="brand">WAYPOINT</div>
        <div className="status err">{error}</div>
      </main>
    );
  }
  if (!result) {
    return (
      <main className="wrap">
        <div className="brand">WAYPOINT</div>
        <div className="status">Loading recovery…</div>
      </main>
    );
  }

  // Slice 2's honest intermediate state: a top candidate, nothing booked.
  if (result.status === "pending" && result.chosen) {
    const candidate = result.chosen;
    const via = viaAirport(candidate);
    const lo: Layover | undefined = (result.layovers ?? []).find(
      (l) => l.airport === via
    );
    const country = lo?.country
      ? COUNTRY_NAMES[lo.country] ?? lo.country
      : null;
    return (
      <main className="wrap">
        <div className="brand">WAYPOINT</div>
        <div className="head">Top candidate found — nothing booked yet.</div>

        <div className="cols">
          <div className="col good">
            <div className="tag good">Top candidate · cheapest found</div>
            <div className="big">
              ${candidate.price} · via {via}
            </div>
            <div className="small">
              {country ? `${country} connection` : "connecting itinerary"}
              {lo?.city ? ` · ${lo.city}` : ""} ·{" "}
              {formatHours(candidate.total_minutes)} total
            </div>
          </div>
        </div>

        <div className="cap">
          Rules verdicts (Slice 3), Qwen ranking (Slice 4) and sandbox booking
          (Slice 5) land next — until then Waypoint refuses to claim a booking
          it hasn&apos;t made. No rejection is shown because nothing has been
          vetted against your passport yet.
        </div>
      </main>
    );
  }

  if (result.status !== "recovered" || !result.chosen || !result.order) {
    return (
      <main className="wrap">
        <div className="brand">WAYPOINT</div>
        <div className="status">Recovery ended: {result.status}</div>
      </main>
    );
  }

  const { chosen, rejected_cheapest: rejected, order } = result;
  const chosenVia = viaAirport(chosen);
  const rejectedVia = rejected ? viaAirport(rejected) : "—";

  // Slice 2+: country/city ride on the wire (RecoveryResult.layovers) —
  // no hardcoded airport->country map in the frontend anymore.
  const wireLayovers = result.layovers ?? [];
  function layoverInfo(airport: string): { country: string; city: string } {
    const lo: Layover | undefined = wireLayovers.find(
      (l) => l.airport === airport
    );
    return {
      country: lo?.country
        ? COUNTRY_NAMES[lo.country] ?? lo.country
        : airport,
      city: lo?.city || airport,
    };
  }
  const rejectedInfo = layoverInfo(rejectedVia);

  // Real cheapest can undercut the original fare — render the sign honestly.
  const fareDiff = Number(order.fare_diff);
  const fareDiffLabel =
    fareDiff >= 0
      ? `+$${fareDiff}`
      : `\u2212$${Math.abs(fareDiff)} (refunded)`;

  return (
    <main className="wrap">
      <div className="brand">WAYPOINT</div>
      <div className="head">Recovered — and you can actually board.</div>

      {result.rationale && (
        <div className="rationale">{result.rationale}</div>
      )}

      <div className="cols">
        {/* The rejected column only exists once the rules engine (Slice 3)
            has actually rejected something. */}
        {rejected && (
          <div className="col bad">
            <div className="tag bad">Naive cheapest — rejected</div>
            <div className="big strike">
              ${rejected.price} · via {rejectedVia}
            </div>
            <div className="small">
              {rejectedInfo.country} self-transfer · <b>visa required</b> for
              your passport → denied at gate
            </div>
          </div>
        )}
        <div className="col good">
          <div className="tag good">Waypoint booked</div>
          <div className="big">
            ${chosen.price} · via {chosenVia}
          </div>
        </div>
      </div>

      <div className="settle">
        <div className="row">
          <span>Original fare paid</span>
          <b>${order.original_fare}</b>
        </div>
        <div className="row">
          <span>New legal reroute</span>
          <b>${order.new_fare}</b>
        </div>
        <div className="row">
          <span>Fare difference — auto-settled in sandbox</span>
          <b>{fareDiffLabel}</b>
        </div>
        <div className="ticket">
          ✔ order created · PNR <b>{order.pnr}</b>
          <br />✔ payment confirmed (sandbox)
          <br />✔ ticket issued — outcome asserted, not assumed
        </div>
      </div>

      {rejected && (
        <div className="punch">
          Without Waypoint you&apos;d have been denied boarding in{" "}
          {rejectedInfo.city}.
        </div>
      )}
    </main>
  );
}
