"use client";

// Screen 2 — the run (Slice 8 design refit, step 2).
//
// REPLAY SAFETY: the server replays the FULL stream from event 0 on every
// (re)connect, and events carry no global sequence field. So on every
// stream `open` we WIPE all screen state and rebuild from scratch, keying
// rows by arrival index. React StrictMode's double-mount is absorbed the
// same way: the effect creates the EventSource, the cleanup closes it, and
// the second mount wipes + rebuilds from replay — one clean stream, never
// double-appended rows.
//
// REFIT NOTE: the markup below follows mockups/desk-v3.html (the "team
// travel booker" look) and the jargon table in 07-design-refit.md. Every
// figure still binds to the stream/state — the mockup's numbers were
// placeholders. The honesty layer (mode banner, disclosures, stale marks,
// couldn't-book reasons, error codes) stays rendered in "the full record"
// zone at the bottom, behind the step-3 "See the full record" panel.

import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useReducer, useRef, useState } from "react";
import type { ReactNode } from "react";

import gsap from "gsap";
import { useGSAP } from "@gsap/react";

import { deskStreamUrl, postEscalationDecision } from "@/lib/api";
import { money } from "@/lib/format";
import type {
  DeskResult,
  EscalationOption,
  Mandate,
  StreamEvent,
} from "@/lib/types";

gsap.registerPlugin(useGSAP);

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
      // Errors render as disclosed record lines (code only, never a raw
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
      // emit. The raw body keeps the event's own rationale for the record;
      // the toast JSX renders plain copy (plainToast), never the raw string.
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

// ---------- plain-language helpers (copy only, no behavior) ----------------

/** Terminal result status -> what the manager reads. Short and calm. */
function plainStatus(status: DeskResult["status"]): string {
  switch (status) {
    case "closed":
      return "All done";
    case "escalated":
      return "Done — still waiting on your OK";
    case "budget_exhausted":
      return "Stopped at your budget — nothing booked past it";
    case "failed":
      return "Couldn't finish — the full record shows why";
  }
}

/** Trip-card title per event type — glanceable, plain words. */
function tripTitle(event: StreamEvent): string {
  switch (event.type) {
    case "trade":
      return event.kind === "book"
        ? "Booked"
        : event.kind === "hold"
          ? "Waiting on a better price"
          : "Needs your OK";
    case "mark":
      return event.stale ? "Price check — holding for now" : "Price checked";
    case "alloc":
      return "Came in under the quote";
    case "loss":
      return "Price went up — we didn't pay it";
    case "reconcile":
      return "Price changed — handled";
    case "error":
      return "Something went wrong";
    case "escalate":
      return "Needs your OK";
    default:
      return "";
  }
}

/** Two-letter avatar monogram from a trip id — never invents names. */
function tripInitials(id: string): string {
  const clean = id.replace(/[^a-zA-Z0-9]/g, "").toUpperCase();
  return clean.slice(0, 2) || "··";
}

/** Friendly main-view label from a raw position id ("…-pos-1" -> "Trip 1").
    Falls back to a short tag; never invents names or routes. Full raw ids
    stay in the fine-print rows. */
function friendlyTrip(id: string | null): string {
  if (!id) return "Trip";
  const m = id.match(/pos-?(\d+)/i);
  if (m) return `Trip ${m[1]}`;
  const clean = id.replace(/[^a-zA-Z0-9]/g, "");
  return clean.length > 8 ? `Trip ${clean.slice(-6)}` : id;
}

/** Formats the price inside an option label with the same money() used
    everywhere else ("book now at 1790.00 (manual approval)" ->
    "book now at $1,790.00 (manual approval)"). Only the known "at <price>"
    shape is touched — labels with no price ("hold — re-check next cycle …")
    or any other digit run are returned verbatim. Display-only; every
    figure is still the stream's own value. */
function formatLabel(label: string, currency?: string): string {
  return label.replace(/\bat\s+(\d+(?:\.\d+)?)/, (_m, price: string) =>
    `at ${money(price, currency)}`
  );
}

