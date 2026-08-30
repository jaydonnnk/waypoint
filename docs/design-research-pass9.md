# Design research — Pass 9: the full record

Person A / `person-a/ui-polish`. Written before any code was changed.

The target of this pass is the panel behind **"See the full record"** on Screen 2.
The teammate's complaint, verbatim: *"it's mainly this long line of logs"*, *"less
wordy, more diagrams"*, *"more user friendly."*

This document records what I looked up, where it came from, and — the part that
matters — **which decision each finding drove**. Findings that changed no decision
are not here.

## How to read the evidence grades

Not everything below is equally solid, and saying so is part of the job.

| Grade | Meaning |
| --- | --- |
| **Measured** | I computed it myself in this repo; reproducible (see `§6`). |
| **Peer-reviewed** | Published study; I read the abstract/summary and cite it. |
| **Practitioner** | Reputable design/industry source, not a controlled study. |
| **Convention** | Widely-held craft knowledge I could not attach to a source. Treated as a working assumption, not evidence. |

---

## 0. The ground truth I designed against

Before reading anything I captured one real run so that every design decision
below is checked against data that actually exists, not against an imagined
payload. Seeded a desk, recorded the full SSE stream, then pulled the snapshot
and the close report.

**The run: 21 events.** 1 `meta`, 4 `step`, 6 `mark`, 2 `loss`, 6 `trade`,
1 `escalate`, 1 `result`.

The blotter (what the record renders as rows) takes `mark` + `loss` + `trade` +
`escalate` = **15 rows**. That is exactly the "fifteen of those rows stacked"
wall named in the brief. Confirmed, not assumed.

Facts that shaped the design:

- **`spent` is `0.00` in all three budget periods.** Comparison mode logs
  decisions and never executes, so every allocation figure is unspent. Any
  "where the money went" chart would be three empty bars. This killed one of the
  brief's suggested directions (see §7).
- **5 of the 6 `mark` events came back `stale`** in this environment
  (`reprice failed — holding stale mark — uncertainty disclosed`). Amber is the
  dominant state of a local run, not the exception.
- **`cost_basis` and `mark_price` both exist on every position** in the
  snapshot — six real, paired values. This is the one genuinely chartable
  quantity in the payload.
- **The `escalate` event's two options carry the same price** (`1790.00` both).
  The branch is about the *action*, not a price difference. A two-price
  comparison graphic would imply a difference that is not there.
- **`ledger[]` has 9 entries, and 6 of them have `amount: "0.00"`** — the
  comparison-mode trade rows. The ledger's real content here is its *notes*, not
  its amounts.
- The stream carries **no timestamps**. Order is knowable; elapsed time is not.

---

## 1. Dense operational data, made readable

**Grade: Convention.** I searched for design write-ups on how Linear, Vercel,
Datadog, Stripe, Retool, GitHub Actions and Sentry structure run/event logs and
**found nothing citable** — the results were integration docs, not design
rationale. What follows is my own reading of those products, and I am marking it
as convention rather than dressing it up as research.

The recurring pattern across those tools, as I understand it: a run is presented
as **a summary that is always visible plus a record that is reachable**, and the
per-line detail is grouped under headings that name a phase, rather than being
streamed as one flat list. What survives as text is the line that carries a
number or a reason. What collapses is repetition. What becomes a graphic is
anything with a shared axis — duration, count, status over time.

**Decisions this drove**

- The record becomes **four titled sections**, not one scrolling list:
  *Where every trip stands* → *How the run went* → *Where the numbers came from*
  → *Every step*. Summary first, raw rows last.
- The 15 raw rows **survive completely** as the final section. Nothing is
  deleted, summarised away, or paraphrased.
- Repetition is what gets collapsed: five identically-worded stale-mark
  disclosures become one stated rule plus five marked rows, instead of five
  copies of the same italic sentence.

---

## 2. Agent / AI transparency, and disclosed uncertainty

**Buell, R. W. & Norton, M. I. (2011). "The Labor Illusion: How Operational
Transparency Increases Perceived Value." *Management Science* 57(9), 1564–1579.**
**Grade: Peer-reviewed.** Five experiments; the service domains were online
travel and online dating. Showing that work is being done raises perceived value,
mediated by perceived effort and reciprocity — to the point that people could
prefer a slower site that shows its work to an instant one returning identical
results.

This is already the stated basis for Waypoint's narration, and the travel domain
makes it unusually on-point. But the effect is about *visible labour*, which is a
claim about the run being watched live. It does **not** license a decorative
record after the fact.

