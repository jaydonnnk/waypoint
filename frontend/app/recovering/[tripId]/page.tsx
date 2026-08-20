"use client";

// Screen 2 — Agent recovering (mockup 02).
// Opens an EventSource to GET /api/trips/{id}/stream and renders the live
// step log + the options table. State updates are idempotent (step lines are
// indexed by step number) because the backend replays buffered events to any
// client that connects after the agent started — that replay also absorbs
// React StrictMode's double-mount in dev.

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { tripStreamUrl } from "@/lib/api";
import { COUNTRY_NAMES, formatHours } from "@/lib/format";
import type { Assessment, StreamEvent } from "@/lib/types";

export default function RecoveringPage() {
  const params = useParams<{ tripId: string }>();
  const tripId = params.tripId;
  const router = useRouter();

  const [steps, setSteps] = useState<string[]>([]);
  const [stepN, setStepN] = useState(0);
  const [budget, setBudget] = useState(12);
  const [assessments, setAssessments] = useState<Assessment[] | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigatedRef = useRef(false);

  useEffect(() => {
    const source = new EventSource(tripStreamUrl(tripId));

    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as StreamEvent;
      switch (event.type) {
        case "meta":
          setBudget(event.step_budget);
          break;
        case "step":
          setStepN((prev) => Math.max(prev, event.n));
          setSteps((prev) => {
            const next = [...prev];
            next[event.n - 1] = event.text;
            return next;
          });
          break;
        case "options":
          setAssessments(event.assessments);
          break;
        case "decision":
          setChosenId(event.chosen_offer_id);
          break;
        case "result":
          source.close();
          if (!navigatedRef.current) {
            navigatedRef.current = true;
            // Small pause so the completed table reads before Screen 3.
            setTimeout(() => router.push(`/recovered/${tripId}`), 1400);
          }
          break;
        case "error":
          setError(event.message);
          source.close();
          break;
      }
    };

    return () => source.close();
  }, [tripId, router]);

  const cheapestId = assessments
    ? [...assessments].sort(
        (a, b) => Number(a.offer.price) - Number(b.offer.price)
      )[0]?.offer.id
    : null;

  function rowLabel(a: Assessment, index: number): string {
    if (a.offer.id === chosenId) return "Waypoint pick";
    if (a.offer.id === cheapestId) return "Cheapest";
    return `Option ${String.fromCharCode(65 + index)}`; // A, B, ...
  }

  return (
    <main className="wrap">
      <div className="brand">WAYPOINT · recovering</div>

      <div className="stream">
        {steps.length === 0 && (
          <div>
            <span className="dim">›</span> connecting to agent…
          </div>
        )}
        {steps.filter(Boolean).map((text, i) => (
          <div key={i}>
            <span className="dim">›</span> {text}
          </div>
        ))}
      </div>
      <div className="budget">
        step budget: {stepN} / {budget} used · agent will stop and ask if it
        can&apos;t resolve
      </div>

      {assessments && (
        <table>
          <tbody>
            <tr>
              <th>Option</th>
              <th>Via</th>
              <th>Price</th>
              <th>Total time</th>
              <th>Passport verdict</th>
            </tr>
            {assessments.map((a, i) => {
              const picked = a.offer.id === chosenId;
              const blocked = !a.executable;
              const via = a.layovers[0];
              const transit = a.verdicts.find(
                (v) => v.rule_name === "transit_visa"
              );
              const verdictClass =
                transit?.status === "allowed"
                  ? "ok"
                  : transit?.status === "unknown"
                    ? "unknown"
                    : "bad";
              const verdictGlyph =
                transit?.status === "allowed"
                  ? "✅ "
                  : transit?.status === "unknown"
                    ? "⚠️ "
                    : "⛔ ";
              return (
                <tr key={a.offer.id} className={picked ? "pick" : undefined}>
                  <td className={blocked && !picked ? "struck" : undefined}>
                    {rowLabel(a, i)}
                  </td>
                  <td className={["via", blocked && !picked ? "struck" : ""]
                    .filter(Boolean)
                    .join(" ")}
                  >
                    {via
                      ? `${via.airport} (${COUNTRY_NAMES[via.country] ?? via.country})`
                      : "—"}
                  </td>
                  <td className={blocked && !picked ? "struck" : undefined}>
                    ${a.offer.price}
                  </td>
                  <td className={blocked && !picked ? "struck" : undefined}>
                    {formatHours(a.offer.total_minutes)}
                  </td>
                  <td className={`verdict ${verdictClass}`}>
                    {verdictGlyph}
                    {transit?.reason}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="cap">
        Visa rules: curated table for major transit hubs (approximation, stated
        openly). Airside vs. self-transfer distinguished.
      </div>
      {error && <div className="status err">error: {error}</div>}
    </main>
  );
}