/** Plain one-line verb per escalation option — the recommendation is the
    "book it" path, the alternative is "hold off". */
function optionVerb(key: "A" | "B", recommendation: "A" | "B"): string {
  return key === recommendation ? "Book it" : "Hold off";
}

/** Toast copy for the MAIN view — plain words only. The reducer still
    builds a raw body (position id + rationale) for the record, but the
    toast JSX never renders it: title and body derive from the reducer's
    own `k` tag, so no raw rationale/note string can leak mid-stream. */
function plainToast(k: string): { title: string; body: string } {
  switch (k) {
    case "book decision logged":
      return { title: "Booked", body: "The price looked fair — booked." };
    case "hold call":
      return {
        title: "Holding",
        body: "Holding off until we get a fresh price.",
      };
    case "escalate call":
      return {
        title: "Needs your OK",
        body: "The price moved too much — I asked you first.",
      };
    case "stale mark — disclosed":
      return {
        title: "Price check — holding for now",
        body: "Keeping the last price we saw for now.",
      };
    case "marked to market":
      return {
        title: "Price checked",
        body: "Checked against today's fares.",
      };
    default:
      return { title: k, body: "" };
  }
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
  // "See the full record" panel (step 3) — pure local UI state. Starts
  // collapsed on every page load; the wipe-and-rebuild replay never reads
  // or writes it, and toggling only flips a boolean, so the stream render
  // (row keys, EventSource, reducer) is never disturbed.
  const [recordOpen, setRecordOpen] = useState(false);
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

  // ---------- step 6: the motion pass (gsap) --------------------------------
  // Display-only. Never touches the reducer, dispatch, EventSource or any
  // figure — every animated target binds to state the stream already owns.
  //
  // REPLAY SAFETY: entrances key off the blotter ARRIVAL INDEX (ix) and a
  // ref of the last ix animated, so the wipe-and-rebuild replay animates
  // only freshly appended cards; cards that already settled are never
  // re-animated and can never be left invisible (fromTo always ends at
  // autoAlpha 1, and the JSX final state is the source of truth).
  // No amounts render before `meta` arrives, so an undefined currency here
  // just means "render without a symbol", never a guess.
  const currency = screen.mandate?.currency;
  const live = screen.mode === "live ticketing";

  const scopeRef = useRef<HTMLElement>(null);
  const bigFigRef = useRef<HTMLDivElement>(null);
  const animatedIxRef = useRef(-1); // last blotter ix whose trip card entered
  const countedRef = useRef(false); // big-figure count-up ran for this session

  // Trip cards stagger in as they resolve (one tween per new arrival).
  useGSAP(
    () => {
      gsap.matchMedia().add(
        { reduceMotion: "(prefers-reduced-motion: reduce)" },
        (ctx) => {
          // Wipe (reconnect replay) — forget what has entered so the
          // rebuild enters cleanly.
          if (screen.blotter.length === 0) {
            animatedIxRef.current = -1;
            return;
          }
          const fresh = screen.blotter.filter(
            (r) => r.ix > animatedIxRef.current
          );
          if (fresh.length === 0) return;
          animatedIxRef.current = Math.max(...fresh.map((r) => r.ix));
          if (ctx.conditions?.reduceMotion) return; // cards just render
          gsap.fromTo(
            fresh.map((r) => `.trip[data-ix="${r.ix}"]`),
            { autoAlpha: 0, y: 12 },
            {
              autoAlpha: 1,
              y: 0,
              duration: 0.5,
              ease: "power2.out",
              stagger: 0.09,
            }
          );
        }
      );
    },
    { scope: scopeRef, dependencies: [screen.blotter.length] }
  );

  // Big "Saved / Over by" figure counts up from 0 to the REAL result.pnl
  // magnitude when the terminal result lands — mono, money()-formatted at
  // every frame, and onComplete always lands on the exact real figure.
  useGSAP(
    () => {
      if (!screen.result) {
        countedRef.current = false; // wiped — a fresh session may count again
        return;
      }
      if (countedRef.current) return;
      const el = bigFigRef.current;
      if (!el) return;
      const pnlNum = Number(screen.result.pnl);
      if (!Number.isFinite(pnlNum)) return;
      countedRef.current = true;
      const positive = pnlNum >= 0;
      const magnitude = Math.abs(pnlNum);
      const label = positive ? "Saved " : "Over by ";
      const cur = currency;
      // Identical to the JSX render — the tween only ever counts TOWARD it.
      const finalText = `${label}${money(screen.result.pnl, cur).replace("−", "")}`;
      gsap.matchMedia().add(
        { reduceMotion: "(prefers-reduced-motion: reduce)" },
        (ctx) => {
          if (ctx.conditions?.reduceMotion) {
            el.textContent = finalText; // real figure, no tween
            return;
          }
          const counter = { v: 0 };
          gsap.to(counter, {
            v: magnitude,
            duration: 0.9,
            ease: "power2.out",
            onUpdate: () => {
              const shown = Math.min(Math.round(counter.v), magnitude);
              el.textContent = `${label}${money(String(shown), cur)}`;
            },
            onComplete: () => {
              el.textContent = finalText; // always the real figure
            },
          });
        }
      );
      return () => {
        countedRef.current = false; // StrictMode remount may count again
      };
    },
    { scope: scopeRef, dependencies: [screen.result, currency] }
  );

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

  // ---------- derived, plain-language readouts (counts of real events) ----

  const escRows = screen.blotter.filter((r) => r.event.type === "escalate");
  const openEscRows = escRows.filter((r) => {
    if (r.event.type !== "escalate") return false;
    const d = decisions[r.event.esc_id];
    return !d || d.state === "open" || d.state === "busy" || d.state === "failed";
  });
  const bookedCount = screen.blotter.filter(
    (r) => r.event.type === "trade" && r.event.kind === "book"
  ).length;
  const lossCount = screen.blotter.filter((r) => r.event.type === "loss").length;
  const recCount = screen.blotter.filter(
    (r) => r.event.type === "reconcile"
  ).length;
  const errCount = screen.blotter.filter((r) => r.event.type === "error").length;
  const settled = Boolean(screen.result) || screen.cycleFailed;

  // ---------- render helpers ----------------------------------------------

  /** One trip card per blotter event — desk-v3 row shape, real stream data. */
  function renderTrip(row: BlotterRow) {
    const { event, ix } = row;
    if (event.type === "escalate") return null; // surfaces as THE DECISION card
    const pos =
      "position_id" in event && event.position_id ? event.position_id : null;
    const blocked = event.type === "loss" || event.type === "error";
    const avatarCls = `avatar a${(ix % 4) + 2}`;

    let badge: ReactNode = null;
    let amount: ReactNode = null;
    let extra: ReactNode = null;

    switch (event.type) {
      case "trade":
        badge =
          event.kind === "book" ? (
            <span className="badge ok">Booked</span>
          ) : event.kind === "hold" ? (
            <span className="badge plain">Holding</span>
          ) : (
            <span className="badge wait">Needs your OK</span>
          );
        break;
      case "mark":
        badge = event.stale ? (
          <span className="badge wait">Holding for now</span>
        ) : (
          <span className="badge plain">Checked</span>
        );
        amount = (
          <div className="fare num">
            {money(event.old, currency)} → {money(event.new, currency)}
          </div>
        );
        break;
      case "alloc":
        badge = <span className="badge ok">Saved</span>;
        extra = <div className="saved">saved {money(event.amount, currency)}</div>;
        break;
      case "loss":
        badge = <span className="badge no">Price went up</span>;
        amount = (
          <div className="fare num">
            {money(event.amount, currency).replace("−", "")}
          </div>
        );
        break;
      case "reconcile":
        badge = <span className="badge plain">Handled</span>;
        amount = <div className="fare num">{money(event.delta, currency)}</div>;
        break;
      case "error":
        badge = <span className="badge no">On it</span>;
        amount = <div className="fare num">{event.code}</div>;
        break;
      default:
        return null;
    }

    return (
      <div key={ix} data-ix={ix} className={blocked ? "trip blocked" : "trip"}>
        <div className={avatarCls}>{tripInitials(pos ?? "—")}</div>
        <div className="info">
          <div className="name">{tripTitle(event)}</div>
          <div className="leg">
            {friendlyTrip(pos)}
            {" · "}
            {event.type === "trade"
              ? event.kind === "book"
                ? "The price looked fair, so I booked it."
                : event.kind === "hold"
                  ? "Holding off keeps the budget safe until we get a fresh price."
                  : "The price moved too much to trust — so I asked you first."
              : event.type === "mark"
                ? event.stale
                  ? "keeping the last price we saw for now"
                  : "checked against today's fares"
                : event.type === "alloc"
                  ? "paid less than the quote"
                  : event.type === "loss"
                    ? `Price went up by ${money(
                        event.amount,
                        currency
                      ).replace("−", "")} — we didn't guess or overspend`
                    : event.type === "reconcile"
                      ? "we handled it before booking anything"
                      : "the details are in the full record below"}
          </div>
          {extra}
        </div>
        <div className="right">
          {amount}
          {badge}
        </div>
      </div>
    );
  }

  /** The full record keeps every blotter row (incl. escalation slots) —
      rendered inside the "See the full record" panel (step 3). */
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
                <span className="chip loss">cost more</span>
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
                <span className="chip escalate">needs your OK</span>
                {posEl}
                {decision.state === "chosen" && (
                  <span className="chip alloc">resolved</span>
                )}
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
                      <span>{formatLabel(opt.label, currency)}</span>
                      {opt.key === event.recommendation && (
                        <span className="rec-flag">my pick</span>
                      )}
                      <span className="esc-price">
                        {money(opt.price, currency)}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Buttons render ONLY in live mode — in a dry run the
                    backend never registers an escalation slot, so a click
                    would POST to nothing (410) and flip this card to a
                    false "already sorted". The pick is stated as a note
                    instead; nothing needs clicking. */}
                {live && (
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
                )}
                {!live && (
                  <div className="esc-note">
                    Dry run — I went with {event.recommendation} (
                    {shortLabel(event.options, event.recommendation)}) on this
                    one; nothing needs clicking.
                  </div>
                )}
                {decision.state === "gone" && (
                  <div className="esc-note">
                    already sorted — we moved on
                  </div>
                )}
                {decision.state === "failed" && (
                  <div className="esc-note">{decision.detail}</div>
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

  /** One amber decision card per escalation slot — the desk-v3 .decide. */
  function renderDecision(row: BlotterRow) {
    const { event, ix } = row;
    if (event.type !== "escalate") return null;
    const decision: Decision = decisions[event.esc_id] ?? { state: "open" };
    const recOpt = event.options.find((o) => o.key === event.recommendation);
    return (
      <div key={ix} className="decide">
        <div className="cap">● One thing needs you</div>
        <div className="decide-name">{friendlyTrip(event.position_id)}</div>
        <p className="reason">
          {recOpt
            ? `This trip now costs ${money(recOpt.price, currency)}`
            : "This trip needs your OK"}
          {screen.mandate
            ? ` — that's over your ${money(
                screen.mandate.authority_cap,
                currency
              )} auto-approve limit.`
            : " — it's over your auto-approve limit."}{" "}
          Want me to book it?
        </p>
        <div className="decide-opts">
          {event.options.map((opt) => (
            <div
              key={opt.key}
              className={
                opt.key === event.recommendation ? "decide-opt rec" : "decide-opt"
              }
            >
              <span>
                {opt.key} — {formatLabel(opt.label, currency)}
              </span>
              {opt.key === event.recommendation && (
                <span className="rec-flag">my pick</span>
              )}
              <span className="price num">{money(opt.price, currency)}</span>
            </div>
          ))}
        </div>
        {/* Buttons render ONLY in live mode — in a dry run the backend
            never registers an escalation slot, so a click would POST to
            nothing (410) and flip this card to a false "already sorted".
            The pick is stated as a note instead; nothing needs clicking. */}
        {live && (
          <div className="btns">
            {event.options.map((opt) => (
              <button
                key={opt.key}
                className={
                  decision.state === "chosen" && decision.choice === opt.key
                    ? "btn primary chosen"
                    : opt.key === event.recommendation
                      ? "btn primary"
                      : "btn ghost"
                }
                disabled={
                  decision.state === "busy" ||
                  decision.state === "chosen" ||
                  decision.state === "gone"
                }
                onClick={() => decide(event.esc_id, opt.key)}
              >
                {decision.state === "chosen" && decision.choice === opt.key
                  ? "Done — your pick"
                  : optionVerb(opt.key, event.recommendation)}
              </button>
            ))}
          </div>
        )}
        {!live && (
          <div className="decide-note">
            Dry run — I went with my pick on this one; nothing needs
            clicking.
          </div>
        )}
        {decision.state === "gone" && (
          <div className="decide-note">already sorted — we moved on</div>
        )}
        {decision.state === "failed" && (
          <div className="decide-note">{decision.detail}</div>
        )}
        <div className="fine">
          {screen.mandate
            ? `I book anything under ${money(
                screen.mandate.authority_cap,
                currency
              )} on my own; above that I always ask.`
            : "I book within your auto-approve limit on my own; above it I always ask."}
        </div>
      </div>
    );
  }

  return (
    <main className="wrap" ref={scopeRef}>
      {/* ---- header ------------------------------------------------------ */}
      <div className="top">
        <div className="brand">
          <span className="beacon" />
          Waypoint
        </div>
        <div className={streamDead ? "r-tag err" : "r-tag"}>
          <div>
            {connected
              ? "Live updates on"
              : streamDead
                ? "Connection closed"
                : "Connecting…"}
          </div>
          {/* comparison-mode / live-ticketing disclosure, in plain words */}
          <div
            className={
              screen.mode == null
                ? "mode-banner pending"
                : live
                  ? "mode-banner live"
                  : "mode-banner comparison"
            }
          >
            {screen.mode == null
              ? "Starting up…"
              : live
                ? "Live — booking for real"
                : "Dry run — no real bookings yet"}
          </div>
        </div>
      </div>
      {streamDead && (
        <div className="status err">
          We can't reach this booking — it may have ended or the link is
          wrong.
        </div>
      )}

      {/* ---- run summary -------------------------------------------------- */}
      <div className="run">
        <h1 className="run-title">Waypoint</h1>
        <div className="run-who">
          Booking your team's trips
          <span className="run-id"> · {deskId}</span>
        </div>

        <div className="budget">
          <div className="left">
            Budget{" "}
            <b className="num">
              {screen.mandate
                ? money(screen.mandate.budget_total, currency)
                : "—"}
            </b>
          </div>
          {screen.result ? (
            <div ref={bigFigRef} className="big num">
              {Number(screen.result.pnl) >= 0
                ? `Saved ${money(screen.result.pnl, currency)}`
                : `Over by ${money(screen.result.pnl, currency).replace("−", "")}`}
            </div>
          ) : (
            <div className="big num">{settled ? "" : "Booking…"}</div>
          )}
        </div>
        {/* step 6 honesty: the stream carries no settled-spend figure, so
            the fill is NEVER animated here — an invented percentage would
            be a lie. The track stays honestly empty and the note points to
            the summary page, where real spent-vs-budget figures exist. */}
        <div className="bar" />
        <div className="bar-note">
          The spent-vs-left bar shows up on the summary page.
        </div>

        <div className="statusline">
          <span className="s">
            <span className="pin g" />
            <b>{bookedCount}</b> booked
          </span>
          <span className="s">
            <span className="pin w" />
            <b>{openEscRows.length}</b> need{openEscRows.length === 1 ? "s" : ""}{" "}
            your OK
          </span>
          {lossCount + recCount > 0 && (
            <span className="s">
              <span className="pin r" />
              <b>{lossCount + recCount}</b> price
              {lossCount + recCount === 1 ? " went" : "s went"} up
            </span>
          )}
          {errCount > 0 && (
            <span className="s">
              <span className="pin r" />
              <b>{errCount}</b> hiccup{errCount === 1 ? "" : "s"}
            </span>
          )}
        </div>
      </div>

      {/* ---- the decisions (only when something needs a human) ---------- */}
      {escRows.map(renderDecision)}

      {/* ---- the trips — one card per real stream event ------------------ */}
      <div className="sec">The trips</div>
      {screen.blotter.length === 0 ? (
        <div className="trip empty">
          Just starting — updates will appear here.
        </div>
      ) : (
        screen.blotter.map(renderTrip)
      )}

      {/* ---- the full record: every check, disclosure and code ------------
             (step 3 — collapsed by default, behind a quiet toggle. The
              JSX inside is the step-2 fineprint block near-verbatim:
              nothing deleted, headings relabeled to plain English. The
              panel is local UI state only; it never touches the stream,
              and `hidden` keeps every row mounted so toggling cannot
              disturb the render.) ---------------------------------------- */}
      <div className="record">
        <button
          type="button"
          className="record-toggle"
          aria-expanded={recordOpen}
          aria-controls="full-record"
          onClick={() => setRecordOpen((o) => !o)}
        >
          {recordOpen ? "Hide the full record ↑" : "See the full record →"}
        </button>
        <div id="full-record" className="fineprint" hidden={!recordOpen}>
        <div className="sec fineprint-k">
          The full record{screen.blotter.length > 0 &&
            ` · ${screen.blotter.length} entries`}
        </div>

        {/* search meter — hidden from the main view; stays in the DOM so
            the step-3 record panel can surface it. */}
        <div className="visually-hidden">
          search meter:{" "}
          {screen.meter ? `${screen.meter.used} of ${screen.meter.max}` : "— of —"}
        </div>

        {/* disclosure register: every meta.disclosures[] string */}
        {screen.disclosures.length > 0 && (
          <div className="register">
            <div className="register-k">notes</div>
            <ul>
              {screen.disclosures.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        )}

        {/* narration — the working log, demoted into the fine print */}
        <div className="stream">
          {screen.steps.length === 0 && (
            <div>
              <span className="dim">›</span> Working on it.
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

        {/* the log — every row, incl. escalation slots + buttons */}
        <div className="blotter">
          {screen.blotter.length === 0 && (
            <div className="brow">
              <div className="b-main">
                <div className="b-body dim-note">
                  nothing here yet — every check and decision lands here as
                  it happens
                </div>
              </div>
            </div>
          )}
          {screen.blotter.map(renderBlotter)}
        </div>
        </div>
      </div>

      {/* ---- terminal result -> the summary ------------------------------- */}
      {screen.result && (
        <div className="result-banner done">
          <div>
            <div className="rb-k">{plainStatus(screen.result.status)}</div>
            <div className="rb-sub">
              Every price and decision is in the full record above.
            </div>
          </div>
          <Link className="cta" href={`/close/${deskId}`}>
            See the summary →
          </Link>
        </div>
      )}

      {/* ---- crash path: terminal DESK_CYCLE_FAILED, no result emitted -- */}
      {screen.cycleFailed && !screen.result && (
        <div className="result-banner">
          <div>
            <div className="rb-k">Stopped early</div>
            <div className="rb-sub">
              Something failed, so there's no final result. The full record
              above is everything that happened — and we didn't guess or
              overspend.
            </div>
          </div>
        </div>
      )}

      <div className="note-soft">
        We never book over your budget, and we never invent a fare.
      </div>

      {/* ---- cold-open toast: plain copy only — the reducer's raw body
             (position id + rationale) never renders in the main view;
             it belongs to the full record -------------------------------- */}
      {screen.toast &&
        toastShown &&
        (() => {
          const t = plainToast(screen.toast.k);
          return (
            <div className="toast" key={screen.toast.key}>
              <div className="toast-k">{t.title}</div>
              {t.body}
            </div>
          );
        })()}
    </main>
  );
}