**"Trusting AI: does uncertainty visualization affect decision-making?"
*Frontiers in Computer Science* (2025).** **Grade: Peer-reviewed, with caveats
I want on the record.** n=147, recruited via Amazon Mechanical Turk and
institutional channels; the tasks were *games* (Pac-Man, Minesweeper, Soccer),
not financial decisions. Uncertainty was encoded with size, colour saturation and
transparency.

Findings: visualising uncertainty significantly increased trust for **58% of
participants who held negative attitudes toward AI**; about **33%** changed their
decision when uncertainty was shown; **size had a greater effect than
transparency** as an encoding.

Caveat I am not going to paper over: gaming scenarios with an MTurk sample do not
straightforwardly transfer to a manager approving flight spend. I am treating the
direction as suggestive, not the magnitudes as applicable.

**Decisions these drove**

- The stale-mark state gets **more** visual weight, not less. It is the honest
  uncertainty in this run (5 of 6 marks), and the audience we most need to
  convince — a sceptical judge or investor — is exactly the group the Frontiers
  study found responds to visible uncertainty.
- Uncertainty is encoded by **size and shape** (a hollow ring versus a filled
  dot, at the same diameter as its neighbours), not by transparency. The study
  found size outperformed transparency; transparency also fails the contrast
  measurements in §6.
- The record is **not** allowed to imply live labour. It is a settled artefact.
  Nothing in it animates to suggest ongoing work.

---

## 3. Financial audit interfaces

**Grade: Convention.** I could not find a citable design study on trading
blotters or audit interfaces, and I am not going to invent one.

The conventions I am designing to, stated as assumptions:

- A record is trusted when the summary and the row-level detail are **visibly the
  same data at two zoom levels**, and the reader can get from one to the other.
- Figures are monospaced and tabular so digits align in a column; signs are
  explicit; magnitudes are never abbreviated in an auditable row.
- An audit trail is **complete or it is not an audit trail**. Filtering is
  acceptable; silent omission is not.

**Decisions these drove**

- The record states its own **composition and count** — "21 events received: 15
  in the log below, plus 4 narration steps, the run header and the result." A
  reader can check the record against the stream without trusting me. This also
  makes the brief's §8.10 count-check a visible property rather than a test I
  claim to have run.
- Every figure keeps `font-variant-numeric: tabular-nums` and `money()`
  formatting in the raw rows. The diagrams may draw geometry from figures; only
  `money()` output is ever printed as text.
- No number in the new work is computed client-side. Where the obvious thing to
  draw was a *delta* (`mark_price − cost_basis`), I print both endpoints instead
  and let the drawn segment carry the gap — see §4.

---

## 4. Data visualisation at small n

