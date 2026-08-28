---
kind: frontend_style
name: Next.js App Router with Global CSS Design Tokens (Mockup-Fidelity Styles)
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/app/globals.css
    - frontend/app/layout.tsx
    - frontend/app/page.tsx
    - frontend/app/desk/[deskId]/page.tsx
    - frontend/app/close/[deskId]/page.tsx
    - frontend/package.json
---

## What system/approach is used

The frontend is a Next.js 15 application using the App Router and React Server/Client components. Styling is done exclusively through a single global stylesheet (`frontend/app/globals.css`) — there are no component-scoped CSS modules, CSS-in-JS libraries, Tailwind, or third-party UI kits. The project declares only `next`, `react`, and `react-dom` as runtime dependencies; all styling lives in plain CSS.

## Key files and packages

- `frontend/app/globals.css` — the sole source of visual style for the entire app (114 lines).
- `frontend/app/layout.tsx` — imports `./globals.css` at the root so styles apply to every page.
- `frontend/app/page.tsx` — Screen 1: "Trip disrupted" (mockup 01).
- `frontend/app/desk/[deskId]/page.tsx` — Screen 2: "Agent recovering" live stream (mockup 02).
- `frontend/app/close/[deskId]/page.tsx` — Screen 3: "Recovery confirmed" outcome (mockup 03).
- `frontend/package.json` — confirms no styling framework dependencies beyond Next/React.
- `docs/plans/waypoint/mockups/` — HTML mockup files that the CSS was ported from (see comment on line 1 of `globals.css`).

## Architecture and conventions

### Design tokens via CSS custom properties
All colors and semantic values are centralized in a `:root` block:
- `--bg`, `--card`, `--ink`, `--mut`, `--line` — neutral palette (backgrounds, cards, text, muted text, borders).
- `--bad`, `--good`, `--accent` — semantic state colors (error/denied, success/approved, primary action).
These variables are referenced throughout the stylesheet instead of hard-coded hex values, giving a single point of theme control.

### Mockup-fidelity class naming
The stylesheet comment states: "Class names match the mockups for pixel-close fidelity across the 3 screens." Classes such as `.wrap`, `.brand`, `.sub`, `.pax`, `.leg`, `.badge`, `.cta`, `.stream`, `.table`, `.col`, `.tag`, `.settle`, `.ticket` mirror the structure of the static HTML mockups under `docs/plans/waypoint/mockups/`. Pages compose these classes directly in JSX `className` attributes rather than defining new component-level styles.

### Three-screen layout pattern
Each route renders one screen inside a `<main className="wrap">` container, which constrains content to `max-width: 640px` centered on the page. Branding uses the shared `.brand` class; status/error messages use `.status` / `.status.err`.

### Responsive strategy
There is no media-query-based responsive framework. The design targets a mobile-first narrow viewport by default (`.wrap` max-width 640px) and relies on Flexbox (e.g., `.cols` with `display: flex; gap: 12px`) for horizontal layouts. No breakpoints are defined in the stylesheet.

### State-driven visual variants
Visual variants are expressed as modifier classes applied conditionally in JSX:
- `.badge.ok` vs `.badge.dead`
- `.verdict.ok` / `.verdict.bad` / `.verdict.unknown`
- `.col.good` vs `.col.bad`
- `.pick` row highlighting
- `.struck` for crossed-out/disabled options
- `.status.err` for error messaging

### Typography and density
Body text uses a system font stack (`-apple-system, Segoe UI, Roboto, sans-serif`) at 15px/1.5 line-height. Monospace text (agent stream log, ticket output) uses `ui-monospace, Menlo, Consolas` on a dark background (`#111` with `#d7f0d7` text), creating a terminal-like aesthetic for operational feedback.

### No component library or utility framework
There is no Tailwind config, no CSS-in-JS, no component library (no shadcn, MUI, AntD, etc.). Every visual element is hand-written CSS against the token variables. This keeps the bundle minimal but means any new visual variant must be added explicitly to `globals.css`.

## Conventions and constraints

- **Single stylesheet**: All styles live in `frontend/app/globals.css`; no per-component CSS files exist.
- **Token-only colors**: New colors should be added to the `:root` custom properties rather than introduced as ad-hoc hex values.
- **Modifier-class pattern**: Visual variants are expressed as separate classes (`.ok`, `.bad`, `.unknown`, `.pick`, `.struck`) toggled via conditional `className` concatenation in JSX, not via CSS nesting or dynamic style objects.
- **Mockup alignment**: Class names intentionally mirror the static mockups to preserve visual fidelity during implementation.
- **No responsive breakpoints**: The stylesheet contains no `@media` rules; responsiveness is achieved through flexible layouts within the fixed 640px-wide container.
- **Global reset**: A universal `* { box-sizing: border-box }` reset is applied before any other rules.