# Pass 10 — implementation report

Person A / `person-a/ui-polish`. Companion to `docs/design-research-pass10.md`.

Screenshots referenced below are in `docs/pass10/`. Every number in §4 was
measured against the running production build, not estimated.

---

## 0. Before you read the rest

Three things I want stated up front rather than buried:

1. **I found and fixed a real honesty bug that was not in the brief's defect
   list.** When the SSE stream was dead, the desk kept rendering "Booking…", a
   pulsing beacon and three shimmering skeleton rows — inventing progress for a
   run that could never arrive. Details in §2.15.
2. **The status colours cannot carry status.** Measured: once each is dark
   enough to clear AA on its own tint, all three land at L\* ≈ 45 and become the
   same tone in greyscale. This reshaped the pass — every status is now a word
   plus a *shape*, with hue as a third redundant channel. §4.3.
3. **Two claims in the brief's own §5 did not survive checking** (the Primer
   shadow trick, and the reason given for preferring box-shadow focus rings).
   Both are documented in the research doc §1. I followed the instruction on the
   focus ring but for a different, true reason.

---

## 1. What shipped, in commits

| Commit | What |
| --- | --- |
| `c8a331e` | Merge `origin/main` (resolved a conflict in the share-card invite block) |
| `4aebb17` | `docs/design-research-pass10.md` |
| `3a1cbe2` | The token layer in `:root` — spacing, type, metric ramp, elevation, radius, semantic colour, focus, motion |
| `6e65cac` | Desk rebuilt on the tokens — one KPI anatomy, layout void removed, phase banner, shape-encoded status |
| `9b4bfcd` | Landing form treatment, wrap-up restructure, every measured AA failure fixed |
| `2cacafc` | Dead-stream / gate / empty states rebuilt so nothing claims work that isn't happening |
| _(final)_ | Trip-detail rows separate figures from reasons; report + evidence images |

There was an unresolved merge with `origin/main` pending when I started (4 commits
ahead, touching `frontend/app/page.tsx` and `globals.css`). I resolved and
committed it separately before touching anything else, as instructed.

---

## 2. Every defect in §4, and what was done

### Layout

**#1 — the large empty column at wide viewports.** *Fixed.*
Measured before: at 1440px the agent console was **166px tall inside a 1320px
grid**, leaving **1154px of bare background** down the right-hand side, because
the decision card, board, record and result banner all lived in the left column.