**Practitioner sources:** Domo's chart guides for
[slope](https://www.domo.com/learn/charts/slope-chart) and
[dumbbell](https://www.domo.com/learn/charts/dumbbell-plot-chart) charts;
[Nightingale, "Beyond the Bar"](https://nightingaledvs.com/beyond-the-bar-alternative-methods-for-visualizing-two-points-of-change/).
**Grade: Practitioner.**

The distinction they converge on: **use a dumbbell when the size of each gap is
the point; use a slope chart when direction and ranking are the point.** A
dumbbell draws each category as two dots joined by a bar, so every gap is legible
at once. Both forms are explicitly recommended for small category counts, which
is what six trips is.

**The problem this created, and how I resolved it.** Waypoint has exactly two
points per trip (`cost_basis`, `mark_price`) and six trips — textbook dumbbell.
But the real values are:

| Trip | Cost basis | Mark |
| --- | --- | --- |
| SIN→NRT | 445.00 | 462.00 |
| DAC→LHR | 820.00 | **1790.00** |
| JFK→LIS | 610.00 | 588.00 |
| BKK→ICN | 388.00 | 323.00 |
| SYD→SIN | 705.00 | 742.00 |
| GRU→MIA | 690.00 | 655.00 |

One trip's move (+970) is fourteen times the next largest (−65). On a shared
zero-based axis the other five gaps render at roughly 1–3% of the width — a few
pixels. This is exactly the "looks silly at this scale" failure the brief warns
about.

**Decisions this drove**

- I kept the **shared, zero-based axis** rather than normalising per row. The
  outlier dominating the chart *is the finding* — it is why the run escalated —
  and a per-row rescale would hide the one fact the picture exists to show.
- I added the **authority cap (`mandate.authority_cap`, $1,500.00) as a labelled
  rule on the same axis.** This is the decision that turns a merely-honest chart
  into an explanatory one: five marks sit left of the line, one sits right of it,
  and the escalation stops needing a sentence. The cap is a real mandate field,
  drawn to the same scale as the dots.
- The small gaps are recovered **as text, not geometry**: each row prints both
  real figures (`$820.00 → $1,790.00`). The eye gets position from the axis and
  precision from the numerals.
- **No delta is printed.** `mark − cost` is client-side arithmetic and the brief
  forbids presenting it as a system figure. Where a real `loss` event exists, its
  own `amount` is shown, because that figure was sent.
- The second diagram (the run timeline) uses **arrival order as its x-axis, and
  says so**. The stream carries no timestamps; an axis labelled "time" would be a
  fabrication. Labelling it "order of events" costs nothing and is true.

---

## 5. Progressive disclosure

**[NN/g, "Progressive Disclosure"](https://www.nngroup.com/articles/progressive-disclosure/).**
**Grade: Practitioner** (NN/g summarising research; the article itself reports no
statistics).

It improves "learnability, efficiency of use, and error rate" by showing a few
important options first and the specialised set on request. On the specific worry
that hiding things damages the user's mental model, the article says: *"Research
says that these are groundless worries: people understand a system better when
you help them prioritize features."*

**Where I think that finding does not reach, and why it matters here.** NN/g is
describing *features* deferred to a secondary screen. We are hiding an **audit
trail** — and the entire pitch is that the agent shows its work. I found no
evidence that the feature-deferral result transfers to evidence-suppression, and
I am not going to pretend it does. A "20–40% faster task completion" figure
circulating on secondary blogs is **not** in the NN/g article; I checked, and I
am not citing it.

**Decisions this drove**

- The record's four sections are **plain headings on one scrolling panel, not
  tabs.** Everything stays in the DOM, reachable by scroll, by browser find, and
  by print. Tabs would hide evidence behind a click and make Ctrl+F fail — the
  wrong trade for an audit trail.
- The one disclosure control that stays is the **existing outer toggle**, which
  is already the established pattern on both screens.
- Section headings **carry counts** ("Every step · 15 entries"). A reader can see
  how much is inside before deciding to read it, so collapsing never feels like
  concealment.

---

## 6. Colour, accessibility, motion — measured

**Grade: Measured.** This is the strongest evidence in this document because I
generated it from the actual palette rather than citing someone.

Method, so the numbers can be re-derived (no script is committed — this pass
ships only presentation files and this document): ratios are WCAG relative
luminance, `L = 0.2126R + 0.7152G + 0.0722B` over sRGB channels linearised by
`c/12.92` when `c ≤ 0.04045` else `((c+0.055)/1.055)^2.4`, contrast
`(L_light + 0.05) / (L_dark + 0.05)`. L\* is CIE lightness from the same
luminance. The colour-blindness column is the Viénot (1999) deuteranope matrix
applied in linear RGB. Inputs are the `:root` tokens in `globals.css`, unchanged.

**The requirement:**
[WCAG 2.2 SC 1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)
— *"Color is not used as the only visual means of conveying information,
indicating an action, prompting a response, or distinguishing a visual element."*

### 6a. Text contrast on the light grounds

| Token | On `--paper` #FBFAF7 | On `--card` #FFF | Verdict |
| --- | --- | --- | --- |
| `--ink` #1B2430 | 15.00 | 15.65 | passes AA body |
| `--mut` #6B7684 | 4.42 | 4.62 | borderline on paper |
| `--faint` #9AA3AE | **2.45** | **2.55** | **fails even 3:1** |
| `--brand` #0F766E | 5.24 | 5.47 | passes AA body |
| `--good` #15A66A | 3.01 | 3.14 | large / non-text only |
| `--warn` #C8811E | 3.04 | 3.17 | large / non-text only |
| `--bad` #D64550 | 4.17 | 4.35 | large text only |
| `--pop` #F2764B | **2.69** | **2.81** | **fails even 3:1** |

### 6b. The same colours on the dark record surface `--term-bg` #111

| Token | Ratio on #111 |
| --- | --- |
| `--good` | **6.02** |
| `--warn` | **5.95** |
| `--pop` | **6.72** |
| `--faint` | **7.40** |
| `--bad` | 4.34 |
| `--brand` | 3.45 |

**This is the single most decision-changing measurement in the pass.** Every
status colour reaches AA *body* contrast on the dark ground and only
large-text/non-text contrast on the light ground. `--faint` goes from failing
(2.45) to comfortable (7.40).

### 6c. Colour-blind separability

| Pair | L\* gap | Deuteranope simulation |
| --- | --- | --- |
| `good` vs `warn` | **0.4** | #6B6180 vs #B2B64D — separable |
| `warn` vs `bad` | 9.1 | **#B2B64D vs #B2BA4D — effectively identical** |
| `good` vs `bad` | 9.5 | separable |

Two hard findings:

1. **`--good` and `--warn` have the same lightness** (L\* 60.3 vs 60.0). Any
   greyscale or lightness-only rendering merges them.
2. **`--warn` and `--bad` collapse to the same colour under deuteranopia**
   (#B2B64D vs #B2BA4D — a 4/255 difference in one channel). Amber and red are
   *not* distinguishable for a large group of viewers.

### 6d. White-on-fill

`--pop` is the primary CTA fill, currently with `color: #fff` — **2.81:1 at
700 weight / 15px**, which is not large text and therefore fails AA. `--ink` on
`--pop` measures **5.57:1**. Similarly `--good`/`--warn` chips currently print
their own hue on a tinted background at **2.85** and **2.88**.

**Decisions this drove**

- **The record stays dark.** It was previously a stylistic choice ("the one dark
  surface, log-console convention"); §6b turns it into a measured one. The new
  diagrams live on #111 specifically because the status palette only reaches AA
  body contrast there.
- **`--faint` never carries text on a light surface** anywhere in the new work.
  On the dark record it is fine and is used for axis labels.
- **Every status is a word plus a mark, and the marks differ in shape**, not only
  hue: filled disc = settled, hollow ring = uncertain/stale, diamond = needs a
  human, bar = a drop. This satisfies SC 1.4.1 and — more importantly — survives
  §6c, where amber and red are the same colour to a deuteranope. The brief
  already required colour + word; the measurement shows why it is load-bearing
  here rather than box-ticking.
- **Primary CTA switches to `--ink` on `--pop`** (2.81 → 5.57). Palette unchanged,
  no new hue; only the text colour moves.
- **Status chips switch to `--ink` text** with the hue carried by an accent, so
  no chip prints at 2.85:1.
- Motion: the record's diagrams **do not animate on open.** The entrance tweens
  in this app exist to show live work arriving; the record is a settled artefact,
  and animating it would be decoration. This also keeps the pass clear of the
  GSAP anchors, which are untouched.

---

## 7. Directions I considered and rejected

The brief offered seven directions and said explicitly not to do all of them.
What I dropped, and why:

- **"Where the money stands" — budget allocation across the three periods.**
  Rejected. `spent` is `0.00` in all three periods because comparison mode never
  executes. The chart would be three identical unspent bars — visually inert and
  easy to misread as broken. The honest version of this fact is one sentence, not
  a graphic, so it becomes a stated line: committed $0.00 of $12,000.00, nothing
  executed. If a live-ticketing run ever populates `spent`, this becomes worth
  drawing.
- **A provenance *diagram* from `rails[]`.** Rejected as drawn. `rails[]` says
  what each source *is* (Atlas / Qwen / Priors / Ledger and its state); it does
  **not** say which source fed which decision. Drawing edges between sources and
  decision kinds would mean inventing the mapping — a direct violation of rule 1.
  The four rails stay as an honest, legible panel with their full `detail` text
  and correct non-green tone for `fallback`; I am not drawing a flow that the
  data does not contain.
- **The meter as a gauge.** Rejected. It already exists as a bar with a real
  reading in the KPI band. A second copy in a different form is duplication, and
  the brief warns against duplicating it.
- **The escalation as a two-price branch.** Kept, but drawn as a *branch*, not a
  price comparison — because both options are priced `1790.00` in the real
  payload. A graphic emphasising the price difference would imply a difference
  that does not exist.

---

## 8. Scanning and hierarchy

**[NN/g, "The Layer-Cake Pattern of Scanning Content on the Web"](https://www.nngroup.com/articles/layer-cake-pattern-scanning/).**
**Grade: Practitioner** (NN/g eyetracking).

Eyetracking identifies four text-scanning patterns: F, spotted, layer-cake and
commitment. In the layer-cake pattern fixations land on **headings and
subheadings**, with few fixations in between until the reader finds the heading
they want and then reads the body beneath it. NN/g's assessment: *"Aside from
reading almost every word, the layer-cake pattern is by far the most effective
way in which users can scan pages."*

**[Laws of UX — Von Restorff effect](https://lawsofux.com/von-restorff-effect/).**
**Grade: Practitioner / secondary** (a 1933 effect, cited here via a secondary
source rather than the original). When several similar objects are present, the
one that differs is the one noticed and remembered — with the caveat that
emphasis must be used sparingly or the emphasised items compete.

**Decisions these drove**

- The record's failure today is precisely a layer-cake failure: **fifteen rows of
  identical visual weight and no headings**, so there is nothing for the scan to
  land on. Adding four real headings is the single highest-leverage change, and
  it costs no content.
- Headings are **unique and descriptive** ("Where every trip stands", not
  "Section 2"), because the pattern only works if the heading tells the reader
  whether to stop.
- Von Restorff is why the **cap line** is the one emphasised mark in the fare
  chart, and why **one trip's row is the visually distinct one**. Every other
  element in the record is deliberately uniform so that the exception reads as
  the exception. It is also the argument against emphasising all five stale marks
  individually — five emphases are no emphasis.

---

## 9. What I could not verify

Stated plainly, per the brief:

- **No citable source for §1 (operational log UIs) or §3 (financial audit
  interfaces).** Both are marked Convention. My searches returned integration
  documentation and marketing pages, not design research.
- The **Frontiers uncertainty study is on games, not finance**, with n=147 from
  MTurk. I used its direction, not its numbers.
- The widely-repeated **"progressive disclosure cuts task time 20–40%"** claim is
  **not** in the NN/g article it is usually attributed to. I found it only on
  secondary blogs with no primary citation, and I have not used it.
- **Von Restorff (1933)** is cited via Laws of UX; I did not read the original
  German paper.
- The **contrast and colour-blindness numbers in §6 are mine**, computed from the
  palette in `globals.css`. They are reproducible, but they are my arithmetic,
  not a third party's audit. The deuteranope simulation is the Viénot (1999)
  linear-RGB matrix, which is an approximation of one common form of colour
  vision deficiency, not a substitute for testing with real users.
- I have **not** user-tested any of this. No claim in the implementation report
  should be read as "users found it clearer" — only as "this is what the evidence
  and the measurements support."

---

## 10. The resulting design brief

What the record becomes, what each mark encodes, and which real field feeds it.

### Section 1 — "Where every trip stands" *(the hero diagram)*

A dumbbell chart, one row per position, on a shared zero-based money axis.

| Mark | Encodes | Real field |
| --- | --- | --- |
| Hollow dot | what the trip cost when booked | `positions[].cost_basis` |
| Filled dot | what it prices at now | `positions[].mark_price` |
| Connecting bar | the gap (geometry only, never printed) | derived — permitted |
| Dashed rule | the auto-approve limit | `mandate.authority_cap` |
| Ring instead of disc | this mark is stale | `positions[].mark_stale` |
| Row text | both endpoint figures | `money(cost_basis)`, `money(mark_price)` |
| Loss figure | a real admitted loss | `loss.amount` (event) |

Text equivalent: a visually-hidden sentence per row carrying trip, route, both
figures and the stale state, plus a caption naming the source and the axis.

### Section 2 — "How the run went"

A laned timeline. x = arrival order of blotter events (**not** time — the stream
has no timestamps). y = one lane per trip, in first-arrival order. One glyph per
event, shaped by kind and coloured by tone, with the trip's label on the lane.

Every one of the 15 blotter events appears exactly once. Shape carries kind so
the chart survives the §6c amber/red collapse.

### Section 3 — "Where the numbers came from"

The four `rails[]` rows, each with `rail`, `label`, full `detail`, and a tone
class from `state`. `fallback` renders amber, never green — per the brief's
absolute rule and per the fact that Qwen genuinely did not run.

### Section 4 — "Every step"

The complete raw record: the `meta.disclosures[]` notes, the four narration
steps, and all 15 `.brow` rows with chip, position id, figures, body and
disclosure. Restyled for the dark ground and the contrast findings; **nothing
removed**. Headed with the composition count from §3.

### States every section must handle

Empty · mid-run · settled · error · stale data · no snapshot yet · escalation
open · reduced motion · awaiting-team gate. Where a section has no real data it
renders nothing or a content-free skeleton — never a zero, never a placeholder
figure that could be mistaken for a reading.
