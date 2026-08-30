# Pass 11 — legibility, one honesty fix, demo hardening

UI-only. Four files: `frontend/app/globals.css`, `frontend/app/presentation.css`,
`frontend/app/layout.tsx`, `frontend/app/desk/[deskId]/page.tsx`, plus this
report and its evidence. The reducer, replay/wipe, `EventSource`, `ixRef`,
snapshot fetching, the decision POST, the Waybot gate, `backend/**` and
`.qoder/**` are untouched. Every GSAP anchor survives: `.trip[data-ix]`,
`bigFigRef` on `.big`, `barFillRef` on `.bar .fill`, `scopeRef` on `<main>`, and
the `.pa-working` wrapper with its `aria-live="polite"`.

---

## 0. READ THIS BEFORE PREPARING THE DEMO MACHINE

**`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WAYBOT_GATED` are inlined into the
client bundle at BUILD time.** Setting them in the shell before `npm start`, or
in the container's `environment:` block, does nothing at all — Next has already
baked the values that were present when `next build` ran. They must be set
**before** the build:

```
NEXT_PUBLIC_API_URL=http://your-host:8000 NEXT_PUBLIC_WAYBOT_GATED=false npm run build
npm start
```

`docker-compose.yml` already does this correctly — both are `build.args`, not
`environment`. Anyone building outside compose must do the same. This was
confirmed the hard way during this pass: capturing the share-card screens
required a full rebuild with `NEXT_PUBLIC_WAYBOT_GATED=true`; changing it at
runtime had no effect whatsoever.

Two more things for whoever sets the machine up:

- **`WAYPOINT_ESCALATION_WAIT` is `"5"` in `docker-compose.yml:34`.** Five
  seconds is the window a presenter has to click the human-approval step — the
  centrepiece of the pitch — while also talking. The backend default is 300s
  (`backend/app/agent/loop.py:62`). The repo owner asked that the shared file be
  left alone and the value documented instead, so here is the exact edit:

  ```yaml
  -      WAYPOINT_ESCALATION_WAIT: "5"
  +      WAYPOINT_ESCALATION_WAIT: "180"
  ```

  This is a fail-closed timer: if nobody clicks, the cycle expires the
  escalation and gives up gracefully. Raising it only lengthens the window.

- **Serve the app over `127.0.0.1`, not `localhost`, if you hit a blank page.**
  On this machine `next start` returned HTTP 400 (Next's `/_error` page, code
  400) for a subset of `/_next/static/chunks/*.js` when the browser requested
  them via the `localhost` hostname — including `main-app`, so React never
  hydrated and the page rendered as static SSR HTML with a permanent
  "Connecting…". The same URLs returned 200 to `curl`, and 200 to the browser
  over `127.0.0.1`, where 0 of 16 resources failed and hydration completed
  normally. Not caused by anything in this pass; it also explains the broken
  `next start` that was already running on port 3000 when this pass began.
  All verification below was therefore run against `http://127.0.0.1:3000`.

---

## 1. Priority 1 — the honesty defect

`frontend/app/desk/[deskId]/page.tsx`, the agent console's narration block.

Pass 10 introduced `running = !settled && !awaiting && !streamDead` and applied
it to the phase banner, the hero, the trips empty state, the beacon and the
skeletons. It did not apply it to the narration, and the comment above the block
still claimed "same guard".

**Why it was reachable.** `settled = Boolean(screen.result) || screen.cycleFailed`,
and `cycleFailed` is set only by an explicit `DESK_CYCLE_FAILED` event on the
wire. A dropped connection sets `streamDead` and neither of those. So any run
that delivered at least one narration step and then lost its stream rendered
**"Working on: \<stale step\>" in the present tense with the stream already
dead** — the pulsing beacon eight lines above correctly suppressed, the claim
beside it not. That block is an `aria-live="polite"` region: a screen-reader
user got the announcement without the "No result" hero and "Connection closed"
lozenge that contradict it on screen.

