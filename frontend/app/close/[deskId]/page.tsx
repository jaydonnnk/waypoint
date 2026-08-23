"use client";

// Screen 3 — the weekly close.
// GET /api/desk/{id}/close, branching on result.status — NEVER on the HTTP
// code alone: 200 carries DeskResult for every logical outcome
// (closed / escalated / budget_exhausted / failed), 504 = still running
// (retry), 500 = the cycle CRASHED (no result exists — disclose honestly).

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getDeskClose, getDeskSnapshot, type CloseOutcome } from "@/lib/api";
import { signedMoney } from "@/lib/format";
import type { DeskResult, DeskStatus } from "@/lib/types";

type Phase =
  | { kind: "waiting" }
  | { kind: "outcome"; outcome: CloseOutcome };

const STATUS_COPY: Record<DeskStatus, { call: string; cls: string; sub: string }> = {
  closed: {
    call: "Weekly close — settled",
    cls: "good",
    sub: "the cycle ran to completion; P&L was computed in code, not asserted",
  },
  escalated: {
    call: "Closed on an escalation",
    cls: "warn",
    sub: "the cycle ended waiting on the one human click — nothing executed past it",
  },
  budget_exhausted: {
    call: "Budget exhausted",
    cls: "bad",
    sub: "the desk stopped at the budget line — budget is never waived, even by a human click",
  },
  failed: {
    call: "Cycle failed — disclosed",
    cls: "bad",
    sub: "the desk gave up honestly instead of guessing; the blotter shows where it stopped",
  },
};

