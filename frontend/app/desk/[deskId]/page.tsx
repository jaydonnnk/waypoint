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

import { deskStreamUrl, getDeskSnapshot, postEscalationDecision } from "@/lib/api";
import { money } from "@/lib/format";
import type {
  DeskResult,
  EscalationOption,
  Mandate,
  Position,
  Rail,
  StreamEvent,
} from "@/lib/types";

gsap.registerPlugin(useGSAP);

// ---------- screen state (reducer so the wipe is one atomic action) ------

type BlotterRow = { ix: number; event: StreamEvent };

type DeskState = {
  mandate: Mandate | null;
  mode: string | null;
  disclosures: string[];
  // Per-rail provenance (S12): null when the meta event carries no rails
  // (old replays) — the strip renders ONLY when present.
  rails: Rail[] | null;
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
  rails: null,
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
        rails: event.rails ?? null, // additive (S12) — absent → nothing renders
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
      return "Done — one trip still needs your OK";
    case "budget_exhausted":
      return "Budget reached — nothing booked past it";
    case "failed":
      return "Stopped early — details in the full record";
  }
}

/** Trip-card title per event type — glanceable, plain words. */
function tripTitle(event: StreamEvent): string {
  switch (event.type) {
    case "trade":
      return event.kind === "book"
        ? "Booked"
        : event.kind === "hold"
          ? "On hold"
          : "Needs your OK";
    case "mark":
      return event.stale ? "Price held" : "Fare updated";
    case "alloc":
      return "Savings found";
    case "loss":
      return "Price dropped";
    case "reconcile":
      return "Price adjusted";
    case "error":
      return "Issue";
    case "escalate":
      return "Needs your OK";
    default:
      return "";
  }
}

/** Two-letter avatar monogram from the MERGED trip identity — never
    invents names (the data holds no passenger names). Priority:
    1. route initials when origin+dest exist (SIN→NRT -> "SN");
    2. trip-label initials ("Regional sales run" -> "RS");
    3. the position's trailing number (pos-5 -> "5").
    Before the snapshot arrives (or when it fails) the position is
    undefined, so every card lands on fallback 3 — never the old "DE". */
function tripInitials(snap: Position | undefined, id: string | null): string {
  if (snap) {
    if (snap.origin && snap.dest) {
      return (snap.origin[0] + snap.dest[0]).toUpperCase();
    }
    // Same optional-chain/trim defense as tripIdentity — a missing or
    // blank trip_label degrades to the fallbacks below, never throws.
    const label = snap.trip_label?.trim();
    if (label) {
      const initials = label
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0])
        .join("")
        .toUpperCase();
      if (initials) return initials;
    }
  }
  if (id) {
    const m = id.match(/pos-?(\d+)/i);
    if (m) return m[1];
  }
  return "··";
}

/** Plain-language trip line built ONLY from snapshot parts that exist —
    e.g. "Regional sales run · SIN → NRT · 2 people" ("1 person" when
    pax is 1). Missing parts are skipped, never guessed. With no snapshot
    (or an unknown position id) it degrades to the "Trip N" fallback. */
function tripIdentity(snap: Position | undefined, id: string | null): string {
  const parts: string[] = [];
  if (snap) {
    const label = snap.trip_label?.trim();
    if (label) parts.push(label);
    if (snap.origin && snap.dest) parts.push(`${snap.origin} → ${snap.dest}`);
    if (typeof snap.pax === "number" && snap.pax >= 1) {
      parts.push(snap.pax === 1 ? "1 person" : `${snap.pax} people`);
    }
  }
  return parts.length > 0 ? parts.join(" · ") : friendlyTrip(id);
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
      return { title: "Booked", body: "Price looked good." };
    case "hold call":
      return { title: "On hold", body: "Waiting for a better price." };
    case "escalate call":
      return { title: "Needs your OK", body: "Price moved — asking you first." };
    case "stale mark — disclosed":
      return { title: "Price held", body: "Using last known price." };
    case "marked to market":
      return { title: "Fare updated", body: "Checked against today's prices." };
    default:
      return { title: k, body: "" };
  }
}

/** Real spent-vs-budget figures from the snapshot's budgets[] — the same
    summing the summary page does: budget = allocated summed across every
    period, spent = spent summed the same way. Returns null when there are
    no budgets or any figure isn't a finite number — the bar then renders
    honestly empty and shows no note, never a guessed figure. */
