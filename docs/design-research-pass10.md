# Design research — Pass 10: the craft pass

Person A / `person-a/ui-polish`. Written before any code was changed, alongside a
measured audit of the shipped build.

Pass 9 rebuilt *the full record*. This pass is about the other 80% of the screen:
the layout, the KPI band, the type, and the component craft. The brief supplied a
research briefing (its §5). My job was to extend it where it matters for this
product, and — the part that turned out to matter most — **to check the parts of
it I intended to lean on.** Two of them did not survive checking. Those
corrections are in §1.

Everything below is here because it changed a decision. Findings that changed
nothing are not here.

## Evidence grades

| Grade | Meaning |
| --- | --- |
| **[MEASURED]** | I computed it in this repo, from this palette or this running build. Reproducible; method stated. |
| **[LAW]** | Normative standard (WCAG). Conformance is testable. |
| **[SPEC]** | Published design-system documentation or vendor source file. Authoritative for that system. |
| **[EVIDENCE]** | Published study with a stated method. |
| **[CONVENTION]** | Practitioner craft. Defensible, unsourced or sourced only to an opinion piece. Treated as judgment, not proof. |

Where a source is a vendor's own token/source file rather than its prose doc site,
I say so — most design-system doc sites are client-rendered and return nothing to
a fetch, so the token files are the more reliable primary source.

---

## 1. Corrections to the brief's §5

The brief asked me to verify anything I leaned on. Two items did not hold, and one
is materially wrong in a way that changes an instruction in its own checklist.

### 1.1 The Primer "border-in-shadow" trick is not a Primer rule

The brief (§5.1) says Primer "merges border and shadow into one token by making
the first layer `0 0 0 1px` — so a floating surface never needs a separate
border."

What Primer actually ships: the ring appears **only in the floating tokens** —
`--shadow-floating-small: 0 0 0 1px #d1d9e040, 0 6px 12px -3px #25292e0a, 0 6px
18px 0 #25292e1f`. Every **resting** token carries no ring at all
(`--shadow-resting-small: 0 1px 1px 0 #1f23280a, 0 1px 2px 0 #1f232808`), and
Primer ships `--borderColor-default: #d1d9e0` for edges.
Source: <https://primer.style/foundations/primitives/color> **[SPEC]**

Primer *does* tokenise border-as-inset-shadow separately — `--boxShadow-thin:
inset 0 0 0 0.0625rem`. Source: <https://primer.style/foundations/primitives/size>

Two other systems handle the same problem differently and are worth copying from:
- **Polaris** draws a card edge as an inset **bevel**, not a border, and the four
  sides are not equal: `1px 0 0 0 rgba(0,0,0,.13) inset` on the sides, `0 -1px 0 0
  rgba(0,0,0,.17) inset` on the bottom, `0 1px 0 0 rgba(204,204,204,.5) inset` on
  the top. The bottom edge is darker than the sides and the top is a light
  highlight — a lit-from-above object, not a stroked rectangle.
  Source: Polaris `polaris-tokens/src/themes/base/shadow.ts` **[SPEC]**
- **Atlassian** makes the border the default and reserves shadow for "where a
  border might be easily missed, such as in very small UI or tables that use
  borders". Its raised token is two layers, one offset and one 0-offset ring:
  `0px 1px 1px #091E4240, 0px 0px 1px #091E424F`.
  Source: <https://atlassian.design/foundations/elevation> **[SPEC]**

**Decision.** I kept the ring-as-first-layer pattern, because it genuinely removes
a whole class of "is this bordered or shadowed?" inconsistency in a file two people
edit — but the elevation scale is documented as **Polaris-derived, not Primer's**,
and the ring is on the *resting* levels deliberately, which is my choice and not
anyone's published rule. I also took Polaris's asymmetry: our shadows are darker
below than at the sides. See §5.

### 1.2 "Use box-shadow for focus rings, not outline" is obsolete

The brief states this twice (§5.3, §7.3 checklist) with the rationale that
"box-shadow respects `border-radius`".