export default function ClosePage() {
  const params = useParams<{ deskId: string }>();
  const deskId = params.deskId;

  const [phase, setPhase] = useState<Phase>({ kind: "waiting" });
  const [attempt, setAttempt] = useState(0);
  // DeskResult carries no currency; read it from the mandate snapshot
  // (existing endpoint). Stays undefined if the snapshot is unreachable —
  // amounts then render without a symbol rather than a guessed one.
  const [currency, setCurrency] = useState<string | undefined>(undefined);

  const load = useCallback(() => {
    setPhase({ kind: "waiting" });
    getDeskClose(deskId).then((outcome) => {
      setPhase({ kind: "outcome", outcome });
    });
  }, [deskId]);

  useEffect(() => {
    load();
  }, [load, attempt]);

  useEffect(() => {
    let cancelled = false;
    getDeskSnapshot(deskId)
      .then((snap) => {
        if (!cancelled) setCurrency(snap.mandate.currency);
      })
      .catch(() => {
        /* snapshot is cosmetic here — the close result stands without it */
      });
    return () => {
      cancelled = true;
    };
  }, [deskId]);

  // ---------- waiting -----------------------------------------------------
  if (phase.kind === "waiting") {
    return (
      <main className="wrap">
        <div className="brand">
          WAYPOINT<span className="tick">.</span>
        </div>
        <div className="sub">weekly close · {deskId}</div>
        <div className="card close-status-card">
          <p className="close-call">Settling the week…</p>
          <p className="close-sub">
            the close endpoint waits with the desk until the cycle finishes
            (bounded to 60 seconds per attempt)
          </p>
        </div>
      </main>
    );
  }

  const { outcome } = phase;

  // ---------- 504: still running (retry) -----------------------------------
  if (outcome.kind === "still_running") {
    return (
      <main className="wrap">
        <div className="brand">
          WAYPOINT<span className="tick">.</span>
        </div>
        <div className="sub">weekly close · {deskId}</div>
        <div className="card close-status-card">
          <p className="close-call warn">Cycle still running</p>
          <p className="close-sub">
            the desk hasn&apos;t finished in this 60-second wait — it is not
            failed, just busy
          </p>
          <button className="cta" onClick={() => setAttempt((a) => a + 1)}>
            Wait again →
          </button>
          <Link className="cta ghost" href={`/desk/${deskId}`}>
            Back to the live desk
          </Link>
        </div>
      </main>
    );
  }

  // ---------- 500: crashed --------------------------------------------------
  if (outcome.kind === "crashed") {
    return (
      <main className="wrap">
        <div className="brand">
          WAYPOINT<span className="tick">.</span>
        </div>
        <div className="sub">weekly close · {deskId}</div>
        <div className="card close-status-card">
          <p className="close-call bad">The cycle crashed</p>
          <p className="close-sub">
            honest disclosure: the desk ended abnormally and produced no
            result. No P&L is shown because none exists — the server keeps
            the raw cause; nothing unverified is rendered here.
          </p>
          <Link className="cta ghost" href={`/desk/${deskId}`}>
            Back to the live desk
          </Link>
        </div>
      </main>
    );
  }

  // ---------- 404: desk not found (no retry — it won't appear) ---------------
  if (outcome.kind === "not_found") {
    return (
      <main className="wrap">
        <div className="brand">
          WAYPOINT<span className="tick">.</span>
        </div>
        <div className="sub">weekly close · {deskId}</div>
        <div className="card close-status-card">
          <p className="close-call bad">Desk not found</p>
          <p className="close-sub">
            the backend has no desk with this id — nothing to close, and
            retrying won&apos;t change that
          </p>
          <Link className="cta ghost" href="/">
            Seed a new desk →
          </Link>
        </div>
      </main>
    );
  }

  // ---------- unreachable ----------------------------------------------------
  if (outcome.kind === "unreachable") {
    return (
      <main className="wrap">
        <div className="brand">
          WAYPOINT<span className="tick">.</span>
        </div>
        <div className="sub">weekly close · {deskId}</div>
        <div className="card close-status-card">
          <p className="close-call bad">Couldn&apos;t read the close</p>
          <p className="close-sub">{outcome.detail}</p>
          <button className="cta" onClick={() => setAttempt((a) => a + 1)}>
            Retry
          </button>
        </div>
      </main>
    );
  }

  // ---------- 200: branch on result.status -----------------------------------
  const result: DeskResult = outcome.result;
  const report = outcome.report;
  const copy = STATUS_COPY[result.status] ?? STATUS_COPY.failed;
  const pnl = Number(result.pnl);
  const pnlCls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";

  return (
    <main className="wrap">
      <div className="brand">
        WAYPOINT<span className="tick">.</span>
      </div>
      <div className="sub">weekly close · {deskId}</div>

      <div className="card close-status-card">
        <p className={`close-call ${copy.cls}`}>{copy.call}</p>
        <p className="close-sub">{copy.sub}</p>

        <div className="close-stats">
          <div className="close-stat">
            <div className="cs-k">P&amp;L (mark-to-market)</div>
            <div className={`cs-v ${pnlCls}`}>
              {signedMoney(result.pnl, currency)}
            </div>
          </div>
          <div className="close-stat">
            <div className="cs-k">losses admitted</div>
            <div className="cs-v">{result.losses_admitted}</div>
          </div>
          <div className="close-stat">
            <div className="cs-k">steps used</div>
            <div className="cs-v">{result.step_count}</div>
          </div>
          {/* S7: breach count is computed in code server-side; render exactly
              what came back, nothing if the field is absent. */}
          {typeof report?.policy_breaches === "number" ? (
            <div className="close-stat">
              <div className="cs-k">policy-cap breaches</div>
              <div className="cs-v">{report.policy_breaches}</div>
            </div>
          ) : null}
        </div>

        {/* Mode line — unconditional: honesty register shows WHICH mode ran. */}
        {result.comparison_mode ? (
          <div className="close-note">
            comparison mode — decisions logged and marked, nothing ordered.
            The P&amp;L above is real mark-to-market math; no ticket was ever
            issued.
          </div>
        ) : (
          <div className="close-note live">
            live ticketing — write commands were executed. The blotter and
            ledger on the desk are the record of what was ordered.
          </div>
        )}

        {/* S7 risk-officer verdict slot: render the auditor line exactly as
            returned — labeled as a second-pass heuristic challenge, never an
            authority. Nothing renders if the line is absent. */}
        {typeof report?.auditor_line === "string" && report.auditor_line ? (
          <div className="close-room filled">
            <div className="auditor-k">
              second-pass risk officer review — a heuristic challenge, not an
              authority
            </div>
            <p className="auditor-line">{report.auditor_line}</p>
            {report.auditor_source === "deterministic-fallback" ? (
              <div className="auditor-src">
                disclosure: this line came from the deterministic fallback — no
                auditor agent ran
              </div>
            ) : null}
          </div>
        ) : (
          <div className="close-room" aria-hidden="true" />
        )}
      </div>

      <Link className="cta ghost" href="/">
        Seed another desk →
      </Link>
    </main>
  );
}
