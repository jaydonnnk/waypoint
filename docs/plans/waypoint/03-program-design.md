# Program Design: Waypoint

## Two gates (the core mental model)
- **Advise gate — open.** The agent (and UI) see *every* alternative, each labeled `allowed` ✅ / `blocked` ⛔ / `unknown` ⚠️ by the rules, with a reason + provenance. Qwen reasons over all of them and narrates why it rejected the cheap illegal/unknown ones.
- **Execute gate — walled, fail-closed.** Auto-book + auto-settle **only** offers where every rule is `allowed`. Any `blocked` or `unknown` → not auto-executed; requires explicit human override. Code enforces this line; the LLM cannot cross it.

This resolves "AI advises freely" + "rules are an absolute wall" — different gates, no contradiction.

## Files
```
backend/
  app/
    main.py              # FastAPI app, CORS, mounts routes + SSE
    api/routes.py        # endpoints (trips, disruptions, webhook, recovery, stream)
    models.py            # domain types — Passenger, Segment, Offer, Layover, ...
    agent/loop.py        # RecoveryAgent — orchestration, 3 guards, execute wall
    agent/judge.py       # RerouteJudge — Qwen, sees ALL offers, narrates, picks from allowed
    rules/base.py        # Rule protocol + RuleVerdict (3-state)
    rules/visa.py        # TransitVisaRule — reads curated[hub][nationality], fail-closed
    rules/passport.py    # PassportValidityRule
    rules/registry.py    # ordered active rules
    atlas/client.py      # AtlasClient — wraps forked skill library, maps to domain types
    data/loaders.py      # load transit_hubs.yaml, passport_index.csv, iata_country.csv
    db/schema.py         # SQLAlchemy tables
    db/store.py          # Store — typed persistence
  data/
    transit_hubs.yaml    # curated (hub x nationality) transit rules — see schema below
    passport_index.csv   # tourist-visa base matrix (entry fallback)
    iata_country.csv     # airport IATA -> ISO-2 country
  tests/
frontend/                # Next.js: 3 screens + SSE client (Gate 4)
```

## Curated data schema — `transit_hubs.yaml`
```yaml
SGN:                         # hub IATA
  country: VN
  has_airside_zone: true     # false => everyone changing terminals clears immigration
  nationalities:
    IN:                      # passport nationality (ISO-2)
      airside_ok: "no"       # yes | no | unknown
      max_hours: null        # airside allowed only under N hours (null = n/a)
      source: "https://... official transit rule"
      last_checked: 2026-08-20
# Lookup miss (hub absent, or nationality absent) => unknown => blocked from execute.
# Freshness: airside cell trusted <= 6 months since last_checked; entry-fallback cell <= 3 months.
# Past the window => treated as unknown => fail-closed (needs override). Everything curated today = fresh.
```

## Staleness & the honest re-read boundary
Two kinds of "re-read before write", kept honest:
- **Price / seat availability** — a REAL live re-read via Atlas `verify` right before booking. Genuine.
- **Visa / transit rules** — no live source exists, so we use the curated table + a **freshness window** (airside 6mo, entry-fallback 3mo) as an honest PROXY. Past the window → `unknown` → fail-closed.

The demo states this plainly — we never imply live visa verification we don't have. Showing a "checked 3 days ago" cell beside a deliberately-aged cell that flips to `unknown → ask override` is the guide's §29 "don't trust stale data" principle made visible = free Compliance & Safety points.

## Types & signatures (no bodies)
```python
# rules/base.py
class RuleVerdict(BaseModel):
    rule_name: str
    status: Literal["allowed", "blocked", "unknown"]     # 3-state, not bool
    reason: str
    source: str | None = None
    last_checked: date | None = None

class Rule(Protocol):
    name: str
    def check(self, offer: "Offer", pax: "Passenger") -> RuleVerdict: ...

# models.py
class Layover(BaseModel):
    airport: str; country: str; hours: float
    same_ticket: bool                 # SECONDARY hint only, never decisive

class Offer(BaseModel):
    id: str; atlas_offer_id: str
    price: Decimal; currency: str; total_minutes: int
    segments: list["Segment"]
    price_status: Literal["reference", "current", "verified"]; bookable: bool
    def layovers(self, iata: "IataCountryMap") -> list[Layover]: ...

class OfferAssessment(BaseModel):
    offer: Offer
    verdicts: list[RuleVerdict]
    executable: bool                  # True iff every verdict.status == "allowed"

# rules/visa.py
class TransitVisaRule:
    name = "transit_visa"
    def __init__(self, hubs: "HubTable", tourist: "PassportMatrix", iata: "IataCountryMap"): ...
    def check(self, offer, pax) -> RuleVerdict: ...
    # per layover: curated[hub][nationality]; has_airside_zone false -> needs entry
    # (fallback to tourist matrix); airside_ok yes & within max_hours -> allowed;
    # no -> blocked; missing -> unknown. same_ticket only softens messaging, never flips.

# agent/judge.py
class RankedDecision(BaseModel):
    chosen_offer_id: str              # MUST be an executable offer; code re-checks
    rationale: str                    # narrates rejected blocked/unknown options too
class RerouteJudge:
    def __init__(self, llm): ...
    def rank(self, assessments: list[OfferAssessment]) -> RankedDecision: ...
    # sees ALL assessments (advise gate); recommends the best EXECUTABLE one.

# agent/loop.py
class RecoveryResult(BaseModel):
    trip_id: str
    status: Literal["recovered", "no_legal_option", "needs_override", "failed"]
    chosen: Offer | None; rejected_cheapest: Offer | None
    order: "Order | None"; step_count: int; rationale: str | None
class RecoveryAgent:
    def __init__(self, atlas, rules: list[Rule], judge, store, step_budget: int = 12): ...
    async def run(self, trip_id: str, emit: Callable[[dict], None]) -> RecoveryResult: ...

# atlas/client.py
class AtlasClient:
    def search(self, origin, dest, dep: date, pax: int) -> list[Offer]: ...
    def verify(self, offer: Offer) -> Offer: ...
    def create_order(self, offer: Offer, pax: list[Passenger]) -> "OrderDraft": ...
    def pay(self, draft: "OrderDraft") -> "PaymentResult": ...        # sandbox auto-approve
    def get_order(self, order_no: str) -> "OrderStatus": ...          # outcome assertion
```