function extractBudgetFigures(
  budgets: { allocated: string; spent: string }[] | undefined
): { spent: number; budget: number } | null {
  if (!budgets || budgets.length === 0) return null;
  const budget = budgets.reduce((sum, b) => sum + Number(b.allocated), 0);
  const spent = budgets.reduce((sum, b) => sum + Number(b.spent), 0);
  if (!Number.isFinite(budget) || budget <= 0 || !Number.isFinite(spent)) {
    return null;
  }
  return { spent, budget };
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

  // ---------- snapshot positions (real trip identity) ----------------------
  // The SSE stream only carries position ids, so cards would otherwise read
  // "Trip N". The desk snapshot holds the real identity (trip_label, route,
  // pax). Kept in SEPARATE state keyed by position id — it is desk-level
  // data, so the replay wipe (dispatch({kind:"wipe"})) never touches it.
  // Fetched once on mount, and again once the terminal result lands so
  // late-changing fields are current. Failures are swallowed silently:
  // the render simply falls back to "Trip N" and never invents anything.
  const [positions, setPositions] = useState<Record<string, Position>>({});
  // The budget bar's spent-vs-budget figures, summed from the SAME
  // snapshot's budgets[] (pattern mirrored from the summary page). Kept in
  // this separate, wipe-surviving state — never in the SSE reducer — so it
  // refetches on mount and after the terminal result, exactly like the
  // positions above. Stays null when the snapshot is unreachable or the
  // figures don't parse: the bar then renders empty with no note.
  const [budgetFigures, setBudgetFigures] = useState<{
    spent: number;
    budget: number;
  } | null>(null);
  // Shared request-sequence guard for BOTH snapshot fetches below: each
  // fetch stamps a monotonically increasing seq before awaiting, and a
  // late response is dropped unless it is still the newest — an out-of-order
  // mount fetch can never overwrite the post-result fetch's data.
  const snapSeq = useRef(0);

  useEffect(() => {
    // Fresh desk — drop the previous desk's snapshot state up front so the
    // budget bar/note and trip identities never leak across desks while the
    // new snapshot loads. (filledFiguresRef is declared further down with
    // the bar tween; effect bodies run after render, so it's initialized.)
    setPositions({});
    setBudgetFigures(null);
    filledFiguresRef.current = null;
    let cancel = false;
    const seq = ++snapSeq.current;
    getDeskSnapshot(deskId)
      .then((snap) => {
        if (cancel || seq !== snapSeq.current) return;
        setPositions((prev) => ({
          ...prev,
          ...Object.fromEntries(snap.positions.map((p) => [p.id, p])),
        }));
        setBudgetFigures(extractBudgetFigures(snap.budgets));
      })
      .catch(() => {
        // silent — cards keep the "Trip N" fallback, the bar stays empty
      });
    return () => {
      cancel = true;
    };
  }, [deskId]);

  // Post-result refetch: watches the reducer's `result` (set by the
  // terminal result event); the reducer/result handling itself is untouched.
  useEffect(() => {
    if (!screen.result) return;
    let cancel = false;
    const seq = ++snapSeq.current;
    getDeskSnapshot(deskId)
      .then((snap) => {
        if (cancel || seq !== snapSeq.current) return;
        setPositions((prev) => ({
          ...prev,
          ...Object.fromEntries(snap.positions.map((p) => [p.id, p])),
        }));
        setBudgetFigures(extractBudgetFigures(snap.budgets));
      })
      .catch(() => {
        // silent — the on-mount copy (or "Trip N") stays on screen
      });
    return () => {
      cancel = true;
    };
  }, [deskId, screen.result]);

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

  // Budget-bar fill — scaleX only, mirrored from the summary page. Base CSS
  // keeps the fill COLLAPSED (scaleX 0) before any JS, so it can never
  // flash full-width or overstate spend. When real figures arrive the fill
  // tweens to the real ratio; a zero ratio is SET (never tweened — a
  // 0 -> 0 tween could leave the element unstyled), so a zero-spend desk
  // always shows an honestly empty bar.
  const barFillRef = useRef<HTMLDivElement>(null);
  const filledFiguresRef = useRef<{ spent: number; budget: number } | null>(
    null
  );
  useGSAP(
    () => {
      const fillEl = barFillRef.current;
      if (!fillEl) return;
      if (!budgetFigures) {
        // No real figures (snapshot missing/failed) — stay collapsed.
        gsap.set(fillEl, { scaleX: 0, transformOrigin: "left center" });
        filledFiguresRef.current = null;
        return;
      }
      const ratio = Math.min(
        1,
        Math.max(0, budgetFigures.spent / budgetFigures.budget)
      );
      const already = filledFiguresRef.current;
      if (already && already.spent === budgetFigures.spent && already.budget === budgetFigures.budget) {
        // Same figures re-running — just force the settled state.
        gsap.set(fillEl, { scaleX: ratio, transformOrigin: "left center" });
        return;
      }
      filledFiguresRef.current = budgetFigures;
      gsap.matchMedia().add(
        { reduceMotion: "(prefers-reduced-motion: reduce)" },
        (ctx) => {
          if (ctx.conditions?.reduceMotion || ratio === 0) {
            // Zero ratio or reduced motion: SET collapsed/exact — the fill
            // can never overstate spend.
            gsap.set(fillEl, { scaleX: ratio, transformOrigin: "left center" });
          } else {
            gsap.fromTo(
              fillEl,
              { scaleX: 0, transformOrigin: "left center" },
              {
                scaleX: ratio,
                transformOrigin: "left center",
                duration: 0.7,
                ease: "power2.out",
                // belt-and-braces: land EXACTLY on the real ratio
                onComplete: () => {
                  gsap.set(fillEl, {
                    scaleX: ratio,
                    transformOrigin: "left center",
                  });
                },
              }
            );
          }
        }
      );
    },
    { scope: scopeRef, dependencies: [budgetFigures] }
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
  // Booked = snapshot-confirmed ONLY (positions are the source of truth):
  // a book JUDGMENT fires before the execute wall, so blotter trade events
  // never count as booked — status "booked" AND ticket_asserted must hold.
  const bookedCount = Object.values(positions).filter(
    (p) => p.status === "booked" && p.ticket_asserted
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
    // Real trip identity from the snapshot — undefined before it arrives or
    // when the id isn't in it, in which case both helpers degrade cleanly.
    const snapPos = pos ? positions[pos] : undefined;
    const blocked = event.type === "loss" || event.type === "error";
    const avatarCls = `avatar a${(ix % 4) + 2}`;

    let badge: ReactNode = null;
    let amount: ReactNode = null;
    let extra: ReactNode = null;

    switch (event.type) {
      case "trade":
        badge =
          event.kind === "book" ? (
            snapPos && snapPos.status === "booked" ? (
              <span className="badge ok">Booked</span>
            ) : (
              <span className="badge plain">Book decision logged</span>
            )
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
        badge = <span className="badge no">Dropped in value</span>;
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
        <div className={avatarCls}>{tripInitials(snapPos, pos)}</div>
        <div className="info">
          <div className="name">{tripTitle(event)}</div>
          <div className="leg">
            {tripIdentity(snapPos, pos)}
            {" · "}
            {event.type === "trade"
              ? event.kind === "book"
                ? "Price looked good"
                : event.kind === "hold"
                  ? "Waiting for a better fare"
                  : "Asking you first"
              : event.type === "mark"
                ? event.stale
                  ? "Using last known price"
                  : "Checked today's fares"
                : event.type === "alloc"
                  ? "Came in under quote"
                  : event.type === "loss"
                    ? `Down ${money(event.amount, currency).replace("−", "")}`
                    : event.type === "reconcile"
                      ? "Adjusted before booking"
                      : "See full record"}
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
                <span className="chip loss">price drop</span>
                {posEl}
                <span className="num">{money(event.amount, currency)}</span>
              </div>
              <div className="b-body">{event.note}</div>
              <div className="b-disc">{event.disclosure}</div>
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
                  {event.stale ? "price held" : "fare check"}
                </span>
                {posEl}
                <span className="num">
                  {money(event.old, currency)} → {money(event.new, currency)}
                </span>
              </div>
              <div className="b-body">
                {event.stale
                  ? "Kept last known price — couldn't get a fresh one"
                  : event.search_ref
                    ? `Fare lookup #${event.search_ref} · check ${event.meter_used}`
                    : `Check ${event.meter_used}`}
              </div>
              {event.disclosure && (
                <div className="b-disc">{event.disclosure}</div>
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
                <span className="chip escalate">approval needed</span>
                {posEl}
                {decision.state === "chosen" && (
                  <span className="chip alloc">done</span>
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
                <span className="chip reconcile">adjusted</span>
                {posEl}
                <span className="num">{money(event.delta, currency)}</span>
              </div>
              <div className="b-body">
                Price changed — {event.resolution === "absorb" ? "absorbed" : "re-quoted"}
              </div>
              <div className="b-disc">{event.disclosure}</div>
            </div>
          </div>
        );
      case "alloc":
        return (
          <div key={ix} className="brow good">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip alloc">savings</span>
                {posEl}
                <span className="num">{money(event.amount, currency)}</span>
              </div>
              <div className="b-body">
                Saved {money(event.amount, currency)} · seat{" "}
                <b>{event.seat_ref}</b>
              </div>
              <div className="b-disc">{event.disclosure}</div>
            </div>
          </div>
        );
      case "error":
        return (
          <div key={ix} className="brow bad">
            {ixEl}
            <div className="b-main">
              <div className="b-head">
                <span className="chip error">issue</span>
                {posEl}
                <span className="num">{event.code}</span>
              </div>
              <div className="b-disc">
                Error logged — details kept server-side
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
        <div className="decide-name">
          {tripIdentity(positions[event.position_id], event.position_id)}
        </div>
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
        <h1 className="run-title">Booking your team's trips</h1>
        <div className="run-who">
          <span className="run-id">{deskId}</span>
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
        {/* Spent-vs-budget, real figures only: both numbers are summed
            from the snapshot's budgets[] (same pattern as the summary
            page). The fill starts collapsed in base CSS and gsap settles
            it at the real ratio (a 0 ratio stays honestly empty). With no
            figures the bar renders empty and the note is dropped entirely
            — never a guess. */}
        <div className="bar">
          <div ref={barFillRef} className="fill" />
        </div>
        {budgetFigures && (
          <div className="bar-note num">
            spent {money(String(budgetFigures.spent), currency)} of{" "}
            {money(String(budgetFigures.budget), currency)} ·{" "}
            {money(String(budgetFigures.budget - budgetFigures.spent), currency)}{" "}
            left
          </div>
        )}

        <div className="statusline">
          <span className="s">
            <span className="pin g" />
            <b>{bookedCount}</b> booked
          </span>
          {openEscRows.length > 0 && (
            <span className="s">
              <span className="pin w" />
              <b>{openEscRows.length}</b> need{openEscRows.length === 1 ? "s" : ""}{" "}
              your OK
            </span>
          )}
          {lossCount > 0 && (
            <span className="s">
              <span className="pin r" />
              <b>{lossCount}</b> price drop{lossCount === 1 ? "" : "s"}
            </span>
          )}
          {recCount > 0 && (
            <span className="s">
              <span className="pin r" />
              <b>{recCount}</b> price adjustment{recCount === 1 ? "" : "s"}
            </span>
          )}
          {errCount > 0 && (
            <span className="s">
              <span className="pin r" />
              <b>{errCount}</b> issue{errCount === 1 ? "" : "s"}
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

        {/* per-rail provenance strip (S12, ADR 0006): demoted into
            the full record — ops manager doesn't need this on main view */}
        {screen.rails && (
          <div className="rails">
            <div className="rails-note">
              Data sources for this run
            </div>
            {screen.rails.map((rail) => (
              <div key={rail.rail} className="rail">
                <span className="rail-name">{rail.rail}</span>
                <span className={`rail-state ${rail.state}`}>{rail.label}</span>
                <span className="rail-detail">{rail.detail}</span>
              </div>
            ))}
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
                  Nothing here yet — entries appear as trips are processed.
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
          </div>
          <Link className="cta" href={`/close/${deskId}`}>
            See summary →
          </Link>
        </div>
      )}

      {/* ---- crash path: terminal DESK_CYCLE_FAILED, no result emitted -- */}
      {screen.cycleFailed && !screen.result && (
        <div className="result-banner">
          <div>
            <div className="rb-k">Stopped early</div>
            <div className="rb-sub">
              Something went wrong — nothing was booked past that point.
            </div>
          </div>
        </div>
      )}

      <div className="note-soft">
        Always within budget. Every fare is real.
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