The right column now exists for exactly **one row** — the "attention row", where
the decision card (what needs you) sits beside the agent panel (what it's doing).
Both are short, so they balance. Everything below spans the full width. As a
bonus the trips grid now runs **three-up at 1440** instead of two.
When there is no escalation the rail has no partner, so the grid drops to one
column and the agent panel becomes a full-width strip.
`docs/pass10/desk-1440-before.jpg` → `desk-1440-after.jpg`.

**#2 — footer centred against left-anchored content.** *Fixed.* It was centred
inside the 892px left column of a 1280px wrap, so it was centred on nothing. It
is now a real `<footer>` on the full content width with a rule above it.

**#3 — no spacing hierarchy.** *Fixed.* Section gaps are `--space-8` (32px);
within-card group gaps are `--space-card-gap` (16px); card padding is a single
`--space-card-padding` token rather than a per-component decision. The ratio
between "between sections" and "within a component" is now 2:1 and consistent.

### The KPI band

**#4 — four tiles, four anatomies.** *Fixed.* All four now share one four-slot
grid — eyebrow / metric / meter / context — and the slots line up across tiles
because the tile itself is a `grid-template-rows: auto 1fr auto auto`. The hero
uses a **larger step of the same metric ramp**, which is a system rather than a
fifth anatomy. Figures use a separate metric ramp from headings (Atlassian's
`font.metric.*` idea) so a KPI value is not a heading in disguise.

**#5 — the budget bar renders at 0% and reads as broken.** *Fixed, honestly.*
An empty track is the truth in a dry run and I did not fake a fill. What made it
read as *broken* was that it was empty, solid, and identical in weight to the two
tracks beside it that do have fills. The zero state is now drawn **dashed**, with
a zero-origin tick and the state word "Nothing committed yet". An empty dashed
track reads as "nothing here yet"; an empty solid one reads as a bar that failed.

**#6 — `$12,000.00` printed three times, sub-line wrapping raggedly.** *Fixed.*
The metric is now `mandate.budget_total` — **one real value that arrived on the
wire**. The other two printings came from client-side sums over `budgets[]`; in
the dry-run case (every period's `spent` is `0.00`) they said nothing that
"Nothing committed yet" doesn't say better. A live run with real spend still gets
the committed/of line.

**#7 — status chips left-aligned in a wide box.** *Fixed.* The tile now carries a
metric (`0 of 6 booked`), a segmented gist track, and the counts as a wrapping
context row that uses the full width. The stacked track is **explicitly gist
only** — every segment above the first is a length judgment, so the real counts
always appear as text beside it.

One correctness fix while I was there: the "needs your OK" chip counted
*escalation events*, so a trip escalated twice counted twice. It now counts
**distinct positions**.

### Type and colour

**#8 — monospace doing far too much work.** *Fixed.* Mono survives in exactly
three places, all genuine operational identifiers: the desk id, the raw position
ids in the record, and the IATA codes on the boarding-pass cards. Eleven distinct
uppercase-mono eyebrows became sentence-case sans; dates, button labels, meter
readouts, section headers and the budget sub-line all left mono.

The load-bearing check: **Figtree ships tabular figures**, verified in the running
build by measuring the rendered width of `1111` against `0000` with and without
`font-variant-numeric: tabular-nums`. That is what let the hero figure leave mono
without jittering during the GSAP count-up.

**#9 — orphaned rule above the first provenance row.** *Fixed.* `.pa-sources`
carried a `border-top` that, whenever the working line was empty, sat above the
first rail with nothing over it. The panel now has a real header. When the panel
has nothing at all to say (the gate), it sheds its card entirely rather than
sitting there as an empty white bar — while keeping the `.pa-working` aria-live
region mounted and in the accessibility tree.

**#10 — tiny all-caps mono "THE TRIPS" far from its cards.** *Fixed.* It is an
18px sans header with its own count chip, sitting `--space-5` above the grid.

### Components

**#11 — the record slab.** *Fixed.* Collapsed, it was a large near-black block
holding one small ghost button: the visually heaviest element on the page saying
the least. Collapsed it is now a quiet light bar that **states what is inside**
using real counts ("14 entries · 4 steps · 4 sources"), so the reader can decide
whether to open it. The dark panel — which Pass 9 justified by measurement, not
taste — appears only once open.

The dark surface also moved from `#111` to `#141B24`, a desaturated dark step of
`--ink`. Pure-ish black has **no elevation headroom**; on a dark ground elevation
is lightness, not shadow. The raised step turned out to be `--ink` itself.

**#12 — underlined label inside a filled button.** *Fixed.* `.cta` is an `<a>`, so
the underline arrived by UA default rather than by choice. Removed, and every
button with a trailing arrow now uses M3's published asymmetric padding (tighten
the icon side by 8px) so it is not optically off-centre.

**#13 — nested radii don't relate.** *Fixed.* The board was an 18px card whose
22px-padded interior held 16px cards: `18 − 22 = −4`, so the inner corners were
rounder than geometry allows. I picked the outer radius and the gap **together**
so the arithmetic lands on a good value: outer `20px`, grid inset `6px`, inner
`20 − 6 = 14px`. Genuinely concentric.

Status lozenges also stopped being pills (8px), following the only published
decision rule I found: Atlassian reserves fully-rounded for "Avatars, pills, emoji
reactions"; M3's chip is 8px with `corner-full` deliberately unused.

**#14 — price movement encoded twice.** *Fixed.* The delta pill wins whenever a
real `loss`/`alloc` event supplied an amount, because that figure is a value the
system actually sent; the struck-through old price only stands in when no such
figure exists — and it then carries a visually-hidden "was", because **`<s>` is
not announced by most screen readers** (MDN), so it could never have been the sole
carrier of "the price moved".

### #2.15 — a defect that was not on the list

**A dead stream claimed the agent was working.** With the connection closed the
desk rendered "Booking…", a pulsing beacon, a "Working through the trips" status,
and three shimmering skeleton rows promising trips that would never arrive. The
guard everywhere was `!settled`, which is not the same as *running*.

Introduced `running = !settled && !awaiting && !streamDead`, and keyed every
work-claiming element off it. The dead screen now reads "No result" /
"Connection closed", the error is a real card rather than one small red line, the
empty state says "No trips to show — we never reached this booking", and the phase
banner says the mode is **unknown** rather than "connecting to the desk" next to
"Connection closed". `docs/pass10/desk-streamdead-after.jpg`.

---

## 3. Other work in scope

- **Screen 1** — labels left mono-uppercase; each numeric field states its own
  bound as a hint and only turns amber once the value is *actually* out of range
  (a cleared field is "not filled in", never "wrong"); inputs are 16px minimum so
  iOS does not zoom on focus; `inputmode` and `autocomplete` set;
  `touch-action: manipulation`; 48px minimum control height.
- **Screen 3** — the hero figure used to sit in a flex row with the label pushed
  to the far left and the figure to the far right, a screen's width apart. It is
  now one stacked hero block set off by a rule. The record toggle matches the
  desk's. The full-bleed white bar that was the only action became a normal-width
  button. The counts became compact tiles instead of two half-page boxes holding
  one digit each.
- **The awaiting-team gate** — `docs/pass10/desk-gate-after.jpg`.
- **Reduced motion** — the global reset moved from `animation: none` to
  `animation-duration: 1ms`, so `transitionend` still fires; vestibular triggers
  (the beacon pulse, the skeleton shimmer, press scaling) are stopped
  individually, while opacity fades are left alone.

---

## 4. Every §8 check, with its actual result

Run against the **production build** (`next build` + `next start`), driven by real
Chrome via puppeteer-core. The dev server hung twice under repeated automated
navigation, which is why verification moved to the production server; that also
covers check 12.

| # | Check | Result |
| --- | --- | --- |
| 1 | Screenshot every screen and state at 1440 / 1080 / 390 | **Done, and looked at.** 25 screenshots per pass, five iterations. Real PNGs from real Chrome. |
| 2 | Replay safety — reload mid-run and after settle, twice each | **PASS.** Three settled reloads produce byte-identical rebuilds (13 rows, 6 cards, 14 record rows, same hero figure). No duplicated `data-ix`. Nothing left at `opacity: 0` or `visibility: hidden`. Mid-run reloads caught rows mid-tween; re-checked after the timeline could end, everything lands. |
| 3 | Every event accounted for | **PASS.** 20 events on the wire (`meta` 1, `step` 4, `mark` 6, `loss` 1, `trade` 6, `escalate` 1, `result` 1). Blotter kinds sum to 14; **14 `.brow` rows rendered**. 4 narration steps on the wire, 4 rendered. The UI's own count-check line agrees: "20 events received — 14 logged in full below, 4 narration steps, the run header, the result." |
| 4 | Contrast audit | **PASS — 0 failures across 401 text runs.** See §4.1. |
| 5 | Greyscale test | **PASS.** `docs/pass10/greyscale-status-test.jpg`. |
| 6 | Keyboard pass | **PASS on all four screens.** Every focusable element reachable, every stop shows an indicator, none clipped by an overflow ancestor, all ≥24×24 CSS px, no traps, logical order. |
| 7 | Reduced motion | **PASS.** Nothing stuck invisible, hero settled on the real figure, 0 animations still running, console clean. |
| 8 | Reflow at 320px and 400% zoom at 1280px | **PASS** on all four screens, both ways. |
| 9 | No horizontal page scroll at 1440 / 1080 / 390 | **PASS** on all four screens. |
| 10 | Console clean | **PASS.** No errors, no React key warnings, no hydration warnings. The only console entry anywhere is a 404 for `/favicon.ico`, which this project has never had — pre-existing, unrelated to this pass. |
| 11 | `npx tsc --noEmit` | **PASS, zero errors.** |
| 12 | `npm run build` | **PASS.** |

### 4.1 The contrast numbers

**Method.** The auditor walks every visible text node, then — because every screen
sits on an animated teal gradient that `getComputedStyle` cannot see —
**screenshots the page and reads the actual painted pixels** in a band beside each
run, judging against the worst point sampled. Where the text's own element paints
an opaque background (a filled button, a chip), that is used directly. WCAG 2.x
relative luminance; large text = ≥24px or ≥18.66px bold.

My first run reported 110 failures. **90 of those were my auditor's fault**, not
the product's — it composited `backgroundColor` up the tree and so scored every
element on the teal ground as white-on-paper at 1.04:1. Fixing the auditor to read
real pixels left **20 genuine failures**, which I then fixed. Final:

| Screen | Text runs checked | Below AA |
| --- | --- | --- |
| Screen 1 landing | 23 | **0** |
| Screen 2 desk (settled, record open) | 281 | **0** |
| Screen 2 awaiting gate | 30 | **0** |
| Screen 3 wrap-up | 67 | **0** |

**The genuine failures that were found and fixed** (all measured, all pre-existing
except where noted):

| Pair | Was | Now | Where it renders |
| --- | --- | --- | --- |
| `--good` on `--goodbg` | **2.85** | **4.85** (`#007B49`) | "Booked" badge on every trip card |
| `--warn` on `--warnbg` | **2.88** | **4.86** (`#995D00`) | "Needs your OK" badge |
| `--bad` on `--badbg` | **3.82** | **4.84** (`#C23140`) | "Dropped in value" badge |
| `--mut` on `--paper` | **4.42** | **4.82** (`#65707E`) | every label, hint, date and chip on a card |
| white on `--pop` | **2.81** | **5.57** (`--ink`) | every filled coral button, incl. the gate's |
| `--good` on `--line` | **2.54** (SC 1.4.11) | **4.32** | the budget bar's fill against its own track |
| `--faint` as the "rest" segment | **2.55** (SC 1.4.11) | **3.20** (`#88919B`) | the trips gist track |
| white @86% on the teal | **3.76** | **≥4.5** (pure white + darker light stop) | footer note, section labels, connection line |
| `.b-ix` @36% on dark | **3.27** | **5.67** | the record's row-index column |
| `.fm-to` @40% on dark | **3.79** | **5.98** | the "→" between figures in the fare chart |
| `--status-wait-on-dark` on its amber tint | **4.27** | **4.81** (`#D1892A`) | "Dry run — I went with…" note in the record |
| `--status-ok-on-dark` on its green tint | **3.64** | **4.82** (`#3DBF81`) | "my pick" flag in the record |
| `.run-id` separator @60% on teal | **3.11** | **4.94** | the wrap-up header |
| ghost button on teal | **2.93** (unreachable at any alpha) | **15:1** (solid surface) | "Start another booking" |

Every derived colour was stepped in **OKLCH with the hue held** (drift ≤1.5°,
chroma reduced only where the colour left sRGB). No new hue families.

**Key token pairs, final:**

```
status ok text on its tint      #007B49 on #EAF7F0    4.85:1   (need 4.5)  PASS
status wait text on its tint    #995D00 on #FBF3E4    4.86:1   (need 4.5)  PASS
status no text on its tint      #C23140 on #FBEDED    4.84:1   (need 4.5)  PASS
budget bar fill on its track    #007B49 on #EAE7DF    4.32:1   (need 3.0)  PASS
segbar 'rest' on card           #88919B on #FFFFFF    3.20:1   (need 3.0)  PASS
meter fill on its track         #0F766E on #EAE7DF    4.43:1   (need 3.0)  PASS
ink on --pop (primary button)   #1B2430 on #F2764B    5.57:1   (need 4.5)  PASS
focus ring outer on card        #0F766E on #FFFFFF    5.47:1   (need 3.0)  PASS
focus ring inner on dark        #FFFFFF on #141B24   17.33:1   (need 3.0)  PASS
white on dark record base       #FFFFFF on #141B24   17.33:1   (need 4.5)  PASS
faint on dark record base       #9AA3AE on #141B24    6.79:1   (need 4.5)  PASS
ok-on-dark on raised surface    #17A76B on #1B2430    5.05:1   (need 4.5)  PASS
wait-on-dark on raised surface  #C9821F on #1B2430    4.99:1   (need 4.5)  PASS
no-on-dark on raised surface    #F05D65 on #1B2430    4.81:1   (need 4.5)  PASS
brand-on-dark on raised surface #449D94 on #1B2430    4.84:1   (need 4.5)  PASS

deliberately below body contrast, used ONLY as a 3px rule (a graphical
object needs 3:1, not 4.5:1) — this is why --bad is never body text on dark:
--bad on #1B2430  3.60:1   --bad on #141B24  3.98:1   (need 3.0)  PASS
```

### 4.2 The focus ring

One token, two tones:
`0 0 0 2px var(--card), 0 0 0 4px var(--brand)`.
`--brand` is 5.47:1 on white but only **2.86:1** on the raised dark surface, so a
single-colour ring cannot serve both grounds. On light the brand ring carries it;
on the dark record the white inner ring does, at 17.33:1. Meets SC 2.4.13's 2px
perimeter geometry (which is **AAA**, not the AA baseline — the brief is right
about this and many teams misreport it).

### 4.3 The greyscale result, and why it drove the pass

```
ok    #007B49   L* 45.1   deuteranope #686863
wait  #995D00   L* 45.1   deuteranope #8F9500
no    #C23140   L* 44.3   deuteranope #A1A900
```

Maximum L\* gap **0.8**; mutual contrast **1.00–1.03:1**. Once three hues are
forced to the same contrast ratio against equally-light backgrounds, they are
forced to the same lightness. This is arithmetic, not a palette flaw.

So colour carries **no** status information anywhere on a light surface. Every
status is a word plus a pip whose *shape* differs — hollow (nothing settled),
half-filled (decided but not executed), solid (snapshot-confirmed), triangle
(moved the wrong way) — following Polaris's Badge `progress` vocabulary.
`docs/pass10/greyscale-status-test.jpg` shows the band and cards fully
desaturated: "All done", "Holding", "Book decision logged", "needs your OK" and
"price drop" all remain distinguishable.

Greyscale is a **strictly harder** test than any colour-vision simulation — it
removes all hue information, where deuteranopia removes only the red–green axis.
Passing it means the CVD case passes too. I also rendered a Viénot deuteranope
simulation and it is consistent.

---

## 5. What I deliberately did NOT do

- **A "held / pending" third budget segment.** Stripe and Ramp both model money
  in three buckets and it is exactly right for this product — but the snapshot
  carries only `allocated` and `spent`. Summing held positions' `cost_basis` to
  invent a third figure would be client-side arithmetic presented as a system
  number. I took the **vocabulary** and left the bucket for a pass where the
  backend puts a held total on the wire.
- **A confidence indicator on the escalation.** The backend emits none. Bansal et
  al. (CHI 2021) find explanations increase acceptance *regardless of
  correctness*; inventing a confidence number is precisely that failure mode.
- **New entrance animations.** Animation cost scales with frequency of use. The
  existing trip-card stagger stays (first-encounter, and it is the labour illusion
  doing real work); I added no motion to the KPI band, the record, or the footer,
  which a repeat viewer sees on every reload.
- **Spreading the three status lightnesses apart** to restore greyscale
  separability. It forces one status below 4.5:1 on its tint, and shape costs no
  contrast at all.
- **`text-box-trim`** for optical alignment in the KPI tiles. It would solve the
  problem properly but only reached Baseline in 2026; I used manual optical
  padding instead.
- **Touching `--mut` itself.** `--text-secondary` is the new darker step;
  `--mut` still resolves to `#6B7684` so nothing else in the app shifted.
- **Removing any disclosure.** Every `disclosure` / `disclosures[]` string, all 14
  record rows, the narration, the rails' full `detail` sentences and the count-check
  line are exactly where they were. The record's four Pass 9 sections are intact.

---

## 6. What I could not verify, and what I'd flag

- **Real devices.** Everything was tested in headless Chrome on Windows at
  emulated widths. I have not touched an actual phone, and iOS Safari in
  particular is unverified — including whether the 16px input rule does what it
  is supposed to, and how `env(safe-area-inset-*)` behaves on the toast.
- **`-webkit-font-smoothing: antialiased`** in the reset is **macOS-only**. I am
  on Windows, so I literally cannot evaluate it and left it alone.
- **Screen readers.** The markup is correct by inspection (aria-live preserved,
  `aria-expanded`/`aria-controls` intact, visually-hidden text equivalents on both
  diagrams, a "was" label added to the struck price) but I have not run NVDA,
  JAWS or VoiceOver.
- **The dev server hung twice** under repeated automated navigation, both times
  needing a `.next` wipe. Backend was healthy each time. I do not know the cause
  and did not chase it — I moved verification to the production server. Worth
  knowing before a live demo.
- **No user testing.** Nothing here should be read as "users found this clearer",
  only "this is what the measurements and the sources support".
- **`:has()`** is used in three places (the KPI band's column count, the agent
  panel's empty state, the trailing-arrow button padding). Baseline since Dec 2023;
  fine for a modern demo, but it degrades to a slightly wider tile rather than
  breaking if it ever runs somewhere older.
- **One judgment call I'd flag for review:** the trips gist track. A stacked bar is
  a length judgment for every segment above the first, so it is genuinely weak
  encoding. I kept it because the counts are always beside it in text and it gives
  the tile the comparison a KPI needs — but if anyone thinks it earns its space
  poorly, removing it costs nothing.

---

## 7. Things that came out well, and one I nearly shipped as "fine"

**Well:** the layout void fix (the page finally reads as one design rather than two
mismatched columns), the KPI band's single anatomy, the record's collapsed state,
and the contrast work — 401 text runs at zero failures on four screens is a real
result, and it started from three shipped AA failures on the most-read component
on the page.

**The one I nearly let go.** The trip card's opened detail was the weakest thing in
the pass: the step rows right-aligned *everything* in the third column, so a
figure like `$462.00` and a sentence like "Waiting for a better fare" were treated
as the same kind of object, leaving "On hold" and its reason at opposite ends of a
narrow card. I had written it up as a known-weak component and then went back and
fixed it, because "merely fine" was not the bar.

A figure and a reason are different things and no longer share a slot: figures
keep the right-hand column, where they line up down the card; a reason is prose
and sits on its own line under its word. `docs/pass10/trip-detail-after.jpg`.

**Still the weakest thing here:** the trips gist track in the KPI band (see §6).
It is defensible but it is the one element whose encoding I would happily argue
against.