## Call stack (recovery, main path)
```
POST /api/disruptions  (or /api/webhooks/atlas)
  RecoveryAgent.run(trip_id, emit)
    Store.get_trip(trip_id)                        # GUARD: re-read world
    AtlasClient.search(broken_leg...) -> [Offer]                     # emit "found N"
    assessments = []
    for offer in offers:
      verdicts = [rule.check(offer, pax) for rule in rules]
      assessments.append(OfferAssessment(offer, verdicts, executable=all allowed))
      Store.save_verdicts(...)                                        # emit each label
    # ADVISE gate: judge sees everything
    RerouteJudge.rank(assessments) -> RankedDecision                  # Qwen; emit rationale
    chosen = lookup(decision.chosen_offer_id)
    # EXECUTE gate: fail-closed wall
    if not chosen.executable:  return needs_override                  # never auto-book blocked/unknown
    if no executable offers:   return no_legal_option                 # GUARD: give up
    AtlasClient.verify(chosen)                                        # GUARD: stale check; emit old/new
    draft = AtlasClient.create_order(chosen, pax)
    AtlasClient.pay(draft)                                            # auto-approve sandbox; emit "settled +$X"
    AtlasClient.get_order(order_no)                                   # GUARD: assert PNR+ticket; emit
    Store.record_decision(...) ; Store.record_order(...)
    return recovered
  (every step budget-counted; exceed -> give up + emit)
```

## Test plan (names → what each asserts)
Rules (3-state + fail-closed):
- `test_visa_blocked_when_airside_no` — curated `airside_ok:no` hub → status blocked, reason names country.
- `test_visa_allowed_when_airside_yes_within_hours` — curated yes + under `max_hours` → allowed.
- `test_visa_unknown_when_hub_not_curated` — hub absent from table → **unknown** (NOT allowed).
- `test_visa_same_ticket_does_not_flip_verdict` — ticket structure changes wording, never the status.
- `test_visa_cell_past_freshness_window_becomes_unknown` — airside cell older than 6mo (or fallback older than 3mo) → unknown (fail-closed), not allowed.
- `test_passport_validity_blocks_expiry_within_6_months` / `test_passport_validity_allows_valid`.
Execute wall + agent:
- `test_execute_wall_rejects_blocked_and_unknown` — chosen not-executable → status `needs_override`, no order created.
- `test_agent_picks_cheapest_EXECUTABLE_not_cheapest_overall` — **core**: cheapest is blocked → agent books cheapest *allowed*, records `rejected_cheapest`.
- `test_judge_sees_all_and_narrates_rejected` — rationale references the rejected blocked/unknown offers (advise gate open).
- `test_agent_gives_up_when_no_executable_option` — all blocked/unknown → `no_legal_option`.
- `test_agent_reverifies_before_booking` — `verify` called before `create_order`.
- `test_agent_asserts_ticket_before_success` — no ticket back → status ≠ recovered.
- `test_agent_respects_step_budget` — forced loop stops at budget.
- `test_offer_mapping_preserves_all_layover_airports` — Atlas fromSegments[3] → 3 layover countries.
Persistence:
- `test_recovery_persists_verdicts_and_decision`.

All tests must fail against pre-change code. Rules/agent use fixtures; one integration test hits sandbox once ticketing is active.

## Least confident decisions (challenge these)
1. **`has_airside_zone` + entry fallback.** When a hub has no airside zone, the passenger must clear immigration → we fall back to the tourist-entry matrix. That fallback's correctness for edge nationalities is the shakiest remaining bit. Fail-closed cushions it (unsure → unknown → blocked).
2. *(resolved)* Freshness window adopted: airside cell trusted ≤ 6 months, entry-fallback ≤ 3 months (shakier data distrusted faster); past window → unknown → fail-closed. It is an explicit PROXY for live re-read (no live transit-visa API exists), stated as such in the demo.
3. **Layover-hour thresholds** (`max_hours`) — hand-curated per hub/nationality; demo hubs only.
4. **Atlas datetime format** (doc typo) — confirm on first live order; drives `hours` + `total_minutes`.
5. **Webhook payload shape** — unknown until a real Atlas incident fires; injected path is the guaranteed demo trigger.
6. **Step budget = 12** — placeholder, tune once the loop runs.

## Demo choreography (build constraint)
Fail-closed rejects any uncurated hub. The scripted demo route MUST run through hubs we've curated in `transit_hubs.yaml`, and must contain BOTH:
- a **cheaper `airside_ok:no`** hub (the trap the agent catches), and
- a **pricier `airside_ok:yes`** hub (the legal reroute the agent picks).
That contrast is the demo. Pick + curate these hubs deliberately (candidates from live data: SGN=trap, ICN=legal) — don't leave it to whatever the sandbox returns.
