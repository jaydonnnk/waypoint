"use client";

// Pass 9 — the fare-movement chart ("Where every trip stands").
//
// Shared by Screen 2's full record and Screen 3's wrap-up so both screens
// tell the same story in the same visual language. Presentation only: it
// owns no state, fetches nothing, and every figure it prints is money()
// over a value the SNAPSHOT actually sent.
//
// HONESTY RULES ENCODED HERE:
//   * Both ends of each dumbbell are real fields (cost_basis, mark_price).
//   * The bar between them is GEOMETRY derived from those decimals, which
//     the brief allows. Its length is never printed as a figure, and the
//     gap (mark - basis) is never shown as text — that would be
//     client-side arithmetic presented as a system number.
//   * A loss figure appears only when a real `loss` event supplied one.
//   * The axis is ZERO-BASED, so a mark's distance from the left edge is
//     its real magnitude; nothing is rescaled per row.
//   * The authority cap is drawn on the SAME scale, which is what makes an
//     escalation self-explanatory: its mark is the one past the line.
//   * A stale mark is a hollow RING (shape), never a faded dot — measured
//     contrast rules out opacity encodings. See docs/design-research-pass9.md.

import type { ReactNode } from "react";

import { money } from "@/lib/format";
import type { Position } from "@/lib/types";

/** "…-pos-1" -> "Trip 1". Never invents a name; mirrors the desk page. */
function shortTrip(id: string): string {
  const m = id.match(/pos-?(\d+)/i);
  if (m) return `Trip ${m[1]}`;
  const clean = id.replace(/[^a-zA-Z0-9]/g, "");
  return clean.length > 8 ? `Trip ${clean.slice(-6)}` : id;
}

export type FareChartProps = {
  /** Snapshot positions, already in the order they should be drawn. */
  positions: Position[];
  currency?: string;
  /** mandate.authority_cap — omitted means no cap rule is drawn. */
  authorityCap?: string;
  /** position id -> that loss EVENT's own amount. Screen 3 has no stream,
      so it passes nothing and no loss figure is drawn (never a guess). */
  losses?: Record<string, string>;
  caption?: ReactNode;
};

export default function FareChart({
  positions,
  currency,
  authorityCap,
  losses,
  caption,
}: FareChartProps) {
  // Drop only what cannot be PLACED on a zero-based axis. A dropped row is
  // never lost to the reader — the raw record still carries every event.
  const rows: { pos: Position; basis: number; mark: number }[] = [];
  for (const p of positions) {
    const basis = Number(p.cost_basis);
    const mark = Number(p.mark_price);
    if (!Number.isFinite(basis) || !Number.isFinite(mark)) continue;
    if (basis < 0 || mark < 0) continue;
    rows.push({ pos: p, basis, mark });
  }

  const capNum = authorityCap === undefined ? NaN : Number(authorityCap);
  const capOk = Number.isFinite(capNum) && capNum > 0;

  let max = 0;
  for (const r of rows) max = Math.max(max, r.basis, r.mark);
  if (capOk) max = Math.max(max, capNum);
  max = max > 0 ? max * 1.08 : 0;

  if (rows.length === 0 || max <= 0) return null;

  const pct = (v: number) => Math.max(0, Math.min(100, (v / max) * 100));

  return (
    <figure className="rec-fig farechart">
      {caption && <figcaption className="rec-cap">{caption}</figcaption>}

      <div className="fm-rows">
        {rows.map(({ pos, basis, mark }) => {
          const lo = Math.min(basis, mark);
          const hi = Math.max(basis, mark);
          const loss = losses?.[pos.id];
          const over = capOk && mark > capNum;
          const name = pos.trip_label?.trim() || shortTrip(pos.id);
          return (
            <div className="fm-row" key={pos.id}>
              <div className="fm-id">
                <span className="fm-trip">{name}</span>
                {pos.origin && pos.dest && (
                  <span className="fm-route num">
                    {pos.origin} → {pos.dest}
                  </span>
                )}
              </div>

              <div className="fm-track" aria-hidden="true">
                {capOk && (
                  <i className="fm-cap" style={{ left: `${pct(capNum)}%` }} />
                )}
                <i
                  className="fm-bar"
                  style={{
                    left: `${pct(lo)}%`,
                    width: `${pct(hi) - pct(lo)}%`,
                  }}
                />
                <i
                  className="fm-dot fm-basis"
                  style={{ left: `${pct(basis)}%` }}
                />
                <i
                  className={
                    "fm-dot fm-mark" +
                    (pos.mark_stale ? " stale" : "") +
                    (over ? " over" : "")
                  }
                  style={{ left: `${pct(mark)}%` }}
                />
              </div>

              <div className="fm-read">
                <div className="fm-figs num" aria-hidden="true">
                  <span className="fm-was">
                    {money(pos.cost_basis, currency)}
                  </span>
                  <span className="fm-to">→</span>
                  <span className="fm-now">
                    {money(pos.mark_price, currency)}
                  </span>
                </div>
                <div className="fm-tags" aria-hidden="true">
                  {pos.mark_stale && (
                    <span className="fm-word wait">Last known price</span>
                  )}
                  {over && <span className="fm-word over">Over your limit</span>}
                  {loss && (
                    <span className="fm-word no num">
                      {money(loss, currency)}
                    </span>
                  )}
                </div>
              </div>

              {/* text equivalent — the same figures, for screen readers
                  and for print, where the geometry does not survive */}
              <span className="visually-hidden">
                {name}
                {pos.origin && pos.dest ? `, ${pos.origin} to ${pos.dest}` : ""}
                : booked at {money(pos.cost_basis, currency)}, latest mark{" "}
                {money(pos.mark_price, currency)}
                {pos.mark_stale ? " (last known price, not refreshed)" : ""}
                {over ? ", above your auto-approve limit" : ""}
                {loss ? `, loss admitted ${money(loss, currency)}` : ""}.
              </span>
            </div>
          );
        })}
      </div>

      <div className="fm-axis" aria-hidden="true">
        <span className="fm-axis-zero num">{money("0", currency)}</span>
        <div className="fm-axis-track">
          {capOk && authorityCap !== undefined && (
            <span
              className="fm-axis-cap num"
              style={{ left: `${pct(capNum)}%` }}
            >
              {money(authorityCap, currency)}
            </span>
          )}
        </div>
      </div>

      <div className="fm-legend" aria-hidden="true">
        <span className="fm-leg">
          <i className="fm-dot fm-basis static" /> booked at
        </span>
        <span className="fm-leg">
          <i className="fm-dot fm-mark static" /> prices now
        </span>
        <span className="fm-leg">
          <i className="fm-dot fm-mark stale static" /> last known price
        </span>
        {capOk && (
          <span className="fm-leg">
            <i className="fm-leg-rule" /> your limit
          </span>
        )}
      </div>
    </figure>
  );
}