That rationale expired in 2023. WebKit: *"Now in Safari 16.4, `outline` always
follows the curve of `border-radius`."*
Source: <https://webkit.org/blog/13966/webkit-features-in-safari-16-4/> **[SPEC]**
Baseline widely available March 2023. Safari was the last holdout; the advice
predates the fix, and every major system now ships `outline`-based rings —
Carbon `outline: 2px solid $focus; outline-offset: -2px`; Primer the same; M3 a
3px ring at 2px outward offset; Polaris 2px solid at 1px offset. (Sources: those
projects' `_focus-outline.scss`, `misc.scss`, `_md-comp-focus-ring.scss`,
`Button.module.css`.) **[SPEC]**

**Decision, and why I still used box-shadow.** I followed the brief's instruction,
but for a different and actually-true reason, and I have written that reason into
the stylesheet so nobody re-derives the dead one. Waypoint puts interactive
elements on **light cards and on a near-black record surface**, and `--brand`
measures **5.47:1 on white but only 2.86:1 on the raised dark surface** — it fails
the 3:1 focus requirement there. `box-shadow` composes a **two-tone ring** from one
token:

```css
--focus-ring: 0 0 0 2px var(--card), 0 0 0 4px var(--brand);
```

On a light surface the brand ring reads against the page (5.47:1); on the dark
record the white inner ring reads against the surface (**17.33:1** measured). One
token, both grounds, and it satisfies SC 2.4.13's 2px-perimeter geometry. `outline`
cannot do two colours. That is the real argument, and it is not the one the brief
gives. **[MEASURED + LAW]**

### 1.3 Confirmed as stated

I re-derived the brief's WCAG formula against the palette and reproduce Pass 9's
table exactly (§4 below), so both are trustworthy. Cleveland & McGill's rank-3 tie,
the 24/44px target sizes, and the M3/Carbon duration tokens all check out against
primary sources (`material-tokens/json/motion.json` gives short1–4 =
50/100/150/200ms; Carbon's `motion.json` gives fast-01 70ms, fast-02 110ms,
moderate-01 150ms). **[SPEC]**

One item I could **not** verify and therefore did not use: Carbon's
24/32/40/48/64px data-table row scale and its "type size never changes with
density" rule (brief §5.1). Both of my attempts to reach a primary source failed.
I designed row density by judgment instead and have marked it as such.

---

## 2. How agent run views earn trust

### 2.1 Progress chatter and the permanent record are different object types

Linear's Agent Activity model has five types — `thought`, `action`, `elicitation`,
`response`, `error` — and **only `thought` and `action` may be marked ephemeral**:
*"Ephemeral activities are displayed temporarily, and will be replaced when the
next activity arrives from the agent."*
Source: <https://linear.app/developers/agent-interaction> (official developer docs)
**[SPEC]**

This is the cleanest statement of a distinction Waypoint already half-implements:
`step` events are narration that may be overwritten, `trade`/`mark`/`loss`/
`escalate` are record entries that must accrete.

**Decision.** The live narration line becomes visibly a *different kind of object*
from the record — one slim strip, one line at a time, replaced in place, with the
count of prior steps stated beside it so nothing looks lost. The record keeps
every row. This is why the working line is **not** styled as a card: it is not an
artefact, it is a status.

### 2.2 "Still working" is a state machine, not a spinner

Linear ships explicit session states — `pending | active | error | awaitingInput |
complete | stale` — "these will be visible to users", with a first response
required within 10s and a recoverable `stale` state after silence.
Sources: <https://linear.app/developers/agent-interaction>,
<https://linear.app/developers/agent-best-practices> **[SPEC]**

**Decision.** Waypoint already has `connected` / `streamDead` / `settled` /
`cycleFailed`. What it lacked was a *visible vocabulary* for them. Each now renders
as a named state with its own tone, and the "Connecting…" state no longer looks
identical to the working state. A stalled agent should look stalled — so I did
**not** add any perpetual spinner or pulse to a state that isn't producing events.

### 2.3 Explanations increase acceptance regardless of correctness

