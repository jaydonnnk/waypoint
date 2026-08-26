# S12 — Per-rail provenance UI (spec + outcome)

Slice S12 of the Recorded-Mode Engine plan. Decision record: `docs/adr/0006-per-rail-provenance.md` (the plan named this 0007, but 0005 was recorded-mode and 0006 was the next free number at check time; S10's container ADR took 0007). Status: **DONE 2026-08-26** — gate 165 passed / 0 failed / 4 deselected (3 live + 1 eval).

After S9 the desk has rails of mixed provenance a single global label cannot describe. The global mode banner stays (zero camera-day risk); beside it, the meta event now carries an additive `rails` field and the desk screen renders a compact four-row rail strip — Atlas, Qwen, priors, ledger — each stating its own provenance. Philosophy adapted from Orkestr's provenance board (`src/ui/view/provenance.ts`, read-only): per-subsystem status, defaults fail to the least-live label ("cannot claim Atlas by omission"), mixed-provenance note, and the two rails that CAN be live ordered first so the non-live rows share the same glance. Code was adapted, not copied.

## Design (what changed)

- `backend/app/provenance.py` (new, pure) — `build_rails(atlas=, brain=, comparison=, live_ticketing=)` → four `{rail, state, label, detail}` rows in fixed order: Atlas and Qwen (the two that CAN be live), then priors and ledger (the two that never can). State vocabulary is closed and branched on, never parsed: Atlas `live`/`recorded`/`comparison`/`unknown`, Qwen `live`/`fallback`, priors `curated`, ledger `real`. Defaults are fail-closed (`comparison=True`, `live_ticketing=False`): a bare call reads comparison-only / fallback / curated / real — a caller cannot claim Atlas by omission.
  - **Atlas**: comparison takes priority (no write commands regardless of envelope source — same ordering as the loop's wire-label block); recorded via the S9 `mode_label` getattr-probe, with the manifest's honesty in the detail (composite capture; whether a TICKETED envelope was ever genuinely captured); a live claim needs BOTH the explicit live-ticketing signal AND a client present; anything else reads `not verified` — the least-live label.
  - **Qwen**: reads `DeskBrain.last_source` (below); no judgment on record reads fallback.
  - **priors** / **ledger**: curated — no ML / real — code-computed; they never vary.
- `backend/app/agent/brain.py` — `DeskBrain.last_source` (init `None`) set at every `judge()` exit, carrying the auditor.py precedent wire values exactly (`SOURCE_AGENT = "agent"` / `SOURCE_FALLBACK = "deterministic-fallback"`). Judgment-only; the execute wall is untouched.
- `backend/app/agent/loop.py` — the meta event gains the **additive** `rails` field built from `build_rails(self.atlas, self.brain, comparison, live_ticketing=not comparison and not recorded)`. `mode` and `disclosures` stay byte-identical (S9's assertions re-run untouched); live-mode desks get the live-sandbox / fallback-or-live rails through the same call.
- `frontend/app/desk/[deskId]/page.tsx` — **the global banner and the disclosure register are untouched**. Reducer: `rails: event.rails ?? null` on meta (absent → null → nothing renders; old replays unaffected). The four-row strip (name + state label + one-sentence detail + mixed-provenance note) renders directly below the header, only when `screen.rails` is present.
- `frontend/lib/types.ts` — additive `Rail` interface + optional `rails?: Rail[]` on the meta event.
- `frontend/app/globals.css` — token-only strip styles beside the mode-banner register; tones mirror it: `--good` only for `live`, `--warn` for recorded/fallback/comparison/unknown, quiet `--mut` for curated/real.

## Honesty contracts (tested)

- **Recorded never wears a live label anywhere** — the Atlas rail of a recorded cycle is asserted `"live" not in label` against both a stub and the REAL `RecordedAtlasClient` with the REAL manifest, across every comparison × brain-source combination.
- **Composite honesty surfaces in the Atlas rail detail** — with the real manifest (`composite=true`, `ticketed_captured=false`) the detail names both: "the capture is composite; no TICKETED envelope was ever captured…".
- **Fail-to-least-live for every input** — missing client, missing mode signal, bare call, brain-with-no-judgment all read the least-live label, never a live claim.
- **Meta rides before the first judgment** — the Qwen rail on the meta event reads fallback even when a live Qwen cycle will follow (least-live, never overclaimed); `last_source` flips to `agent` only after a valid live judgment.
- **Determinism survives** — rails derive only from cycle-start state; S9's two-cycle byte-identity gate re-passes with the field present.

## Test evidence

- `backend/tests/test_provenance.py` — pure matrix (live/recorded/comparison × qwen agent/fallback/none), fail-to-least-live set, recorded-never-live across all combinations (stub + real client), composite manifest honesty verbatim, `last_source` set at every judge exit (agent / transport-failure / hostile-shape / no-key), and two full-cycle loop wiring tests (recorded armed → recorded rails; disarmed → comparison-only rails).
- Gate: `cd backend; .venv\Scripts\python -m pytest` → **165 passed / 0 failed / 4 deselected** (baseline before S12: 136 passed).
- Boot smoke (recorded): `WAYPOINT_ATLAS_MODE=recorded` + armed gates, uvicorn boot, seed via the prewarm pattern — the buffered meta event carries all four rails with honest labels (recorded replay / deterministic fallback / curated / real).

## Frontend behavior

- Old replays (meta without `rails`) render nothing — the reducer keeps `null`; no strip, no empty card.
- The strip is display-only: it binds to the wire field and never to derived/guessed state; tone classes branch on the closed `state` vocabulary.