**The fix.** A `streamDead` arm in the past tense, placed between `running` and
`settled`. The step is kept, not dropped — it genuinely arrived. The wrapper and
its `aria-live` are byte-identical.

```
running && latestStep      -> "Working on" + step            (present tense)
streamDead && latestStep   -> "Last step before the connection dropped — …"
settled                    -> "Finished — nothing running."
```

**Audit of every remaining `settled` in the desk page** (4 live uses):

| line | use | answers |
|---|---|---|
| 891 | `const settled = Boolean(screen.result) \|\| screen.cycleFailed` | definition — is the result final |
| 953 | `running = !settled && !awaiting && !streamDead` | derivation |
| 2050 | hero pending text: `settled ? "" : "Booking…"` | is the result final |
| 2302 | `settled ? "Finished — nothing running."` | is the result final |

None of them answers "is work happening". The three-way split is clean and can
now be stated:

> **`streamDead` = is the stream alive · `running` = is the run running ·
> `settled` / `screen.result` = is the result final.**

**Verified in the running production build** — a real desk, six narration steps
delivered, no result, then the backend restarted so the stream 404s on retry and
`EventSource` closes (evidence: `docs/pass11/desk-streamdead-1440-after.jpg`):

| check | result |
|---|---|
| narration reads in the past tense | ✅ "Last step before the connection dropped — Judgment in — 6 picks; the execute wall re-checks every one in code" |
| beacon absent | ✅ `.pa-beacon` not in the DOM |
| no skeletons | ✅ 0 × `.trip.skeleton` |
| hero says no result | ✅ "No result" + lozenge "Connection closed" |
| phase banner present | ✅ "Dry run — no real bookings" |
| `aria-live` intact | ✅ `aria-live="polite"` on the same wrapper |
| contrast in that state | ✅ 116 text runs, 0 below AA |

---

## 2. Priority 2 — the owner's note, answered

> *"maybe like the font changes? make bigger bolder or the color contrast. if not its good alr."*

Read as legibility, not as a contrast audit. **The three `--status-*-text`
values were not touched** — verified from the running build: `#007B49`,
`#995D00`, `#C23140`, unchanged.

### 2.1 The hero figure, restored

Two tokens existed for one role. `--fs-hero: clamp(32px, 4.6vw, 46px)` was the
old one and **nothing referenced it** (nor any other `--fs-*`); the live hero
used `--metric-hero-size: clamp(28px, 2.6vw, 36px)`. That is how the single most
important number in the product lost 22% at 1440 in the pass meant to strengthen
hierarchy. `--fs-hero` is deleted. One role, one token.

| | before | after | at 1920 | 1440 | 1080 | 390 |
|---|---|---|---|---|---|---|
| `--metric-hero-size` | `clamp(28px, 2.6vw, 36px)` | `clamp(30px, 3.3vw, 47px)` | 47px | **47px** (was 36) | 35.6px (was 28.1) | 30px (was 28) |
| hero tracking | `-1px` fixed | `-0.022em` (scales) | −1.03px | −1.03px | −0.78px | −0.66px |
| hero `min-height` | `calc(size × 1.12)` | `calc(size × var(--metric-hero-lh))` | token-bound | token-bound | token-bound | token-bound |

The wrap-up's peak-end figure gets its **own** step, because it owns a
full-width card rather than a tile in a four-up band — two tokens, two roles,
not two tokens for one role:

| | before | after | 1920 | 1440 | 1080 | 390 |
|---|---|---|---|---|---|---|
| `.close-status-card .budget .big` | `--metric-hero-size` (36px @1440) | `--metric-peak-size: clamp(34px, 4.2vw, 58px)` | 58px | **58px** | 45.4px | 34px |

