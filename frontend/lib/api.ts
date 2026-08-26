import type { CloseReport, DeskResult, DeskSnapshot } from "./types";

// The backend origin. Overridable via NEXT_PUBLIC_API_URL.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** POST /api/desk/seed -> seeds the mandate + portfolio, starts the cycle
 * server-side, returns the desk_id. The ops manager's budget constraints
 * travel in the body; contingency_pct is a FRACTION (e.g. 0.05), matching
 * the backend Mandate model (the form converts from percent). */
export async function seedDesk(constraints: {
  budget_total: number;
  authority_cap: number;
  contingency_pct: number;
}): Promise<string> {
  const res = await fetch(`${API_URL}/api/desk/seed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(constraints),
  });
  if (!res.ok) {
    throw new Error(`POST /api/desk/seed failed (${res.status})`);
  }
  const body = await res.json();
  return body.desk_id as string;
}

/** The SSE endpoint URL for a desk's live stream. The server buffers and
 * replays EVERY event from event 0 on each connect/reconnect. */
export function deskStreamUrl(deskId: string): string {
  return `${API_URL}/api/desk/${deskId}/stream`;
}

/** GET /api/desk/{desk_id} -> snapshot (positions, ledger, budgets, meter). */
export async function getDeskSnapshot(deskId: string): Promise<DeskSnapshot> {
  const res = await fetch(`${API_URL}/api/desk/${deskId}`);
  if (!res.ok) {
    throw new Error(`GET /api/desk/${deskId} failed (${res.status})`);
  }
  return (await res.json()) as DeskSnapshot;
}

/** Outcome of GET /api/desk/{desk_id}/close. Branch on `kind`, NEVER on an
 * HTTP code alone: 200 carries a CloseReport (DeskResult + the breach count
 * + auditor line) for every logical outcome
 * (closed / escalated / budget_exhausted / failed); 504 = still running;
 * 500 = the cycle CRASHED (no result exists). */
export type CloseOutcome =
  | { kind: "result"; result: DeskResult; report: CloseReport }
  | { kind: "still_running" }
  | { kind: "crashed" }
  | { kind: "not_found" } // 404 — unknown desk_id; retrying is futile
  | { kind: "unreachable"; detail: string };

export async function getDeskClose(deskId: string): Promise<CloseOutcome> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/desk/${deskId}/close`);
  } catch {
    return { kind: "unreachable", detail: "the desk backend is not reachable" };
  }
  if (res.status === 504) return { kind: "still_running" };
  if (res.status === 500) return { kind: "crashed" };
  if (res.status === 404) return { kind: "not_found" };
  if (!res.ok) {
    return { kind: "unreachable", detail: `unexpected response (${res.status})` };
  }
  // S7: the 200 body is a CloseReport wrapper around DeskResult.
  const report = (await res.json()) as CloseReport;
  return { kind: "result", result: report.result, report };
}

export type DecisionOutcome =
  | { kind: "accepted"; choice: "A" | "B" }
  | { kind: "gone" } // 410 — the slot was consumed/expired; the desk moved on
  | { kind: "failed"; detail: string };

/** POST /api/desk/{desk_id}/escalations/{esc_id}/decision — the one human
 * click. Only meaningful in live-ticketing mode (comparison mode never
 * registers a slot, so the POST there is guaranteed 410). */
export async function postEscalationDecision(
  deskId: string,
  escId: string,
  choice: "A" | "B"
): Promise<DecisionOutcome> {
  let res: Response;
  try {
    res = await fetch(
      `${API_URL}/api/desk/${deskId}/escalations/${escId}/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice }),
      }
    );
  } catch {
    return { kind: "failed", detail: "the desk backend is not reachable" };
  }
  if (res.status === 410) return { kind: "gone" };
  if (!res.ok) {
    return { kind: "failed", detail: `unexpected response (${res.status})` };
  }
  return { kind: "accepted", choice };
}
