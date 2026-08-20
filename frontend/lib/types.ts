// Frontend mirror of the backend Gate 3 domain types (app/models.py).
// Decimals serialize as strings; prices render directly (e.g. "$458").

export interface Passenger {
  name: string;
  passport_country: string;
  passport_expiry: string;
  doc_number: string;
  issuing_country: string;
}

export interface Segment {
  dep_airport: string;
  arr_airport: string;
  dep_time: string;
  arr_time: string;
  flight_number: string;
  direction: string;
  status: "active" | "cancelled";
}

export interface Layover {
  airport: string;
  country: string;
  hours: number;
  same_ticket: boolean;
}

export interface Offer {
  id: string;
  atlas_offer_id: string;
  price: string; // Decimal -> string
  currency: string;
  total_minutes: number;
  segments: Segment[];
  price_status: "reference" | "current" | "verified";
  bookable: boolean;
  same_ticket: boolean;
}

export type VerdictStatus = "allowed" | "blocked" | "unknown";

export interface RuleVerdict {
  rule_name: string;
  status: VerdictStatus;
  reason: string;
  source: string | null;
  last_checked: string | null;
}

// One row of Screen 2's table (an OfferAssessment + precomputed layovers).
export interface Assessment {
  offer: Offer;
  layovers: Layover[];
  verdicts: RuleVerdict[];
  executable: boolean;
}

export interface Order {
  order_no: string;
  pnr: string;
  ticket_number: string;
  original_fare: string;
  new_fare: string;
  fare_diff: string;
  currency: string;
  settled: boolean;
  ticket_asserted: boolean;
}

export interface RecoveryResult {
  trip_id: string;
  status: "recovered" | "no_legal_option" | "needs_override" | "failed";
  chosen: Offer | null;
  rejected_cheapest: Offer | null;
  order: Order | null;
  step_count: number;
  rationale: string | null;
}

// The SSE event contract (backend/agent/loop.py).
export type StreamEvent =
  | { type: "meta"; trip_id: string; step_budget: number }
  | { type: "step"; n: number; text: string }
  | { type: "options"; assessments: Assessment[] }
  | { type: "decision"; chosen_offer_id: string; rationale: string }
  | { type: "result"; result: RecoveryResult }
  | { type: "error"; message: string };