**The hero column had to widen with it.** Measured at 1440 with the restored
47px: `"Over by $487.00"` needs 358px and `"Saved $1,234.00"` needs 360px,
against 339px of inner width in the old `1.55fr` column — so the headline figure
wrapped to two lines on *any* four-digit value, and the KPI band grew from 178px
to 230px. `.kpis` goes `1.55fr 1fr 1fr 1fr` → **`1.9fr 1fr 1fr 1fr`** (and the
meter-less variant `1.5fr` → `1.85fr`), giving 388px of inner width:

| | hero tile | companion tiles | hero lines | band height |
|---|---|---|---|---|
| before | 379px | 244px | 2 | 230px |
| after | 431px | 227px | **1** | **178px** |

Re-checked for label wrapping at 1280 and 1440: zero `.kpi-k`, `.kpi-ctx` or
`.kpi-v-of` wrapped to a second line that did not before.

### 2.2 Body text — the real "bigger"

Every step that carries **running text** moves up one notch. The heading steps
were already large and are deliberately untouched. Line-height follows the
ramp's own rule (the ratio tightens as size grows); tracking still crosses zero.

| token | before | after | Δ |
|---|---|---|---|
| `--font-fine-size` / `-lh` / `-track` | 12px / 16px / 0.16px | **12.5px / 17px / 0.14px** | +4% |
| `--font-small-size` / `-lh` / `-track` | 13px / 18px / 0.08px | **13.5px / 19px / 0.06px** | +4% |
| `--font-body-size` / `-lh` / `-track` | 14px / 20px / 0.04px | **15px / 22px / 0.02px** | +7% |
| `--font-lead-size` / `-lh` / `-track` | 16px / 24px / 0 | **17px / 26px / 0** | +6% |
| `.constraint-hint` | hard-coded 11.5px | `var(--font-fine-size)` = 12.5px | +9% |

15px rather than 16px: the trip cards run three-up and the KPI band four-up, and
15/22 is the largest step that leaves both intact at 1280 and 1440 with the
hero also restored. Combined with §2.3's colour move the perceived change is
considerably larger than 7%.

### 2.3 "Bolder" — colour first, then weight

**Secondary text colour.** `--text-secondary: #65707E` measured 4.82:1 on
`--paper` — over the 4.5 floor by 0.32 — while primary sits at 15:1, and it
carries nearly all the explanatory copy. It also measured **4.42:1 on `--badbg`,
i.e. it was already failing AA there**, which the Pass 10 table did not list.
One OKLCH step down the same hue (a = −0.00686 and b = −0.02473 held exactly)
gives `#4F5967`:

| ground | `#65707E` (before) | `#4F5967` (after) |
|---|---|---|
| `--paper` `#FBFAF7` | 4.82 | **6.80** |
| `--card` `#FFFFFF` | 5.03 | **7.10** |
| `--goodbg` `#EAF7F0` | 4.57 | **6.45** |
| `--warnbg` `#FBF3E4` | 4.56 | **6.44** |
| `--badbg` `#FBEDED` | **4.42 (FAIL)** | **6.23** |

L\* moves 46.8 → 37.5; primary stays at 13.9, so secondary is still clearly
secondary. `--text-tertiary` keeps the *old* `#65707E` — the two names are now
two real steps instead of one colour written twice (it was previously unused).

**Weight.** Secondary running text that inherited 400 from the body shorthand
moves to 500. Source weight census, `presentation.css`:

| weight | before | after |
|---|---|---|
| 400 | 4 | 1 (input placeholder only — a placeholder should stay lighter than the value) |
| 500 | 6 | 14 |
| 600 | 26 | 26 |
| 700 | 27 | 27 |
| 800 | 5 | 5 |

500 is the ceiling for running text: 600 reads as shouting and Figtree 700 in a
paragraph looks like a rendering error.

### 2.4 The trap — respected, and one correction restated

The three status text colours are untouched. Verified from the running build:
`--status-ok-text: #007B49`, `--status-wait-text: #995D00`,
`--status-no-text: #C23140`.

They converge at L\* ≈ 45 (45.1 / 45.1 / 44.3, mutual contrast 1.00–1.03:1)
because each was forced to clear AA on its own tint — and that convergence is
*why* status is carried by a word and a pip shape, never by hue.

