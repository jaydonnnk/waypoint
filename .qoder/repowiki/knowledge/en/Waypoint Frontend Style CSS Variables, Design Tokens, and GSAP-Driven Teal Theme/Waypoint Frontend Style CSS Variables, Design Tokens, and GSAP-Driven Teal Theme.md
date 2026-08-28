---
kind: frontend_style
name: 'Waypoint Frontend Style: CSS Variables, Design Tokens, and GSAP-Driven Teal Theme'
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/app/globals.css
    - frontend/app/presentation.css
    - frontend/app/layout.tsx
    - frontend/app/page.tsx
    - frontend/package.json
    - frontend/next.config.mjs
---

## What system/approach is used

The frontend is a Next.js 15 (App Router) + React 19 application styled with **plain CSS** — no CSS-in-JS, no Tailwind, no component library. Visual identity is driven by a single global stylesheet (`frontend/app/globals.css`) that declares a design-token layer on `:root`, plus an additive presentation stylesheet (`frontend/app/presentation.css`). Motion and ambient animation are handled via **GSAP** (`gsap` + `@gsap/react`) imported directly in client components.

Fonts are loaded at the root layout level from Google Fonts (`Figtree` for body text, `IBM Plex Mono` for figures/monospace), with fallbacks declared in CSS variables so the UI renders without network access.

## Key files and packages

- `frontend/package.json` — dependencies: `next`, `react`, `react-dom`, `gsap`, `@gsap/react`; no styling framework or UI kit.
- `frontend/app/layout.tsx` — loads `globals.css` and `presentation.css` as global stylesheets; injects Google Fonts `<link>` with `precedence="default"` to satisfy React 19.
- `frontend/app/globals.css` — the entire style system: design tokens, base styles, page-level shells, shared components (cards, buttons, chips, meters, blotter, toast, badges), responsive breakpoints, keyframes, and reduced-motion handling.
- `frontend/app/presentation.css` — additive-only layer scoped to class selectors; explicitly forbids `:root`, `body`, broad element selectors, and `!important`.
- `frontend/app/page.tsx` — uses GSAP (`useGSAP`, `gsap.matchMedia`) for staggered entrance animations and respects `prefers-reduced-motion`.
- `frontend/next.config.mjs` — minimal config; output mode toggled via `NEXT_STANDALONE` env var.

## Architecture and conventions

### Design tokens live in `:root`
All colors, fonts, radii, and spacing are exposed as CSS custom properties:
- Palette: `--paper`, `--card`, `--ink`, `--mut`, `--faint`, `--line`, `--line2`, `--brand` (deep teal #0F766E), `--good`, `--goodbg`, `--pop` (warm coral #F2764B), `--warn`, `--warnbg`, `--bad`, `--badbg`.
- Typography: `--sans = "Figtree", system-ui, sans-serif`; `--mono = "IBM Plex Mono", ui-monospace, monospace`.
- Radii: `--r-card: 18px`, `--r-trip: 14px`, `--r-btn: 11px`, `--r-bar: 8px`, `--r-pill: 20px`.
- Spacing scale: `--sp-2` through `--sp-7` (8–26px).
Legacy aliases (`--bg`, `--display`, `--accent`, terminal tokens) remain for backward compatibility during the Slice 8 refit.

### Single-source global stylesheet
`globals.css` is the authoritative source of truth. It contains:
- Base reset (`box-sizing: border-box`, body font/background).
- Shared primitives: `.wrap`, `.num`, `.brand`, `.beacon`, `.cta`, `.note`, `.status`, `.card`.
- Screen-specific sections: mandate intro, start card, full-bleed two-pane hero (`.mandate-screen`), immersive teal shell (`.teal-app`), desk header/meters, narration stream, blotter rows, escalation cards, result banners, run summary, trips list, close screen, tables, provenance rails strip.
- Responsive rules via `@media (max-width: 900px)` and `@media (max-width: 620px)`.
- Keyframe animations (`rise`, `blink`, `toast-in`) and a global `prefers-reduced-motion: reduce` rule that disables all animations/transitions.

### Additive presentation layer
`presentation.css` is intentionally constrained: it may only use class/pseudo-class selectors, must not touch `:root`, `body`, or broad element selectors, and must never use `!important`. This keeps it safe to load after `globals.css` without overriding core tokens.

### Two-page visual model
- **Screen 1 (start)**: full-bleed teal gradient background with an animated waypoint field (SVG routes, pins, aurora blobs, sheen) behind a two-column grid — left pane pitch copy, right pane constraint form. Collapses to single column under 900px.
- **Screens 2 & 3 (desk/close)**: same teal ground via `.teal-app`, but content floats as opaque white cards (`.run`, `.trip`, `.meter`) over the background, keeping dense data legible.

### Motion policy
Animations are transform/opacity only. GSAP runs inside `useGSAP` with `scope` refs, gated by `gsap.matchMedia("(prefers-reduced-motion: no-preference)")`. The budget bar fill starts collapsed (`transform: scaleX(0)`) so it can never overstate spend before JS settles it — an explicit honesty guarantee.

### Component naming
Classes follow a flat BEM-like convention using semantic names rather than utility classes: `.mandate-intro`, `.start`, `.assure-chip`, `.hero-brand`, `.hero-form`, `.teal-app`, `.desk-head`, `.meter`, `.stream`, `.blotter`, `.brow`, `.chip`, `.esc`, `.toast`, `.result-banner`, `.run`, `.trip`, `.badge`, `.close-status-card`, `.record`, `.fineprint`, `.rails`, `.rail`.

## Conventions and constraints

- **No CSS framework**: No Tailwind, Styled Components, Emotion, or similar — everything is hand-authored CSS.
- **Design tokens first**: Colors, fonts, radii, and spacing are always referenced via CSS variables, never hard-coded literals in component styles.
- **Additive overrides only**: `presentation.css` augments globals without redefining tokens or touching global selectors.
- **Reduced motion respected**: All animations are disabled when `prefers-reduced-motion: reduce` is set; GSAP matchMedia gates runtime animations.
- **Honesty semantics preserved**: Budget bar defaults to zero width; toast and status messages use established color registers (`--good`, `--warn`, `--bad`); mode banners and rail states map to consistent semantic classes (`.live`, `.pending`, `.comparison`, `.curated`, `.real`, `.recorded`, `.fallback`, `.unknown`).
- **Responsive strategy**: Breakpoints at 900px (two-pane collapses to one) and 620px (tighter padding). Uses `clamp()` for fluid typography and spacing.
- **Font loading**: Fonts are preconnected and loaded via `<link>` in `<head>` with `precedence="default"`; CSS fallbacks ensure offline rendering.