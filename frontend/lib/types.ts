// Frontend mirror of the desk contract (backend/app/models.py + the SSE
// catalog in backend/app/agent/loop.py). Decimals arrive as JSON strings
// and may be negative; render them, never compute with them client-side.

export interface Mandate {
  id: string;
  holder: string;
  created_at: string;
  budget_total: string;
  authority_cap: string;
  contingency_pct: number;
  currency: string;
  // Optional trip context (task #2) — operator-entered, display-only.
  team_size: number;
  destination_label: string;
  trip_purpose: string;
}

export interface Position {
  id: string;
  trip_label: string;
  origin: string;
  dest: string;
  depart_date: string;
  pax: number;
  status: "held" | "booked";
  cost_basis: string;
  mark_price: string;
  mark_at: string;
  mark_stale: boolean;
  atlas_offer_id: string | null;
  atlas_order_no: string | null;
  ticket_asserted: boolean;
}

export interface Budget {
  id: number;
  desk_id: string;
  period: string;
  allocated: string;
  spent: string;
  contingency: string;
}

export type DeskStatus = "closed" | "escalated" | "budget_exhausted" | "failed";

export interface DeskResult {
  desk_id: string;
  status: DeskStatus;
  pnl: string;
  losses_admitted: number;
  step_count: number;
  comparison_mode: boolean;
}

// GET /api/desk/{desk_id}/close 200 body (S7). A wrapper around DeskResult:
// the breach count is deterministic code, the auditor line is narration only.
// The SSE `result` event still carries the bare DeskResult — this shape is
// close-specific.
export interface CloseReport {
  result: DeskResult;
  policy_breaches: number;
  auditor_line: string;
  auditor_source: "agent" | "deterministic-fallback";
  // Task #8 (additive): plain-English twin of `auditor_line`, built in
  // code from the same blotter facts. Optional — absent/null means fall
  // back to the verbatim `auditor_line` in the visible slot.
  auditor_plain?: string | null;
}

// The two priced escalation options — always exactly [A, B] on the wire.
export interface EscalationOption {
  key: "A" | "B";
  label: string;
  price: string;
}

export interface LedgerEntry {
  id: number;
  ts: string;
  kind: string;
  amount: string;
  position_id: string | null;
  ref: string | null;
  note: string | null;
}

// GET /api/desk/{desk_id} snapshot.
export interface DeskSnapshot {
  desk_id: string;
  mandate: Mandate;
  positions: Position[];
  ledger: LedgerEntry[];
  budgets: Budget[];
  meter: { used: number; max: number };
  done: boolean;
}

// Per-rail provenance row on the meta event (S12, ADR 0006). ADDITIVE:
// old replays carry no `rails` field at all — the reducer keeps null and
// the strip renders nothing. `state` is a closed vocabulary branched on
// for tone classes, never parsed: live/recorded/comparison/unknown
// (Atlas), live/fallback (Qwen), curated (priors), real (ledger).
export interface Rail {
  rail: string;
  state: string;
  label: string;
  detail: string;
}

// The SSE event contract (backend/app/agent/loop.py) — 10 types, exact
// field names. There is no book event; booking surfaces as trade kind
// "book" plus the terminal result.
export type StreamEvent =
  | {
      type: "meta";
      desk_id: string;
      mandate: Mandate;
      meter: { used: number; max: number };
      mode: string;
      disclosures: string[];
      rails?: Rail[]; // additive (S12) — absent on old replays
    }
  | { type: "step"; n: number; text: string }
  | {
      type: "loss";
      position_id: string;
      amount: string;
      note: string;
      disclosure: string;
    }
  | {
      type: "trade";
      position_id: string;
      kind: "book" | "hold" | "escalate";
      rationale: string;
    }
  | {
      type: "mark";
      position_id: string;
      old: string;
      new: string;
      search_ref: string | null;
      meter_used: number;
      // Present only on the stale path (search_ref null, stale true);
      // the success path omits both.
      stale?: boolean;
      disclosure?: string;
    }
  | {
      type: "escalate";
      esc_id: string;
      position_id: string;
      reason: string;
      options: EscalationOption[];
      recommendation: "A" | "B";
      disclosures: string[];
    }
  | {
      type: "reconcile";
      position_id: string;
      delta: string;
      resolution: "absorb" | "requote";
      disclosure: string;
    }
  | {
      type: "alloc";
      position_id: string;
      amount: string;
      seat_ref: string;
      disclosure: string;
    }
  | { type: "error"; code: string; position_id?: string }
  | { type: "result"; result: DeskResult };