**The correction, restated correctly** (Pass 10's stated rationale was wrong):
spreading the status lightnesses apart would not push a **pip** below 4.5:1,
because a pip is a non-text mark governed by SC 1.4.11's **3:1** threshold, not
the 4.5:1 text threshold — the pips have roughly 12 L\* of headroom the labels
do not. The real constraint on spreading them is **hue fidelity**: amber pushed
dark stops reading as amber and becomes brown. The shape system is still the
right answer; the reason previously given for it was not.

---

## 3. Priority 3 — demo hardening

**3.1 Escalation window.** Documented in §0, per the owner's decision to leave
`docker-compose.yml` alone. No repo change.

**3.2 Fonts are now self-hosted.** `layout.tsx` no longer emits a
`fonts.googleapis.com` stylesheet link; `next/font/google` downloads both
families at build time and serves them from this origin. Verified against the
production build: the served HTML contains **no** reference to
`fonts.googleapis.com`, and every `@font-face src:` in the built CSS is a
same-origin `/_next/static/media/*.woff2`. 22 `@font-face` rules, zero external
URLs anywhere in the CSS.

**And it caught a real defect the brief did not expect.** Walking every element
in the running build whose computed `font-family` resolves to IBM Plex Mono, the
weights actually in use are **400** (the desk id, `.fm-route`, `.fm-was`),
**600** (IATA codes) and **700** (`.tc-delta`, `.fm-now` — the price move and
the current fare). The old `<link>` requested only 500 and 600, so **every mono
700 was being synthesised as faux bold**, sitting in the same card row as real
ones. The request is now `400, 500, 600, 700`; `document.fonts` confirms 400,
600 and 700 all report `loaded` as real faces.

**3.3 Build-time env.** §0.

**3.4 The record now opens itself on settle.** `useState(false)` still, plus two
refs: it opens **once**, only when `settled` flips true, never while the run is
live (an accreting log under a live board is noise), and never against a reader
who has already touched the toggle. A reconnect replay re-delivers the same
result and must not re-open a panel the reader has since closed — hence
`recordAutoRef`.

No animation-on-open regression: the record's interior has no GSAP tweens and
its rows carry `animation: none` (the Pass 9 decision). **No page jump**,
measured at 1440 scrolled so the record sat 300px below the fold, sampling every
100ms across the transition:

| | before settle | after settle |
|---|---|---|
| `scrollY` | 554 | **554** |
| `.record` top in viewport | 654 | **654** |
| `.board` top in viewport | 116 | **116** |
| document height | 1453 | 4712 |

Only the document grew, downward. Nothing in view moved.

**3.5 Tested at 1920.** Nothing stranded or over-stretched: the `.wrap` caps at
1200px and centres, the KPI band and three-up trips hold their proportions, and
`scrollWidth == clientWidth == 1905` (no horizontal scroll). Landing, desk
mid-run, desk settled, record open, wrap-up, awaiting gate and share card all
captured at 1920.

---

## 4. Priority 4 — the four verified defects

**4.1 The trips empty state no longer asserts a false cause.** It read
*"No trips to show — we never reached this booking"* under `streamDead` and
*"Just starting — updates will appear here."* for everything else — so a run
that **settled** having booked nothing (every candidate dropped, the budget
exhausted, every escalation declined) was told either that the agent never got
there or that it was still warming up. Both false. Now four states, four
sentences, none asserting a cause the screen cannot see:

| state | copy |
|---|---|
| `awaiting` | "Nothing yet — the trips appear once you release the booking." |
| `settled` | "No trips on the board. This run reached the end of its work — whatever it considered is in the full record below." |
| `streamDead` | "No trips arrived before the connection closed. We can't say what happened after that." |
| otherwise | "Just starting — updates will appear here." |

`settled` is checked **before** `streamDead`: a result outranks a closed stream —
the run finished, we just stopped listening. The copy also got a measure
(`max-width: 58ch` on the copy, not on the dashed frame, so the frame still
lines up with the skeleton rows beneath it).

**4.2 The stacked gist track — the premise was already false, and it is now
stronger anyway.** The boundary numbers in the brief are right: `good|warn`
1.00, `good|bad` 1.03, `warn|bad` 1.03. But the segments **never touch**.
`.segbar` is a flex row with a gap, and the gap paints the tile's own surface —
measured from the running build as `rgb(255,255,255)`. White against each
segment:

| separator vs segment | ratio | SC 1.4.11 (3:1) |
|---|---|---|
| `#FFFFFF` vs `--status-ok-text` `#007B49` | 5.34 | pass |
| `#FFFFFF` vs `--status-wait-text` `#995D00` | 5.36 | pass |
| `#FFFFFF` vs `--status-no-text` `#C23140` | 5.52 | pass |
| `#FFFFFF` vs `.seg-flat` `#88919B` | 3.20 | pass |

So the track is not collapsing into an undifferentiated bar, and removing it was
not warranted. It was widened 2px → 3px so the separation survives a projector
and a downscale. The real counts still sit beside it in text.

**4.3 The agent console is NOT sticky — deliberately.** There are indeed no
`position: sticky` rules in the file, and the reason to leave it that way is
mechanical, not aesthetic: **a sticky grid item's containing block is its grid
area**, so a one-row item has zero travel no matter what `top` says. Measured,
the attention row gives the console about 47px of possible travel — visually
nothing. Giving it real travel means spanning it down the rows the trips occupy,
which restores the full-height 316px right rail Pass 10 deleted and takes the
trips grid from three-up to two-up at 1440: 1152px of content minus a 316px rail
minus a 24px gutter leaves 812px, i.e. ~260px cards against today's ~368px.
Buying a live-narration rail by shrinking the trips is the wrong trade. The
decision and its arithmetic are recorded in a comment at the head of §4 of
`presentation.css` so the next pass does not re-litigate it.

**4.4 The census re-run over rare states.** Five forced and measured, against
the production build, with every ratio computed from resolved painted colours
(each element's `color` composited over the first opaque ancestor background, or
over the page gradient evaluated analytically at that element's own centre):

| forced state | how | text runs | below AA |
|---|---|---|---|
| stream dead **with narration steps** | backend restarted mid-run → stream 404s on retry → `EventSource` CLOSED | 116 | **0** |
| never-reached / zero trips | stale desk id after a backend restart | 257 | **0** |
| awaiting-travelers gate | `gated:true` seed | 26 | **0** |
| $1,000 budget / $100 auto-approve cap | tight-constraint seed | 257 | **0** |
| very long destination + purpose (68 / 84 chars) | long-label seed; context line wraps to 3 lines, band grows to 212px, nothing spills | 257 | **0** |

Totals across all screens and widths: **0 text runs below AA**, on 14–257 runs
per screen.

**Two states I could not force, and why:** `DESK_CYCLE_FAILED` (`cycleFailed`)
requires the backend crash path, which is out of bounds for a UI-only pass; and
"budget exhausted" is not reachable through the recorded rail at all — the
recorded Atlas replay commits nothing, so `spent` stays `0.00` and the tile
honestly reads "Nothing committed yet" even with a $1,000 budget. Both of those
states reuse `.lozenge.no` and `--text-secondary`, which are measured elsewhere
in the table above, but the exact compositions were not rendered.

---

## 5. Two defects found during verification, not in the brief

**5.1 The KPI band never actually collapsed to one column at 390 when the meter
tile was absent.** `.kpis:not(:has(.kpi-meter-tile))` has specificity (0,2,0);
the plain `.kpis` inside `@media (max-width: 700px)` has (0,1,0). So the two-up
rule from the 1079px block won at *every* width below it. At 390 that left a
three-tile band two-up — and with the hero restored, `"Not started"` rendered
162px wide inside a 166px tile and **spilled 12px past the card's own edge into
the neighbouring tile**. Both selectors now collapse at ≤700px.

**5.2 A metric could push out of its own tile.** `.kpi-v` is a grid item and so
defaults to `min-width: auto`, refusing to shrink below its content. Explicit
`min-width: 0` lets it wrap instead. After both fixes, at 390: one column
(343px), zero children extending past any `.kpi` boundary.

---

## 6. Verification results — actual, not "should pass"

Production build (`npm run build` + `npm start`), served over `127.0.0.1:3000`
against the live recorded backend. Widths were driven by a same-origin iframe
harness sized to exact CSS pixels, so `1920 / 1440 / 1080 / 390` are the real
viewport widths the media queries saw (`documentElement.clientWidth` reported
back on every capture).

**1. Screenshots — 8 screens × 4 widths.** Landing, share card, awaiting gate,
desk mid-run, desk settled, record open, wrap-up, stream-dead — captured at
1920, 1440, 1080 and 390. Four are saved as evidence:

- `docs/pass11/desk-streamdead-1440-after.jpg` — the §1 fix
- `docs/pass11/desk-streamdead-greyscale.jpg` — the same screen desaturated
- `docs/pass11/desk-settled-1440-after.jpg` — the restored hero, one line
- `docs/pass11/desk-390-after.jpg` — the fixed single-column band

**2. The §1 fix specifically.** ✅ See the table in §1. Stream killed after six
narration steps and before any result; past tense, no beacon, no skeletons, "No
result", banner present, `aria-live` intact.

**3. Contrast.** ✅ Recomputed from painted values for every pair changed — the
tables in §2.1, §2.2, §2.3 and §4.2. The three `--status-*-text` values read
back from the running build unchanged.

**4. Greyscale.** ✅ Desaturating the desk leaves every status legible: each
carries a **word** ("Done — one trip still needs your OK", "Holding", "Book
decision logged", "recorded replay", "deterministic fallback", "curated — no
ML", "real — code-computed", "Connection closed") **and** a distinct pip shape
(`pip-full` / `pip-half` / `pip-open` / `pip-cross`). Price moves keep their ↓
glyph. Three encodings; two survive greyscale.

**5. Replay.** ✅ Reloaded twice mid-run and twice after settle. Identical
rebuild every time — same 14 `data-ix` values in the same order
(`2,9,11,3,12,4,13,5,14,6,15,7,10,16`), **zero duplicates**, zero elements left
at `opacity < 1` or `visibility: hidden`, identical trip-card and record-row
content hashes.

**6a. Keyboard.** ✅ Tabbed through the settled desk with real key events. Every
interactive element takes focus in DOM order, none is skipped, none traps.
`:focus-visible` paints the two-tone token everywhere —
`0 0 0 2px #FFFFFF, 0 0 0 4px #0F766E` on light, and the inverse
`0 0 0 2px #141B24, 0 0 0 4px #FFFFFF` on the open dark record. Every focused
element was inside the viewport. Enter on the record toggle collapses it and
flips `aria-expanded` to `false`.

**6b. Reduced motion.** ⚠️ **Verified by inspection, not emulated.** The harness
cannot set the browser or OS preference, and changing the system accessibility
setting was out of bounds. The contract is unchanged: the global reset in
`globals.css` collapses durations to 1ms (so `transitionend` still fires), the
`@media (prefers-reduced-motion: reduce)` block in `presentation.css` still
stops the beacon pulse, the skeleton shimmer and the press transforms, and the
GSAP `matchMedia` branches still render final state without tweening. **Pass 11
introduced no new animation, transition or keyframe** — the diff changes sizes,
colours, weights, a gap, grid columns and a `min-width`, nothing time-based.

**6c. Reflow.** ✅ At 320px (equivalently, 400% zoom of a 1280px viewport):
`scrollWidth == clientWidth == 305`, **no horizontal scroll on the document**.
The record's run-map is wider than 320 and scrolls inside its own
`.rm-scroll { overflow-x: auto }` container, which is the correct pattern. No
horizontal scroll at any tested width: 320, 390, 1080, 1440, 1920.

**6d. Console.** ✅ Clean. The only messages on any screen came from a browser
extension (`chrome-extension://…/contentscript.js`, MetaMask). No application
errors, no React hydration warnings.

**7. `npx tsc --noEmit`** ✅ clean. **`npm run build`** ✅ succeeds (run with the
dev server stopped, never sharing `.next`). Route sizes: `/` 3.77 kB, `/desk/[deskId]`
11.6 kB, `/close/[deskId]` 3.25 kB, 103 kB shared.

**8. Diff scope.** ✅ `git diff --stat` for this pass touches exactly four
frontend presentation files plus this report and its four evidence images. No
`backend/**`, no `.qoder/**`, no `docker-compose.yml`.

**8b. Fast-forward onto `origin/main` — ⚠️ NO LONGER TRUE, and not because of
this pass.** The branch was a clean fast-forward when this pass began (checked
first thing). During the pass `origin/main` gained one commit —
`9297e7b waybot: 2-pax sandbox capture knobs + recorded-mode UI label` — which
touches `frontend/app/desk/[deskId]/page.tsx` and `frontend/app/globals.css`,
two of the four files this pass edits. The branch is now **29 ahead, 1 behind**.

I did not rebase. Probing it on a throwaway branch (aborted, branch deleted, no
trace left), the rebase replays 24 commits and **conflicts at commit 3 of 24**,
in `frontend/app/page.tsx`, long before any Pass 11 work. Restoring the
fast-forward is therefore a 24-commit conflict resolution across someone else's
landed feature work, not a mechanical operation — and it rewrites a pushed
branch, which I cannot push. **That is the owner's call.**

One substantive overlap worth flagging before the merge: `9297e7b` adds a
**third mode-banner state** — "Recorded — replaying a real sandbox ticket" —
because recorded mode was mislabelling itself "Dry run". Every screenshot in
this report shows the old two-state banner ("DRY RUN — no real bookings") on a
recorded-mode desk. That change and this pass's phase-banner styling will need
reconciling in `page.tsx` and `globals.css`.

---

## 7. What I chose not to do

- **Sticky agent console.** §4.3 — the geometry makes it cost the three-up trips
  grid. Documented in the CSS.
- **Removing the stacked gist track.** §4.2 — the separator already clears
  1.4.11 at every boundary; there was nothing to fix beyond widening it.
- **Touching `docker-compose.yml`.** The owner chose "document it"; §0 carries
  the exact edit.
- **Changing the status text colours.** §2.4 — the trap, respected.
- **Deleting the other dead `--fs-*` tokens.** `--fs-fine` … `--fs-h1` are also
  unreferenced, but only `--fs-hero` was causing the drift the brief identified.
  Removing six more tokens is churn this pass does not need.
- **Rebasing onto the new `origin/main`.** §6.8b — 24 commits, conflicts from
  commit 3, rewrites a pushed branch, and I cannot push.

## 8. What I could not verify

- **`prefers-reduced-motion` in the browser** — §6b, by inspection only.
- **`DESK_CYCLE_FAILED` and budget-exhausted renders** — §4.4, unreachable
  without touching the backend or a rail that commits money.
- **Escalation resolved (`gone` / `busy` / `failed`)** — in recorded/dry-run mode
  the decision card renders no resolution buttons ("Dry run — I went with my pick
  on this one; nothing needs clicking"), so those three states have no path from
  the UI. The tab-through confirmed no decision buttons exist in this mode.
- **The `localhost` HTTP 400 on `next start`** — §0. Reproduced reliably and
  worked around, but not root-caused; it is an environment issue, not a code one
  (`curl` gets 200 on the same URLs, and so does the browser over `127.0.0.1`).
- **A real projector.** 1920 was tested as a viewport width, not on hardware.
