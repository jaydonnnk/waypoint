"use client";

// Screen 2 — the live desk / blotter.
//
// REPLAY SAFETY: the server replays the FULL stream from event 0 on every
// (re)connect, and events carry no global sequence field. So on every
// stream `open` we WIPE all screen state and rebuild from scratch, keying
// blotter rows by arrival index. React StrictMode's double-mount is
// absorbed the same way: the effect creates the EventSource, the cleanup
// closes it, and the second mount wipes + rebuilds from replay — one
// clean stream, never double-appended rows.

import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useReducer, useRef, useState } from "react";

import { deskStreamUrl, postEscalationDecision } from "@/lib/api";
import { money } from "@/lib/format";
import type {
  DeskResult,
  EscalationOption,
  Mandate,
  StreamEvent,
} from "@/lib/types";

// ---------- screen state (reducer so the wipe is one atomic action) ------

type BlotterRow = { ix: number; event: StreamEvent };

type DeskState = {
  mandate: Mandate | null;
  mode: string | null;
  disclosures: string[];
  meter: { used: number; max: number } | null; // always SET, never incremented
  steps: { n: number; text: string }[];
  blotter: BlotterRow[];
  result: DeskResult | null;
  // True once the terminal DESK_CYCLE_FAILED error lands (backend crash
  // path: one error event, no result, stream ends). Settled end-state.
  cycleFailed: boolean;
  toast: { key: number; k: string; body: string } | null;
};

const INITIAL: DeskState = {
  mandate: null,
  mode: null,
  disclosures: [],
  meter: null,
  steps: [],
  blotter: [],
  result: null,
  cycleFailed: false,
  toast: null,
};

type Action =
  | { kind: "wipe" }
  | { kind: "event"; event: StreamEvent; ix: number };

function reducer(state: DeskState, action: Action): DeskState {
  if (action.kind === "wipe") return INITIAL;
  const { event, ix } = action;
  switch (event.type) {
    case "meta":
      return {
        ...state,
        mandate: event.mandate,
        mode: event.mode,
        disclosures: event.disclosures,
        meter: event.meter, // SET — concurrent fan-out can duplicate/reorder
      };
    case "step":
      return { ...state, steps: [...state.steps, { n: event.n, text: event.text }] };
    case "result":
      return { ...state, result: event.result };
    case "error":
      // Errors render as disclosed blotter lines (code only, never a raw
      // message), so they stay in the record. DESK_CYCLE_FAILED is the one
      // TERMINAL error: the backend crash path emits it and then ends the
      // stream without a result — mark the screen settled. Every other
      // code is mid-cycle; the stream stays open for them.
      return {
        ...state,
        blotter: [...state.blotter, { ix, event }],
        cycleFailed: event.code === "DESK_CYCLE_FAILED" || state.cycleFailed,
      };
    case "trade": {
      const next: DeskState = {
        ...state,
        blotter: [...state.blotter, { ix, event }],
      };
      // Cold-open toast, live from the event itself. The book beat reads
      // "book decision logged" — never a booking state the backend didn't
      // emit. Only the event's own rationale is shown.
      next.toast = {
        key: ix,
        k: event.kind === "book" ? "book decision logged" : `${event.kind} call`,
        body: `${event.position_id} — ${event.rationale}`,
      };
      return next;
    }
    case "mark": {
      const next: DeskState = {
        ...state,
        blotter: [...state.blotter, { ix, event }],
        meter: state.meter
          ? { ...state.meter, used: event.meter_used } // SET, never +=
          : { used: event.meter_used, max: 0 },
      };
      next.toast = event.stale
        ? {
            key: ix,
            k: "stale mark — disclosed",
            body: `${event.position_id} held at last mark (${event.new})`,
          }
        : {
            key: ix,
            k: "marked to market",
            body: `${event.position_id} · ${event.old} → ${event.new}`,
          };
      return next;
    }
    case "loss":
    case "escalate":
    case "reconcile":
    case "alloc":
      return { ...state, blotter: [...state.blotter, { ix, event }] };
    default:
      return state;
  }
}

// ---------- escalation decision state (per esc_id) ------------------------

type Decision =
  | { state: "open" }
  | { state: "busy" }
  | { state: "chosen"; choice: "A" | "B" }
  | { state: "gone" }
  | { state: "failed"; detail: string };

