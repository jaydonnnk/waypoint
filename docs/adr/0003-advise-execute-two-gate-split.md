# 0003 — Split "advise" from "execute": open advice, fail-closed execution

## Status
Accepted — 2026-08-20

## Context
Two requirements looked contradictory: (a) the visa/passport rules must be an absolute wall the AI can never override, and (b) the AI should see and reason about *all* options, including risky ones, and explain its choices. Treating these as one gate forced a false choice between a rigid filter and open reasoning.

## Decision
Two distinct gates:
- **Advise gate — open.** Qwen and the UI see every alternative, each labeled `allowed` / `blocked` / `unknown` by the rules engine, with reason + provenance. The AI reasons over all of them and narrates why it rejects the cheap illegal/unknown options. This is where genuine judgment under uncertainty happens ("this hub is unknown, denial risk is real, a legal option is $40 more, given your tight connection I recommend X").
- **Execute gate — walled, fail-closed.** The agent auto-books + auto-settles **only** offers where every rule is `allowed`. `blocked` and `unknown` require an explicit human override; the LLM cannot select one for execution — code re-checks `executable` after the LLM picks.

## Consequences
- No contradiction: advice is soft and open, execution is hard and conservative.
- Safety: the agent can never autonomously book an option it isn't confident is legal (fail-closed). Wrong-but-safe (pricier legal flight) beats wrong-but-dangerous (denied boarding).
- Rubric: the visible reasoning-under-uncertainty in the advise gate is the real agentic judgment — strengthens the x2 case and avoids the x0.5 "AI is just a lookup" trap. The deterministic execute wall keeps AI out of the funds-settlement decision.
- The UI must render all three labels (✅/⛔/⚠️) and the AI's narration over the rejected ones — that contrast is the demo.
