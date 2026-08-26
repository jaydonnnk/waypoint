# 0006 — Per-rail provenance on the wire

## Status
Accepted — 2026-08-26

## Context
After S9 the desk has rails of mixed provenance that a single label cannot describe: the Atlas rail may be the live sandbox, a recorded replay (composite capture, no TICKETED envelope), or comparison-only; the Qwen rail may be a live model call or the deterministic prior-band fallback — while the priors are always curated and the ledger is always code-computed. The screen already carries ONE global mode banner, and that banner is true of whichever rail the reader happens to be looking at and false of the others — Orkestr's provenance board names this precisely: one global "live" label is the single most dangerous thing the interface could do, because a live badge on the judgment says nothing about the ticketing, and vice versa. The honesty hole ADR 0005 closed for the wire LABEL (recorded never wears "live") stays open at the SCREEN level whenever any two rails disagree.

## Decision
Provenance becomes per-rail, always, riding the wire as an ADDITIVE `rails` field on the meta event, built by one pure function:

- **`build_rails()` in `backend/app/provenance.py`** — pure: takes the rail inputs, returns four rows `{rail, state, label, detail}`. Order is fixed and meaningful: the two rails that CAN be live (Atlas, Qwen) first, the two that never can (priors, ledger) immediately after, so a reader never sees a live label without the non-live rows in the same glance.
- **The Atlas rail probes what S9 already puts on the wire**: the `mode_label` getattr-probe (ADR 0005 precedent) selects recorded; the two write gates' comparison decision selects comparison-only; only an explicitly signaled live-ticketing mode with a client present may say live sandbox. The recorded rail surfaces the manifest's honesty in its detail — composite capture, and whether a TICKETED envelope was ever genuinely captured.
- **The Qwen rail reads `DeskBrain.last_source`** — a one-line attribute `judge()` sets at every exit, carrying the auditor.py precedent wire values exactly (`agent` / `deterministic-fallback`). The execute wall's re-check is unaffected; this is narration about the narration gate, nothing more.
- **Fail-to-least-live is the default for every input.** Missing client, missing mode signal, or a brain that has not judged reads as the least-live label that rail has — never as a live claim ("cannot claim Atlas by omission"). `build_rails` called bare yields comparison-only / fallback / curated / real.
- **Additive only.** `mode` and `disclosures` stay byte-identical; the global banner and the disclosure register are untouched. Old replays carry no `rails` field and the frontend renders nothing for them — the strip exists only when the wire proves it.

## Consequences
- Mixed provenance is stated per rail on every screen that receives a meta event: a recorded Atlas rail can never be read as live because the Qwen fallback row sits beside it labeled fallback, and the priors/ledger rows state what never varies.
- The contract is testable as a pure matrix (mode × brain source) plus one absolute rule: recorded mode never emits an Atlas rail labelled live; the composite manifest's honesty appears verbatim in the recorded rail's detail.
- Determinism survives: rails derive only from cycle-start state (client identity, gate reads, no-judgment-yet brain), so two recorded cycles remain byte-identical (S9's gate re-asserts with the field present).
- Cost: one new pure module, one additive wire field, one additive UI strip. Supersedes nothing — 0005's wire-label honesty rule remains the floor; this ADR raises it to the whole screen.
