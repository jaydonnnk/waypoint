"use client";

// Screen 3 — Recovery confirmed (mockup 03).
// Fetches GET /api/trips/{id}/recovery and renders the before/after:
// rejected cheapest vs booked legal, fare diff auto-settled, PNR/ticket.

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { getRecovery } from "@/lib/api";
import { CITY_NAMES, COUNTRY_NAMES, viaAirport } from "@/lib/format";
import type { RecoveryResult } from "@/lib/types";

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
  const rejectedCountry =
    COUNTRY_NAMES[countryOf(rejectedVia)] ?? rejectedVia;
  const rejectedCity = CITY_NAMES[rejectedVia] ?? rejectedVia;

  return (
    <main className="wrap">
      <div className="brand">WAYPOINT</div>
      <div className="head">Recovered — and you can actually board.</div>

      {result.rationale && (
        <div className="rationale">{result.rationale}</div>
      )}

      <div className="cols">
        <div className="col bad">
          <div className="tag bad">Naive cheapest — rejected</div>
          <div className="big strike">
            ${rejected?.price} · via {rejectedVia}
          </div>
          <div className="small">
            {rejectedCountry} self-transfer · <b>visa required</b> for your
            passport → denied at gate
          </div>
        </div>
        <div className="col good">
          <div className="tag good">Waypoint booked</div>
          <div className="big">
            ${chosen.price} · via {chosenVia}
          </div>
          <div className="small">
            {COUNTRY_NAMES[countryOf(chosenVia)] ?? chosenVia} airside transit
            · <b>legal on your passport</b>
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
          <b>+${order.fare_diff}</b>
        </div>
        <div className="ticket">
          ✔ order created · PNR <b>{order.pnr}</b>
          <br />✔ payment confirmed (sandbox)
          <br />✔ ticket issued — outcome asserted, not assumed
        </div>
      </div>

      <div className="punch">
        Without Waypoint you&apos;d have been denied boarding in {rejectedCity}.
      </div>
    </main>
  );
}

// The tracer offers connect through exactly one hub; its country comes from
// the demo IATA map. Slice 2+ will carry the country on the wire instead.
const AIRPORT_COUNTRY: Record<string, string> = {
  SGN: "VN",
  DMK: "TH",
  ICN: "KR",
  PUS: "KR",
};

function countryOf(airport: string): string {
  return AIRPORT_COUNTRY[airport] ?? "??";
}