Bansal et al., CHI 2021: *"explanations increased the chance that humans will
accept the AI's recommendation, regardless of its correctness."* Zhang, Liao &
Bellamy find confidence scores help calibrate trust but that "trust calibration
alone is not sufficient", and highlight problems with local explanation.
Sources:
<https://www.microsoft.com/en-us/research/publication/does-the-whole-exceed-its-parts-the-effect-of-ai-explanations-on-complementary-team-performance/>,
<https://arxiv.org/abs/2001.02114> **[EVIDENCE]**

This is the sharpest warning in the whole pass, and it points the same way as the
brief's §5.8.2 ("polished, realistic presentation raises trust without improving
its appropriateness").

**Decision.** The decision card leads with a **checkable comparison**, not with
prose: the fare, the cap, and the fact that one exceeds the other, all three being
real values the manager can verify against their own mandate. Persuasive rationale
text stays, but it is demoted below the arithmetic rather than being the headline.
I also declined to add any "confidence" indicator — the backend emits none, and
inventing one is exactly the failure mode this research describes.

### 2.4 Reasoning traces are a secondary disclosure by design

Anthropic's own API docs: *"what you see is never the raw chain of thought: the
text in a thinking block is a summary"*, and `display: "omitted"` is the default on
the newest models.
Source: <https://platform.claude.com/docs/en/build-with-claude/thinking> **[SPEC]**

**Decision.** Validates keeping the full record behind a disclosure control rather
than on the page by default — but note the brief's §5.7 caveat that hiding
*consequence* destroys trust while hiding *detail* is fine. The record holds
detail; the dry-run status, the cap, and the escalation are consequences and stay
uncollapsed on every screen.

### 2.5 Collapse by grouping, with the outcome on the group

Temporal's timeline collapses three raw events (`ActivityTaskScheduled`,
`...Started`, `...Completed`) into **one row spanning the activity's duration**,
colours the span by outcome, and shows retries as *an attempt count on that row*
rather than as new rows.
Source: <https://temporal.io/blog/lets-visualize-a-workflow> (vendor engineering
post describing the shipped UI) **[SPEC-ish; vendor, not a spec]**

**Decision.** This is exactly the trip card's "N updates" toggle, and it told me
the toggle was right but under-labelled. The card footer now carries the group's
**outcome**, not just its count.

---

## 3. Money that has not moved

Waypoint's whole demo is money that will never be spent, and the current UI says
so only in words. These are the documented patterns.

### 3.1 Three buckets, not two

Stripe: *"Your balance includes funds that are available, pending, and any reserved
funds, if applicable."*
Source: <https://docs.stripe.com/reports/balance> **[SPEC]**

Stripe also names the exact confusion to design against: *"Card statements from
some issuers… don't always distinguish between authorisations and captured
(settled) payments, which can sometimes confuse customers."*
Source: <https://docs.stripe.com/payments/place-a-hold-on-a-payment-method>

Ramp: *"Pending transactions count against your card limit"*, and travel vendors'
pending charges "can take up to 30 days".
Source: <https://support.ramp.com/pending-charges-faqs> **[SPEC]**

**Decision — and the limit I hit.** The right design is a budget meter reading
*spent / held / remaining*. Waypoint's snapshot supports only two of those:
`budgets[].allocated` and `budgets[].spent`. There is no held total on the wire,
and summing the `cost_basis` of held positions to invent one would be client-side
arithmetic presented as a system figure — forbidden, and rightly. So I took the
**vocabulary** and not the third bucket: the budget tile now names the state of the
money ("Nothing committed yet") instead of printing a computed remainder, and the
trips carry Stripe/Ramp's own status words. Recorded here so a future pass that
adds a held total on the wire knows the design is waiting for it.

### 3.2 A non-colour glyph for degree of completion

Polaris's Badge ships a `progress` prop with exactly three values —
`incomplete | partiallyComplete | complete` — each rendered as its own pip, where
the pip is an **8px square at 3px radius with a 1.25px border**, not a circle.
Sources: `polaris-react/src/components/Badge/Badge.module.css`,
`.../Pip/Pip.module.css`; component docs
<https://shopify.dev/docs/api/app-home/web-components/feedback-and-status-indicators/badge>
**[SPEC]**