// First word of an option label ("hold — re-check next cycle…" -> "hold").
function shortLabel(options: EscalationOption[], key: "A" | "B"): string {
  const opt = options.find((o) => o.key === key);
  if (!opt) return key;
  return opt.label.split("—")[0].trim() || key;
}

export default function DeskPage() {
  const params = useParams<{ deskId: string }>();
  const deskId = params.deskId;

  const [screen, dispatch] = useReducer(reducer, INITIAL);
  const [connected, setConnected] = useState(false);
  // Terminal stream death (e.g. unknown deskId -> the stream endpoint
  // 404s and EventSource fires one error with readyState CLOSED, no retry).
  const [streamDead, setStreamDead] = useState(false);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const ixRef = useRef(0);

  useEffect(() => {
    const source = new EventSource(deskStreamUrl(deskId));

    source.onopen = () => {
      // (Re)connect = full server replay from event 0. Wipe and rebuild.
      setConnected(true);
      setStreamDead(false);
      ixRef.current = 0;
      setDecisions({});
      dispatch({ kind: "wipe" });
    };
    source.onerror = () => {
      setConnected(false);
      // CLOSED = the browser will NOT retry (404 unknown desk, or the
      // server refused outright). Surface a terminal line instead of
      // spinning on "connecting…" forever.
      if (source.readyState === EventSource.CLOSED) {
        setStreamDead(true);
      }
    };
    source.onmessage = (msg) => {
      let event: StreamEvent;
      try {
        event = JSON.parse(msg.data) as StreamEvent;
      } catch {
        return; // never render a frame we can't type
      }
      const ix = ixRef.current;
      ixRef.current += 1;
      dispatch({ kind: "event", event, ix });
      // Terminal stops: the `result` event, OR the crash path's terminal
      // DESK_CYCLE_FAILED error (stream ends there with no result —
      // leaving the source open would auto-reconnect and wipe/rebuild in
      // an infinite loop). All other error codes are mid-cycle: stay open.
      const terminal =
        event.type === "result" ||
        (event.type === "error" && event.code === "DESK_CYCLE_FAILED");
      if (terminal) {
        source.close();
      }
    };

    return () => source.close(); // StrictMode: second mount rebuilds via replay
  }, [deskId]);

  // Toast auto-hide (re-keyed by event index so replays re-animate it).
  const [toastShown, setToastShown] = useState(false);
  useEffect(() => {
    if (!screen.toast) return;
    setToastShown(true);
    const t = setTimeout(() => setToastShown(false), 6000);
    return () => clearTimeout(t);
  }, [screen.toast]);

  // No blotter amounts render before `meta` arrives, so an undefined
  // currency here just means "render without a symbol", never a guess.
  const currency = screen.mandate?.currency;
  const live = screen.mode === "live ticketing";

  async function decide(escId: string, choice: "A" | "B") {
    setDecisions((d) => ({ ...d, [escId]: { state: "busy" } }));
    const outcome = await postEscalationDecision(deskId, escId, choice);
    setDecisions((d) => ({
      ...d,
      [escId]:
        outcome.kind === "accepted"
          ? { state: "chosen", choice: outcome.choice }
          : outcome.kind === "gone"
            ? { state: "gone" }
            : { state: "failed", detail: outcome.detail },
    }));
  }

  // ---------- render helpers ----------------------------------------------

  function renderBlotter(row: BlotterRow) {
    const { event, ix } = row;
    const pos =
      "position_id" in event && event.position_id ? event.position_id : null;
    const posEl = pos && <span className="b-pos">{pos}</span>;
    const ixEl = <span className="b-ix">{String(ix + 1).padStart(2, "0")}</span>;

    switch (event.type) {
      case "loss":
        return (
          <div key={ix} className="brow bad">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip loss">loss admitted</span>
                {posEl}
                <span className="num">{money(event.amount, currency)}</span>
              </div>
              <div className="b-body">{event.note}</div>
              <div className="b-disc">disclosure: {event.disclosure}</div>
            </div>
          </div>
        );
      case "trade":
        return (
          <div key={ix} className="brow accent">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip trade">{event.kind}</span>
                {posEl}
              </div>
              <div className="b-body">{event.rationale}</div>
            </div>
          </div>
        );
      case "mark":
        return (
          <div key={ix} className={event.stale ? "brow warn" : "brow good"}>
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className={event.stale ? "chip mark-stale" : "chip mark"}>
                  {event.stale ? "mark · stale" : "mark"}
                </span>
                {posEl}
                <span className="num">
                  {money(event.old, currency)} → {money(event.new, currency)}
                </span>
              </div>
              <div className="b-body">
                {event.stale
                  ? "held at the last mark — search skipped, uncertainty disclosed"
                  : event.search_ref
                    ? `reprice ref ${event.search_ref} · meter at ${event.meter_used}`
                    : `meter at ${event.meter_used}`}
              </div>
              {event.disclosure && (
                <div className="b-disc">disclosure: {event.disclosure}</div>
              )}
            </div>
          </div>
        );
      case "escalate": {
        const decision: Decision = decisions[event.esc_id] ?? { state: "open" };
        return (
          <div key={ix} className="brow warn">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip escalate">escalation</span>
                {posEl}
              </div>
              <div className="esc">
                <div className="esc-reason">{event.reason}</div>
                <div className="esc-opts">
                  {event.options.map((opt) => (
                    <div
                      key={opt.key}
                      className={
                        opt.key === event.recommendation
                          ? "esc-opt rec"
                          : "esc-opt"
                      }
                    >
                      <span className="esc-key">{opt.key}</span>
                      <span>{opt.label}</span>
                      {opt.key === event.recommendation && (
                        <span className="rec-flag">recommended</span>
                      )}
                      <span className="esc-price">
                        {money(opt.price, currency)}
                      </span>
                    </div>
                  ))}
                </div>

                {!live ? (
                  <div className="esc-note">
                    auto-resolved to {event.recommendation} (
                    {shortLabel(event.options, event.recommendation)}) —
                    comparison mode; in live mode this is your one human
                    click.
                  </div>
                ) : (
                  <>
                    <div className="esc-buttons">
                      {event.options.map((opt) => (
                        <button
                          key={opt.key}
                          className={
                            decision.state === "chosen" &&
                            decision.choice === opt.key
                              ? "esc-btn chosen"
                              : "esc-btn"
                          }
                          disabled={
                            decision.state === "busy" ||
                            decision.state === "chosen" ||
                            decision.state === "gone"
                          }
                          onClick={() => decide(event.esc_id, opt.key)}
                        >
                          {decision.state === "chosen" &&
                          decision.choice === opt.key
                            ? `${opt.key} — clicked`
                            : `choose ${opt.key}`}
                        </button>
                      ))}
                    </div>
                    {decision.state === "gone" && (
                      <div className="esc-note">
                        already resolved — the desk moved on
                      </div>
                    )}
                    {decision.state === "failed" && (
                      <div className="esc-note">{decision.detail}</div>
                    )}
                  </>
                )}

                <div className="esc-discs">
                  {event.disclosures.map((d) => d).join(" · ")}
                </div>
              </div>
            </div>
          </div>
        );
      }
      case "reconcile":
        return (
          <div key={ix} className="brow">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip reconcile">reconcile</span>
                {posEl}
                <span className="num">{money(event.delta, currency)}</span>
              </div>
              <div className="b-body">
                price changed — resolution: <b>{event.resolution}</b>
              </div>
              <div className="b-disc">disclosure: {event.disclosure}</div>
            </div>
          </div>
        );
      case "alloc":
        return (
          <div key={ix} className="brow good">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip alloc">alloc</span>
                {posEl}
                <span className="num">{money(event.amount, currency)}</span>
              </div>
              <div className="b-body">
                realized savings allocated · seat ref{" "}
                <b>{event.seat_ref}</b>
              </div>
              <div className="b-disc">disclosure: {event.disclosure}</div>
            </div>
          </div>
        );
      case "error":
        return (
          <div key={ix} className="brow bad">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip error">error</span>
                {posEl}
                <span className="num">{event.code}</span>
              </div>
              <div className="b-disc">
                disclosed failure — code only; the raw message never leaves
                the server
              </div>
            </div>
          </div>
        );
      default:
        return null;
    }
  }

  const meterPct =
    screen.meter && screen.meter.max > 0
      ? Math.min(100, Math.round((screen.meter.used / screen.meter.max) * 100))
      : 0;
  const meterHeat = meterPct >= 85 ? "hot" : meterPct >= 60 ? "warm" : "";

  return (
    <main className="wrap">
      <div className="brand">
        WAYPOINT<span className="tick">.</span>
      </div>
      <div className="sub">
        live desk · {deskId} ·{" "}
        {connected
          ? "stream connected"
          : streamDead
            ? "stream closed"
            : "connecting…"}
      </div>
      {streamDead && (
        <div className="status err">
          stream closed — desk not found or cycle ended
        </div>
      )}

      {/* ---- header: mandate card + meter + mode ------------------------ */}
      <div className="desk-head">
        <div className="card mandate-card">
          <div className="mc-holder">
            {screen.mandate ? screen.mandate.holder : "mandate loading…"}
          </div>
          <div className="mc-id">{deskId}</div>
          <div className="mc-figures">
            <div>
              <div className="fig-k">budget</div>
              <div className="fig-v">
                {screen.mandate
                  ? money(screen.mandate.budget_total, currency)
                  : "—"}
              </div>
            </div>
            <div>
              <div className="fig-k">authority cap</div>
              <div className="fig-v">
                {screen.mandate
                  ? money(screen.mandate.authority_cap, currency)
                  : "—"}
              </div>
            </div>
            <div>
              <div className="fig-k">contingency</div>
              <div className="fig-v">
                {screen.mandate ? `${screen.mandate.contingency_pct}%` : "—"}
              </div>
            </div>
          </div>
        </div>

        <div className="desk-side">
          {/* search meter — ALWAYS visible, always SET to the received value */}
          <div className="meter">
            <div className="meter-k">
              <span>search meter</span>
              <span>bounded spend</span>
            </div>
            <div className="meter-read">
              {screen.meter ? `${screen.meter.used} / ${screen.meter.max}` : "— / —"}
            </div>
            <div className="meter-track">
              <div
                className={`meter-fill ${meterHeat}`}
                style={{ width: `${meterPct}%` }}
              />
            </div>
          </div>

          <div
            className={live ? "mode-label live" : "mode-label comparison"}
          >
            {screen.mode ?? "mode pending — waiting for meta"}
          </div>
        </div>
      </div>

      {/* ---- disclosure register: every meta.disclosures[] string ------- */}
      {screen.disclosures.length > 0 && (
        <div className="register">
          <div className="register-k">disclosure register</div>
          <ul>
            {screen.disclosures.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- narration feed --------------------------------------------- */}
      <div className="section-k">narration</div>
      <div className="stream">
        {screen.steps.length === 0 && (
          <div>
            <span className="dim">›</span> waiting for the desk to speak…
          </div>
        )}
        {screen.steps.map((s, i) => (
          <div
            key={`${s.n}-${i}`}
            className={
              i === screen.steps.length - 1 &&
              !screen.result &&
              !screen.cycleFailed
                ? "cur"
                : undefined
            }
          >
            <span className="dim">›</span> {s.text}
          </div>
        ))}
      </div>

      {/* ---- the blotter ------------------------------------------------ */}
      <div className="section-k">blotter</div>
      <div className="blotter">
        {screen.blotter.length === 0 && (
          <div className="brow">
            <div className="b-main">
              <div className="b-body" style={{ color: "var(--mut)" }}>
                no blotter entries yet — marks, calls and settlements land
                here as the cycle runs
              </div>
            </div>
          </div>
        )}
        {screen.blotter.map(renderBlotter)}
      </div>

      {/* ---- terminal result -> the weekly close ------------------------ */}
      {screen.result && (
        <div className="result-banner">
          <div>
            <div className="rb-k">cycle complete · {screen.result.status}</div>
            <div style={{ fontSize: 13, marginTop: 2 }}>
              the blotter above is the record — the weekly close settles it
            </div>
          </div>
          <Link className="cta" href={`/close/${deskId}`}>
            Go to the weekly close →
          </Link>
        </div>
      )}

      {/* ---- crash path: terminal DESK_CYCLE_FAILED, no result emitted -- */}
      {screen.cycleFailed && !screen.result && (
        <div className="result-banner">
          <div>
            <div className="rb-k">cycle failed — disclosed</div>
            <div style={{ fontSize: 13, marginTop: 2 }}>
              the desk ended abnormally and emitted no result; the blotter
              above is everything that was disclosed before it stopped
            </div>
          </div>
        </div>
      )}

      {/* ---- cold-open toast: live from real trade/mark events ---------- */}
      {screen.toast && toastShown && (
        <div className="toast" key={screen.toast.key}>
          <div className="toast-k">{screen.toast.k}</div>
          {screen.toast.body}
        </div>
      )}
    </main>
  );
}
