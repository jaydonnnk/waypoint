import type { RecoveryResult } from "./types";

// The backend origin. Overridable via NEXT_PUBLIC_API_URL.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** POST /api/disruptions -> seeds the trip, starts recovery, returns trip_id. */
export async function reportDisruption(): Promise<string> {
  const res = await fetch(`${API_URL}/api/disruptions`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`POST /api/disruptions failed (${res.status})`);
  }
  const body = await res.json();
  return body.trip_id as string;
}

/** The SSE endpoint URL for a trip's live step stream. */
export function tripStreamUrl(tripId: string): string {
  return `${API_URL}/api/trips/${tripId}/stream`;
}

/** GET /api/trips/{id}/recovery -> the final RecoveryResult. */
export async function getRecovery(tripId: string): Promise<RecoveryResult> {
  const res = await fetch(`${API_URL}/api/trips/${tripId}/recovery`);
  if (!res.ok) {
    throw new Error(`GET /api/trips/${tripId}/recovery failed (${res.status})`);
  }
  return (await res.json()) as RecoveryResult;
}