**This is the single most useful finding in the pass**, because of §4.2 below: our
status hues are *mathematically incapable* of carrying status on a light ground.
A three-state pip shape carries it instead, and it is a shipped pattern rather than
something I invented.

**Decision.** Every status badge gets a pip whose **shape** encodes the state —
hollow ring (nothing settled), half-filled (decided, not executed), solid
(snapshot-confirmed) — plus the word, plus the hue. Three redundant encodings, two
of which survive greyscale.

### 3.3 Simulation is an account-level identity plus one banner

Stripe: sandboxes "simulate creating real objects without affecting actual
transactions or moving real money"; the mode is an **account-level switch** with a
Dashboard notification box, not a per-row warning.
Source: <https://docs.stripe.com/test-mode> **[SPEC]**

GOV.UK's phase banner is the documented component for exactly this shape of
persistent service-level message: it sits *"in the `<header>` directly after the
header component"*, and *"Phase banners are shown across all pages of a service, so
users should understand it as a service-level message."*
Source: <https://design-system.service.gov.uk/components/phase-banner/> **[SPEC]**

**Decision.** The dry-run disclosure is promoted from a 12.5px line floating in the
top-right corner into a **proper phase banner in the app bar**, present on every
screen, with a state word and a tone. It is more visible than before and it is
*one* place — no per-fare nagging, which the same research says is the wrong move.
The brief's hard rule ("the dry-run banner stays visible") is satisfied more
strongly than it was.

---

## 4. Colour — measured, and the finding that reshaped the pass

**Method** (identical to Pass 9 §6, so the two are comparable): WCAG relative
luminance `L = 0.2126R + 0.7152G + 0.0722B` over sRGB channels linearised by
`c/12.92` when `c ≤ 0.04045` else `((c+0.055)/1.055)^2.4`; contrast
`(L_light + 0.05)/(L_dark + 0.05)`. OKLab/OKLCH conversions use Ottosson's
published matrices. Deuteranope simulation is the Viénot (1999) linear-RGB matrix.
L\* is CIE lightness from the same luminance. Inputs are the `:root` tokens.
Script: `scratchpad/color.mjs` (not committed — this pass ships CSS, TSX and this
document). **[MEASURED]**

I re-derived Pass 9's table first as a check on both of us. **It reproduces
exactly** — `--good` 3.01 on paper, `--warn` 3.04, `--faint` 2.45, `--bad` 4.17,
`--pop` 2.69, and 6.02 / 5.95 / 7.40 / 4.34 / 6.72 on `#111`.

### 4.1 Three real AA failures in the shipped build

| Pair | Where it renders | Ratio | Needs |
| --- | --- | --- | --- |
| `--good` on `--goodbg` | `.badge.ok` — "Booked" on every trip card | **2.85** | 4.5 |
| `--warn` on `--warnbg` | `.badge.wait` — "Needs your OK" | **2.88** | 4.5 |
| `--bad` on `--badbg` | `.badge.no` — "Dropped in value" | **3.82** | 4.5 |
| `--good` on `--line` | the budget bar's fill against its own track | **2.54** | 3.0 (SC 1.4.11) |
| white @86% on the teal at `#12857B` | `.note-soft`, `.sec`, `.r-tag` on the ground | **3.76** | 4.5 |

These are not hypotheticals; they are the status chips a judge reads first.

### 4.2 The finding that changed the pass: at AA, our three status hues collapse to one lightness

I derived a darker step of each status hue in OKLCH, holding hue exactly (drift
≤1.5°, chroma reduced only where the colour left sRGB) and walking lightness down
until each cleared **4.8:1** on its own tint — a deliberate margin over the 4.5
threshold, since §5.2 of the brief is right that this is a threshold and not a
rounded value.

| Token | From | Derived | on tint | on `--paper` | on `--card` | **L\*** |
| --- | --- | --- | --- | --- | --- | --- |
| `--status-ok-text` | `#15A66A` | **`#007B49`** | 4.85 | 5.12 | 5.34 | 45.1 |
| `--status-wait-text` | `#C8811E` | **`#995D00`** | 4.86 | 5.13 | 5.36 | 45.1 |
| `--status-no-text` | `#D64550` | **`#C23140`** | 4.84 | 5.28 | 5.52 | 44.3 |

