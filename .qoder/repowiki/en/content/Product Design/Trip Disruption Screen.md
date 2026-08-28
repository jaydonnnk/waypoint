# Trip Disruption Screen

<cite>
**Referenced Files in This Document**
- [01-trip-disrupted.html](file://docs/plans/waypoint/mockups/01-trip-disrupted.html)
- [02-agent-recovering.html](file://docs/plans/waypoint/mockups/02-agent-recovering.html)
- [03-recovery-confirmed.html](file://docs/plans/waypoint/mockups/03-recovery-confirmed.html)
- [routes.py](file://backend/app/api/routes.py)
- [page.tsx](file://frontend/app/page.tsx)
- [desk page.tsx](file://frontend/app/desk/[deskId]/page.tsx)
- [close page.tsx](file://frontend/app/close/[deskId]/page.tsx)
- [api.ts](file://frontend/lib/api.ts)
- [02-architecture.md](file://docs/plans/waypoint/02-architecture.md)
- [03-program-design.md](file://docs/plans/waypoint/03-program-design.md)
</cite>

## Update Summary
**Changes Made**
- Updated to reflect the migration from trip recovery workflow to desk-based architecture
- Clarified that visual mockups remain for reference but underlying implementation has changed
- Added documentation for new desk cycle management endpoints and SSE stream
- Updated API references from trip recovery to desk management
- Maintained backward compatibility notes for legacy functionality

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document specifies the Trip Disruption Screen as the primary entry point when a flight cancellation is detected. **Updated**: While the visual mockups remain for reference, the underlying workflow has been replaced by the new desk-based architecture. The screen now serves as a legacy interface maintained for backward compatibility, with the actual disruption handling managed through desk cycle management endpoints. The documentation explains how the screen presents the booked itinerary with cancelled legs clearly flagged, shows traveler passport information in context, and provides immediate visibility into disruption impact through the new desk-based system.

## Project Structure
The Trip Disruption Screen consists of three visual mockup screens that form a coherent flow, now backed by desk-based architecture:
- Trip disrupted: initial view showing the affected itinerary and recovery action (legacy UI)
- Recovering: live agent reasoning stream via SSE (now powered by desk cycle)
- Recovered: confirmation with before/after comparison (now using desk results)

```mermaid
graph TB
A["Trip Disrupted<br/>Legacy UI"] --> B["Desk Seed<br/>New Architecture"]
B --> C["Desk Stream<br/>SSE Events"]
C --> D["Desk Close<br/>Final Result"]
A -.->|Error states| E["No legal option / needs override"]
C -.->|Step budget exceeded| E
```

**Diagram sources**
- [01-trip-disrupted.html:1-51](file://docs/plans/waypoint/mockups/01-trip-disrupted.html#L1-L51)
- [routes.py:123-139](file://backend/app/api/routes.py#L123-L139)
- [routes.py:142-168](file://backend/app/api/routes.py#L142-L168)
- [routes.py:181-194](file://backend/app/api/routes.py#L181-L194)

**Section sources**
- [01-trip-disrupted.html:1-51](file://docs/plans/waypoint/mockups/01-trip-disrupted.html#L1-L51)
- [02-agent-recovering.html:1-64](file://docs/plans/waypoint/mockups/02-agent-recovering.html#L1-L64)
- [03-recovery-confirmed.html:1-63](file://docs/plans/waypoint/mockups/03-recovery-confirmed.html#L1-L63)
- [routes.py:123-194](file://backend/app/api/routes.py#L123-L194)

## Core Components
- Itinerary card list: Each leg is a card showing route, flight number, times, and status. Cancelled legs are visually distinct; downstream segments show risk status.
- Passenger context banner: Shows traveler name and passport country flag to reinforce rule checks and personalization.
- Recovery call-to-action: Primary button to initiate desk cycle management.
- Live reasoning stream: Monospace log showing desk cycle steps like mandate setup, reprice fan-out, judgment calls, and execution decisions.
- Desk state display: Lists positions with verdicts (book/hold/escalate) and highlights chosen options.
- Confirmation panel: Before/after comparison, settlement details, and ticket assertion via desk results.

Key responsibilities:
- Communicate disruption impact immediately and unambiguously.
- Provide clear next actions to start desk cycle management.
- Stream transparency during desk cycle to build trust.
- Confirm successful resolution with evidence from desk operations.

**Section sources**
- [01-trip-disrupted.html:14-47](file://docs/plans/waypoint/mockups/01-trip-disrupted.html#L14-L47)
- [02-agent-recovering.html:33-60](file://docs/plans/waypoint/mockups/02-agent-recovering.html#L33-L60)
- [03-recovery-confirmed.html:35-56](file://docs/plans/waypoint/mockups/03-recovery-confirmed.html#L35-L56)

## Architecture Overview
**Updated**: The Trip Disruption Screen now integrates with desk-based architecture via REST and Server-Sent Events (SSE):
- Frontend displays the Trip Disrupted screen and triggers desk seed.
- Backend receives desk seed requests, runs the desk cycle with DeskAgent, applies rules, and streams progress.
- The frontend updates the Recovering screen in real time via SSE events and then renders the Recovered screen upon completion.

```mermaid
sequenceDiagram
participant User as "User"
participant FE as "Frontend Screens"
participant API as "Desk REST"
participant Agent as "DeskAgent"
participant Rules as "Rules Engine"
participant Atlas as "Atlas Client"
participant SSE as "SSE Stream"
User->>FE : Open Trip Disrupted
FE->>API : POST /api/desk/seed
API->>Agent : run(desk_id, emit)
Agent->>Atlas : search alternatives (meter-gated)
Agent->>Rules : check each position
Rules-->>Agent : verdicts (book/hold/escalate)
Agent->>SSE : emit meta/mark/trade events
FE->>SSE : subscribe to /desk/{id}/stream
SSE-->>FE : streaming updates
Agent->>Atlas : verify offers + execute orders
Agent->>API : record desk state
API-->>FE : final DeskResult
FE->>FE : Render Recovered screen
```

**Diagram sources**
- [routes.py:123-139](file://backend/app/api/routes.py#L123-L139)
- [routes.py:142-168](file://backend/app/api/routes.py#L142-L168)
- [routes.py:181-194](file://backend/app/api/routes.py#L181-L194)
- [02-architecture.md:18-23](file://docs/plans/waypoint/02-architecture.md#L18-L23)

## Detailed Component Analysis

### Trip Disrupted Screen
Purpose:
- Immediately communicate that a leg is cancelled and what downstream impact exists.
- Show traveler passport context to frame why certain reroutes may be blocked.
- Offer a single, clear action to start desk cycle management.

Visual hierarchy:
- Top-level brand and trip summary establish context.
- Passenger banner prominently shows traveler identity and passport country.
- Itinerary cards:
  - Cancelled leg: high-contrast border and badge indicating cancellation.
  - Downstream segment: "AT RISK" badge to signal dependency on landing time.
- Primary CTA: full-width button to initiate desk cycle.
- Supporting note: reassurance about reading passport before rebooking.

Interactions:
- Clicking the recovery button initiates desk cycle via `/api/desk/seed` and navigates to the Recovering screen.
- Status badges provide quick scanning of leg statuses.

Responsive behavior:
- Single-column layout optimized for mobile with comfortable tap targets.
- Cards stack vertically; badges remain visible without overflow.

Accessibility:
- Use semantic headings for sections (brand/title, itinerary, actions).
- Ensure badges convey meaning beyond color (text labels like "CANCELLED", "AT RISK").
- Provide keyboard focus order: itinerary cards → recovery button → notes.
- Announce dynamic changes via aria-live regions when transitioning to the Recovering screen.

Error states:
- If desk seed fails, display an error message with retry and support options.
- If no legal option exists in desk cycle, surface a clear explanation and prompt for manual assistance.

Design system consistency:
- Follow spacing, typography scale, and color tokens used across mockups.
- Maintain consistent badge semantics and card structure for all disruption scenarios.

Extensibility:
- Add new leg types (e.g., multi-segment connections) using the same card pattern.
- Introduce additional status badges (e.g., delayed, changed gate) while preserving hierarchy.

**Section sources**
- [01-trip-disrupted.html:1-51](file://docs/plans/waypoint/mockups/01-trip-disrupted.html#L1-L51)
- [page.tsx:13-62](file://frontend/app/page.tsx#L13-L62)

### Recovering Screen
**Updated**: Now powered by desk cycle management instead of trip recovery.

Purpose:
- Provide transparent, step-by-step visibility into the desk agent's reasoning and actions.
- Show position assessments with desk-based verdicts and highlight chosen alternatives.

Key elements:
- Live stream: monospace log detailing mandate setup, reprice fan-out, judgment calls, and execution decisions.
- Search meter indicator: informs users when the desk agent will stop if it cannot resolve (20 searches/cycle).
- Position table: lists alternatives with desk verdicts (book/hold/escalate) and highlights the chosen option.
- Highlighted pick: visually emphasizes the chosen legal option from desk judgment.

Interactions:
- Auto-updates via SSE stream from `/api/desk/{desk_id}/stream`; no manual input required.
- Users can observe desk cycle progress and understand why certain positions are held or booked.

Responsive behavior:
- Stream scrolls within a fixed-height container on small screens.
- Table wraps content; ensure readability on narrow devices.

Accessibility:
- Stream text should be announced incrementally via aria-live polite regions.
- Table headers and rows must be properly labeled; use scope attributes for th.
- Ensure sufficient contrast for verdict colors and strike-through text.

Error states:
- If step budget is exceeded or no legal option is found, display a graceful give-up message explaining constraints and next steps.

Design system consistency:
- Reuse color tokens for book/hold/escalate verdicts.
- Keep typography and spacing aligned with other screens.

Extensibility:
- Add new desk outputs (e.g., seat selection, allocation decisions) to the table and stream without disrupting layout.
- Support multiple positions by grouping verdicts per position where relevant.

**Section sources**
- [02-agent-recovering.html:1-64](file://docs/plans/waypoint/mockups/02-agent-recovering.html#L1-L64)
- [desk page.tsx](file://frontend/app/desk/[deskId]/page.tsx)
- [routes.py:142-168](file://backend/app/api/routes.py#L142-L168)

### Recovered Screen
**Updated**: Now displays desk cycle results instead of trip recovery outcomes.

Purpose:
- Confirm successful desk cycle completion with clear before/after comparison and settlement evidence.
- Reinforce that the chosen option is legal and boardable for the traveler's passport based on desk judgment.

Key elements:
- Comparison columns: naive cheapest (rejected) vs. desk-booked (legal).
- Settlement block: original fare, new fare, auto-settled difference from desk operations.
- Ticket assertion: order created, payment confirmed, ticket issued via desk write path.

Interactions:
- Read-only confirmation; optional links to view PNR or manage booking.

Responsive behavior:
- Columns stack on mobile; settlement block remains readable.

Accessibility:
- Use descriptive headings for comparison and settlement sections.
- Ensure strike-through text conveys rejection; pair with explicit labels.

Error states:
- If desk cycle fails or ticket not issued, show a failure state with guidance to contact support.

Design system consistency:
- Maintain badge/tag semantics and color usage for success/failure.
- Align typography and spacing with previous screens.

Extensibility:
- Add additional post-booking info (e.g., seat assignment, lounge access) without breaking layout.
- Support multi-passenger confirmations with grouped details.

**Section sources**
- [03-recovery-confirmed.html:1-63](file://docs/plans/waypoint/mockups/03-recovery-confirmed.html#L1-L63)
- [close page.tsx](file://frontend/app/close/[deskId]/page.tsx)

## Dependency Analysis
**Updated**: The Trip Disruption Screen now depends on desk-based architecture:
- Backend desk endpoints for mandate seeding, state retrieval, and cycle management.
- SSE stream for live updates during desk cycle execution.
- Desk store for persistent evidence of positions, ledger, and budgets.
- Atlas client for searching alternatives, verifying offers, and executing orders within desk constraints.

```mermaid
graph LR
FE["Frontend Screens"] --> DESK_API["Desk REST Endpoints"]
DESK_API --> DESK_AGENT["DeskAgent"]
DESK_AGENT --> RULES["Rules Engine"]
DESK_AGENT --> ATLAS["Atlas Client"]
DESK_AGENT --> SSE["SSE Stream"]
DESK_AGENT --> STORE["Desk Store"]
SSE --> FE
STORE --> DB["SQLite Database"]
```

**Diagram sources**
- [routes.py:123-194](file://backend/app/api/routes.py#L123-L194)
- [02-architecture.md:25-31](file://docs/plans/waypoint/02-architecture.md#L25-L31)

**Section sources**
- [routes.py:123-194](file://backend/app/api/routes.py#L123-L194)
- [02-architecture.md:25-31](file://docs/plans/waypoint/02-architecture.md#L25-L31)

## Performance Considerations
- Minimize payload size for desk state and position lists to reduce render time on mobile.
- Debounce SSE messages to avoid excessive reflows; batch updates where appropriate.
- Lazy-load detailed position tables after initial critical path renders.
- Cache static assets (styles, fonts) and reuse components to improve perceived performance.
- Avoid blocking the main thread during large table renders; consider virtualization for long lists.
- Monitor desk search meter usage to prevent excessive API calls during fan-out operations.

## Troubleshooting Guide
Common issues and resolutions:
- Desk seed failure:
  - Symptom: Error state on Trip Disrupted screen.
  - Action: Retry desk seed; if persistent, show support link and fallback instructions.
- No legal position:
  - Symptom: Recovering screen indicates inability to find executable positions.
  - Action: Display explanation and prompt for manual assistance; log reason for audit.
- Step budget exceeded:
  - Symptom: Desk agent stops early due to complexity limits.
  - Action: Inform user of constraints and suggest escalation.
- Stale offer or price change:
  - Symptom: Verification fails or price differs from initial search.
  - Action: Re-run search within meter limits and update UI; notify user of changes.
- Ticket assertion failure:
  - Symptom: Order created but ticket not issued via desk write path.
  - Action: Surface failure state with support guidance; preserve audit trail.
- Desk cycle timeout:
  - Symptom: Desk cycle does not complete within expected timeframe.
  - Action: Check backend logs and provide user with timeout error information.

**Section sources**
- [routes.py:181-194](file://backend/app/api/routes.py#L181-L194)
- [03-program-design.md:145-155](file://docs/plans/waypoint/03-program-design.md#L145-L155)

## Conclusion
The Trip Disruption Screen serves as the critical first touchpoint for travelers experiencing flight cancellations. **Updated**: While the visual interface remains familiar, the underlying system has migrated to desk-based architecture for more robust disruption management. By clearly flagging cancelled legs, contextualizing passport constraints, and providing immediate desk cycle initiation, it reduces uncertainty and accelerates resolution. The subsequent Recovering and Recovered screens maintain transparency and trust through live desk cycle monitoring and concrete confirmation. Adhering to responsive design, accessibility standards, and error handling ensures a robust experience across devices and conditions. Extensibility guidelines enable future disruption scenarios while preserving consistency with the design system.

## Appendices

### Visual Hierarchy Reference
- Priority order:
  1. Cancelled leg badge and route.
  2. Passenger context banner (name and passport).
  3. Downstream risk indicators.
  4. Desk cycle initiation CTA.
  5. Supporting notes.

### Accessibility Checklist
- Semantic HTML structure with proper headings and landmarks.
- Color-independent status indicators (labels + icons).
- Keyboard navigation with visible focus states.
- Screen reader announcements for dynamic updates (SSE stream).
- Sufficient contrast ratios for all text and badges.

### Design System Consistency
- Use shared color tokens for background, cards, ink, muted, lines, bad, good, accent.
- Maintain consistent spacing, typography scale, and border radii across screens.
- Standardize badge semantics (e.g., CANCELLED, AT RISK, book/hold/escalate).
- Keep component patterns (cards, tables, banners) reusable and predictable.

### Legacy Compatibility Notes
- Visual mockups remain unchanged for backward compatibility.
- API endpoints have migrated from trip recovery to desk management.
- Frontend maintains existing user experience while leveraging new backend architecture.
- Migration path allows gradual transition from trip recovery to desk-based workflows.