# Product: Waybot — team-trip identity capture → autonomous booking

## Problem

Today Waypoint books flights against **hardcoded demo travelers**. An ops manager can set the money mandate (budget, cap, contingency) but cannot get real people onto the ticket, and cannot sign off on the actual itinerary before money moves. Two real jobs are missing on the human side:

- **"I have a team of N going to a client meeting. Getting everyone's passport details is the annoying part."** The manager chases people over chat, copies passport numbers by hand, fat-fingers a document number, and a booking gets rejected.
- **"I want to see what it's going to book before it books it."** Right now the agent decides and executes; the manager never sees the chosen itinerary until it's ticketed.

Waybot closes both without turning Waypoint into a form-filling booking tool: the manager keeps the money mandate; the *travelers* supply their own identities through a chat bot they already have open.

## Success metric

**Time-to-release for an N-traveler trip: from "manager starts a desk" to "all N passports captured and validated, manager approves, agent books" — target under 5 minutes for a 4-person team in the demo, with zero manual passport typing by the manager.**

Measured from the desk `awaiting_travelers` timestamp to the `released` timestamp in the ledger. Secondary: **passport-field error rate = 0** on booked orders (every document number passes the ICAO check-digit gate before it can enter an order payload).

## Announcement — the blog post before the feature

> **Waypoint now books your whole team, not a demo passenger.**
>
> Start a desk with your budget and trip, and Waypoint hands you a share link plus a private confirmation code. Drop the link in your team's group chat. Each traveler taps it, opens Waybot on Telegram, and photographs their passport — Waybot reads it, checks it, and shows them a masked card to confirm. No spreadsheets, no passport numbers pasted into chat.
>
> When everyone's verified, Waypoint pings you: *all travelers ready.* You review the names, enter your code, and the agent goes to work — searching, timing the market, and stopping to show you the exact itinerary and price for a one-tap **Approve** before it ever spends. Every money guardrail you set still holds, unwaivable.
>
> When it books, every traveler gets their flight details in the same chat. Honest by design: we tell you it's a booking reference, not an airline PNR, and we never touch your team's passport photos after we've read them.

## Screens

- `mockups/01-share-card.html` — Manager's start screen after seeding: share link + confirmation code + live traveler progress (2/4 verified…).
- `mockups/02-waybot-chat.html` — The Telegram traveler flow: deep-link open → "send your passport" → masked confirm card → done. Includes the checksum-fail typed-entry fallback.
- `mockups/03-code-release.html` — Manager reviews the **named** verified roster and enters the code to release the cycle (the security-critical review step).
- `mockups/04-approval-card.html` — G4: the priced itinerary pushed to the manager with Approve / Hold before any money moves.
- `mockups/05-travel-pack.html` — G5: the per-traveler travel pack pushed on TICKETED, with the "confirmation reference, not PNR" disclosure.

## Scope for the MVP vs. the backlog

- **MVP (the announcement above must be true):** S0 foundations + G1 (real travelers on the order) + G4 (approve/hold). This is the demoable core loop.
- **Refinement backlog, in value order:** G5 travel pack → G2 policy filter → G3 trip construction → G6 duty-of-care alerts. Each is independently shippable and independently demoable.

## Decisions locked at Gate 1

- **Contact fields (email/mobile): collected, optional.** The bot asks after the passport confirm card; a traveler can skip. Not required for the Atlas order (which needs only passport/MRZ fields) — collected for the travel-pack push and future use.
- **Future direction (explicitly OUT of hackathon scope):** replace passport-photo capture with employee SSO / directory login, so identity comes from the company's own system instead of a photo. Noted so the demo narrative can point at it; not built now.
- **Demo posture: recorded-mode for the scripted pitch (pre-seeded travelers, replay byte-safe — the gate makes zero Atlas calls), live sandbox + live Telegram bot held for judge Q&A.** Both paths must work.

## Permanently out of scope (say this to judges)

Hotels / ground transport, post-ticket rebooking or refunds (the CLI has no change/cancel/refund verb), seat assignment (module inactive on this sandbox), live visa rules, loyalty numbers (no field in the Atlas schema), real airline PNR delivery (order status returns a status code only).