Look at the last column. **L\* 45.1, 45.1, 44.3.** Their mutual contrast is
1.00–1.03:1. Under the Viénot deuteranope matrix, wait and no resolve to `#8F9500`
and `#A1A900`.

In other words: *the moment you force three different hues to the same contrast
ratio against equally-light backgrounds, you have forced them to the same
lightness.* This is arithmetic, not a palette flaw — it would happen to any three
hues under the same constraint. Pass 9 found the base tokens converged (L\* 60.3
vs 60.0); at AA-compliant darkness they converge **completely**.

**Decisions this drove — the backbone of the pass:**

1. **Colour carries no status information anywhere on a light surface.** Not as a
   fallback, not as a hint. Every status is a **word** plus a **pip whose shape
   differs** (§3.2). The hue is the third, redundant encoding.
2. The greyscale test in the brief's §8.5 is therefore not a box to tick at the
   end — it was the design constraint from the start.
3. I explicitly considered and rejected spreading the three lightnesses apart
   (which would restore greyscale separability). Doing so forces one status below
   4.5:1 on its tint, and the only status you could afford to darken further is
   the one that needs least attention. Shape is strictly better: it costs no
   contrast at all.

### 4.3 The dark record surface, and why `#111` moves

The brief's §5.2 is right that `#121212`-not-`#000` is about **elevation
headroom**: on pure black you cannot render a recessed surface or a lighter
shadow. Pass 9 chose `#111` for measured contrast reasons and those reasons still
hold — but `#111` is a neutral with nothing above or below it.

I derived a dark ramp from `--ink` itself (same hue, chroma held near zero), which
keeps it inside the fixed palette as "a darker shade of an existing hue":

| Role | Value | white | `--faint` | `--good` | `--warn` | `--bad` |
| --- | --- | --- | --- | --- | --- | --- |
| `--surface-inverse` (base) | **`#141B24`** | 17.33 | 6.79 | 5.52 | 5.46 | 3.98 |
| `--surface-inverse-raised` | **`#1B2430`** *(= `--ink`)* | 15.65 | 6.13 | 4.99 | 4.93 | 3.60 |
| `--surface-inverse-high` | **`#232D3A`** | 13.93 | 5.45 | 4.44 | 4.39 | 3.20 |

Every ratio Pass 9 relied on survives, and the raised step **is `--ink` exactly** —
a pleasing accident that means the elevation ramp needs only two new values.
`--bad` still fails body contrast on dark (3.98 / 3.60), so Pass 9's rule stands
unchanged: **red is a border or a mark on dark, never body text.** For the cases
that genuinely need red *text* on dark I derived a lifted step,
**`--status-no-on-dark: #F05D65`** (4.81 on the raised surface), alongside
`#17A76B` and `#C9821F`. This follows M3's documented dark-theme rule that accents
get lighter, not darker (`primary` moves tone 40 → tone 80).

### 4.4 The teal ground cannot carry small body text

Measured from the **running build** by screenshotting the painted pixels rather
than reading the CSS, because the animated aurora layers composite with
`mix-blend-mode: screen` and genuinely change the ground:

- app bar ground, actually painted: `#11514B` → white @86% = **7.24:1** ✔
- gradient light stop `#12857B` → white @86% = **3.76:1** ✘, pure white = 4.50 (exactly at threshold)
- landing's brightest stop `#1A9A8C` → pure white = **3.47:1** ✘ even at full opacity

**Decision.** All body text on the ground goes to **pure white**, and the
`.teal-app` gradient's light stop is pulled back so that pure white clears 4.5:1 at
every painted point. Translucent white is kept only for genuinely decorative
strokes. Where a text run has to sit low on the gradient, it gets a surface instead
of a scrim — which is what turned the orphaned footer note into a real footer.

---

## 5. Elevation, radius, density — the values I chose

### 5.1 Elevation

Following Polaris on the two things it does that Primer does not: the shadow colour
is **never pure black** (ours is `--ink` #1B2430, the background's own hue family),
and the light source is directly above, so the **bottom edge is darker than the
sides**. Opacity rises with elevation — the brief flags Polaris and Comeau
disagreeing on direction; I picked Polaris and applied it consistently, which is
what the brief actually asks for.

M3 gives the level count and, usefully, the reservation: six levels, *"+4 and +5
are designated for interactive states like hovering or dragging"*.
Source: `material-web/docs/components/elevation.md` **[SPEC]**
So our scale is 0–3 for resting surfaces and 4 for lifted/dragged only.

Also from M3, and it corrected an assumption of mine: **Material publishes no
hover or pressed elevation for cards** — only resting (1dp elevated, 0dp
filled/outlined) and dragged (2dp / 8dp).
Source: `material-components-android/docs/components/Card.md` **[SPEC]**
So a card that lifts on hover is convention, not spec. Our trip cards lift by one
level on hover because they are clickable; the KPI tiles, which are not, do not
move at all.

### 5.2 Radius, and making the nesting actually concentric

The formula is `inner = outer − gap`, with a geometric derivation (concentric
circles share a centre).
Source: <https://cloudfour.com/thinks/the-math-behind-nesting-rounded-corners/>
**[CONVENTION, with a derivation]**

It is no longer only convention: Apple has promoted it to a platform API —
`ConcentricRectangle`, and a `concentric` corner style *"where the corner's radius
shares a center point with the container shape's corner radius"*, with
`containerConcentric` matching automatically.
Source: <https://developer.apple.com/documentation/swiftui/concentricrectangle>
**[SPEC]**

The shipped build violates this badly: the trips board is an 18px card whose
22px-padded interior holds 16px cards — `18 − 22 = −4`, so the inner corners are
*rounder than geometry allows*, which is precisely why they read as unrelated.

**Decision.** I picked the outer radius and the gap **together** so the arithmetic
lands on a value that is also a good radius, rather than picking three radii and
hoping:

```
--radius-xl: 20px    top-level surfaces
--radius-lg: 14px    = 20 − 6, so the trips grid is inset 6px from the board edge
--radius-md: 10px    buttons, inputs, inner trays
--radius-sm:  8px    status lozenges
```

Real concentricity, and the 6px inset gives the board a deliberate "tray" reading.

For the lozenges specifically I stopped using a pill. Atlassian is the only system
I found that publishes a **decision rule** for pill-vs-fixed: lozenges and tags get
4px, badges 2px, and `radius-full` is *"reserved for Avatars, pills, emoji
reactions"*. Source: <https://atlassian.design/DESIGN.md> **[SPEC]**
M3 agrees in practice — its chip is 32px tall at **8px** radius, with
`corner-full: 9999px` available and deliberately unused
(`tokens/.../_md-comp-assist-chip.scss`). Polaris Badge is likewise 8px, not its
own `border-radius-full`.

### 5.3 The optical asymmetry nobody eyeballs correctly

M3's filled-button padding, from its token file: no icon **24/24**; **leading icon
16 leading / 24 trailing**; **trailing icon 24 leading / 16 trailing**. The icon
side is tightened by exactly 8dp.
Source: `material-web/tokens/.../_md-comp-filled-button.scss` **[SPEC]**
Its chips do the same: bare 16/16, but with a leading icon the leading space drops
to 8px with an 8px icon-label gap.
Source: `_md-comp-assist-chip.scss` **[SPEC]**

**Decision.** Applied verbatim to every button with a trailing arrow (of which
Waypoint has five) and every lozenge with a leading pip. This is the "2× zoom"
defect that makes a button look bought rather than built, and it is a published
number, not a feel.

### 5.4 ALL CAPS

Atlassian's written guidance: *"MUST NOT use ALL CAPS or `letter-spacing`-stretched
uppercase anywhere—including eyebrows, lozenges, tags, badges, and table column
headers. There are no exemptions."*
Source: <https://atlassian.design/DESIGN.md> **[SPEC]**

Worth noting honestly: **their own shipped Lozenge component contradicts this**
(`text-transform: uppercase; font-size: 11px; letter-spacing: .165px` in
`@atlaskit/lozenge`'s compiled CSS). The system disagrees with itself; the newer
written rule is the one with a stated position.

**Decision.** This is the strongest external support for defects #8 and #10. Every
uppercase-mono eyebrow in Waypoint — and there were eleven distinct ones — becomes
sentence-case sans. I did not follow it to the letter: the app bar's dry-run phase
banner keeps a small-caps treatment, because GOV.UK's phase banner does and because
it is the one label that must not be mistaken for content.

### 5.5 Where monospace stays

The brief's rule (Vercel's) is mono for "code, commands, paths, tokens, and short
operational identifiers", with figures allowed if they carry `tabular-nums`.

I tested whether that "if" applies to us, because it decides whether the hero
figure can leave mono at all: **Figtree ships tabular figures**, verified in the
running build by measuring the rendered width of `1111` against `0000` with and
without `font-variant-numeric: tabular-nums` (proportional: unequal; tabular:
equal to within 0.5px). **[MEASURED]**

**Decision.** Mono survives in exactly three places, all of them genuine
operational identifiers: the desk id, the raw position ids in the record, and the
IATA codes on the boarding-pass cards. Every figure moves to Figtree with
`tabular-nums` — including the GSAP count-up, which is the case that actually
needed proving, since proportional digits would make it jitter frame to frame.

---

## 6. The KPI band

A number alone is meaningless — the standard formulation is the one about
quarter-to-date sales: *"Compared to what? Is this good or bad? Are we on track?"*
A tile needs the value, at least one comparison, and a qualitative state. This is
unanimous practitioner convention rather than a study. **[CONVENTION]**

The shipped band fails it four different ways *and* fails to fail it consistently:
four tiles, four anatomies, no shared baseline, one bar rendering at 0% with no
explanation, one figure printed three times, one tile left-aligned in a box twice
as wide as its content.

**Decision — one anatomy, four slots, every tile:**

```
eyebrow      what this measures            sans, 13px, secondary
metric       the value                     metric ramp, tabular-nums
meter        a track, or a state row       the comparison
context      one line                      the qualitative state
```

The metric ramp is separate from the heading ramp, which is Atlassian's idea and
the only published scale that names this (`font.metric.large` 28/32 Bold,
`medium` 24/28, `small` 16/20) — so a KPI value is not a heading in disguise. The
hero tile uses a larger step of the *same* ramp; that is a system, not a fifth
anatomy.

On the 0% bar: an empty track is the truth and I did not fake it. What made it read
as broken was that it was empty **and unexplained**. It now carries a zero-origin
tick and the state word "Nothing committed yet", which is Stripe's vocabulary from
§3.1. An empty state with a name is not a broken state.

On the triple `$12,000.00`: the metric is now `mandate.budget_total` — a single
real value that arrives on the wire — and the two client-side sums that produced
the other two printings are gone from the tile's text in the dry-run case, leaving
the bar (geometry from real decimals, which the brief permits) to carry spend.

---

## 7. Charts and comparisons

Cleveland & McGill's ranking as actually published: (1) position on a common scale;
(2) position on non-aligned scales; (3) **length, direction, angle — tied**;
(4) area; (5) volume, curvature; (6) shading, colour saturation. The familiar
"position > length > angle" is a later reformulation; the original ties rank 3.
**[EVIDENCE]**

Pass 9's dumbbell already encodes position on a common zero-based scale with the
authority cap drawn on the same axis, which is the right call and I did not touch
its geometry.

Two things I did change:

- **Stacked bars are position-encoding only for the bottom segment**; every other
  segment is a length judgment. So the new trips-status track in the KPI band is
  explicitly a *gist* graphic — it is never the only place a count appears, and the
  actual counts sit beside it as text. **[EVIDENCE]**
- **"If the user needs the variance, show the variance, not two numbers to
  subtract."** This is defect #14 exactly. A struck-through old price *and* a delta
  pill is two devices for one fact. **[CONVENTION]**

  There is a second, harder reason to pick one, and it is normative rather than
  aesthetic: *"The presence of the `s` element is not announced by most screen
  reading technology in its default configuration."*
  Source: <https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/s>
  **[SPEC]** A struck price is invisible to most assistive tech, so it cannot be
  the *only* carrier of "the price moved".

  **Decision.** One encoding at a time, and the one that survives: when a real
  `loss` or `alloc` event supplied an amount, the delta pill shows **that event's
  own figure** and the strike-through is dropped. When no such event exists, the
  strike-through stands alone — and gains a visually-hidden "was" so it is
  announced.

  I want to flag the tension honestly: the brief's §5.2 argues *for* redundant
  encoding (colour + shape) and §5.5 argues *against* it (struck price + pill).
  They are not in conflict — redundancy is right when the two encodings say the
  same thing to different audiences, and wrong when they compete for the same
  glance. "Redundant is bad" is not a citable rule in either direction.
  **[CONVENTION]**

---

## 8. Motion

Confirmed from primary token files rather than the summaries: M3 short1–4 =
50/100/150/200ms and medium1–4 = 250/300/350/400ms with standard easing
`cubic-bezier(0.2, 0, 0, 1)` (`material-tokens/json/motion.json`); Carbon fast-01
70ms, fast-02 110ms, moderate-01 150ms (`@carbon/motion`). **[SPEC]**

The press-scale number the brief gives (~0.96) traces to exactly one source —
Rauno Freiberg's interface guidelines — and **no design system publishes a
press-scale value at all**. Source: <https://interfaces.rauno.me/>
**[CONVENTION]**. I used it; I am not calling it evidence.

The principle I actually leaned on hardest is the brief's least-applied one:
**animation cost scales with frequency of use.** A judge will watch this run once;
a person testing it will watch it forty times.

**Decisions:**
- The entrance stagger on trip cards stays (first-encounter, and it is the labour
  illusion doing real work). Its GSAP anchors are untouched.
- Nothing new animates. I added no entrance to the KPI band, the record, or the
  footer — surfaces a repeat viewer sees on every reload.
- Hover transitions are 110ms (Carbon fast-02, "fades"); state changes 150ms;
  nothing over 240ms.
- Reduced motion is **substitution, not deletion**. The global reset moves from
  `animation: none` to `animation-duration: 1ms` so `transitionend` still fires —
  the brief's §5.3 point, and the reason the current nuclear rule had to change.
  Opacity-only alternatives are restored for the transitions that carry meaning,
  since *"animation involving only opacity, colour and blur is unlikely to be
  problematic."*

---

## 9. What I could not verify, and what I got wrong

- **Carbon's data-table row-height scale (24/32/40/48/64px) and its "type size
  never changes with density" rule.** The brief cites both; I could not reach a
  primary source for either. Row density in this pass is judgment.
- **Material 3 hover/pressed elevation for cards** — not published. Only resting
  and dragged are. Our hover lift is convention.
- **A published inset-vs-full-bleed rule for card headers and footers.** Material
  documents full-bleed for unrelated sections and 16dp-inset for related content
  within a section, which is the closest thing, and is what I followed.
- **`-webkit-font-smoothing: antialiased`** (in our reset): it is **macOS-only and
  has no effect on Windows or Linux**, and I am testing on Windows, so I *cannot*
  evaluate it and did not touch it.
  Source: <https://dbushell.com/2024/11/05/webkit-font-smoothing/> **[CONVENTION]**
- **`text-box-trim`** would solve the optical-alignment problem in the KPI tiles
  properly, but it only reached Baseline in 2026 and I could not confirm the
  rendering across the browsers this demo might run on. I used manual optical
  padding instead and left a note.
- **No user testing of any kind.** Nothing in the implementation report should be
  read as "users found this clearer" — only as "this is what the measurements and
  the sources support".
- The contrast, OKLCH and colour-blindness numbers in §4 are **my arithmetic**.
  They are reproducible and they reproduce Pass 9's independently, but they are not
  a third-party audit, and the Viénot matrix approximates one form of colour vision
  deficiency rather than substituting for testing with real people.
