# Alibaba Agentic Hackathon: Consolidated Guide & Insights

## 1. The Vision: The "Agentic Era"

Alibaba Cloud is emphasizing a major paradigm shift in AI development for this hackathon.

*   **Timeline of AI Evolution:** AI has progressed from *Pre-training* (Nov 2022) to *Reasoning* (Dec 2024) to the current *Agentic AI* era (Nov 2025).
*   **Capability Leaps:** Growth is no longer linear. Models are making leaps forward, and agents are embracing self-evolution, driving their own capability growth engine.
*   **Outcomes Delivered:** Agents are moving beyond just completing standard workflows; the new measure of value is creating what was previously impossible.
*   **Human-Machine Organization:** The dynamic is shifting from basic human-machine interaction to individuals becoming "super-individuals" who command fleets of agents. Organizations will become a "human-Agent fabric."

## 2. The Problem: Current AI Coding Traps & Failure Points

The hackathon highlights why current, ad-hoc AI coding workflows ("wild coding") often fail in enterprise environments.

### The "One-Shot Prototype" Trap
The current development loop (Ideation → Design → Generation → Testing → Publishing) often stops abruptly ("and then nothing"), leading to dead-end projects.
*   **What This Pass Never Does:** No second pass (nothing goes back to make v2), no spec/plan (nothing written down defining success), nothing is kept (learned context is lost), and nobody can keep it running (cannot pick up, extend, or fix 3 weeks later).
*   **The Cost:** A build that only runs in a demo scores 1 on "Operating Scale", forcing "Cost Controllability" to 0. Two out of three Feasibility dimensions are lost in one stroke.

### Working Locally ≠ Release Ready
Prototypes may work perfectly in standalone local environments ("happy path"), but they lack crucial system context (architecture, contracts, dependencies). This leads to failures at integration (interface mismatches, broken flows, validation gaps).

### Agent Loop Failure Points
These three failures do not throw an error; logs look clean, but outcomes are wrong. Human checkpoints and acceptance tests are required to catch them.
1.  **Infinite Loop:** Agents lack termination rules and re-plan forever, burning budget on unreachable goals. 
    *   *Fix:* Give the agent a step budget and an explicit way to give up.
2.  **Stale Data:** Agents act on cached/old data (e.g., booking a seat that sold two minutes ago).
    *   *Fix:* Force the agent to re-read the world immediately before every write.
3.  **False Success:** The API returns a 200 OK code, so the agent marks the task as done, but nobody checked the real-world outcome.
    *   *Fix:* Assert real-world outcomes.

## 3. The Solution: Spec-Driven Development & Qoder

To solve these pain points, the methodology shifts to **"Specify before building. Verify before releasing."**

### Spec-Driven Development Lifecycle
1.  **PRD (01):** Define Business Need, Scope, and User Outcome.
2.  **Specify (02):** Detail User Stories, Acceptance Criteria, and System Constraints.
3.  **Plan (03):** Outline Architecture, Interfaces, and Task Breakdown.
4.  **Implement (04):** Utilize Coding Agents, Tool calls, and Checkpoints.
5.  **Validate (05):** Run Tests, Security Checks, and verify against Release Criteria.

*   **Engineering Controls Across the Lifecycle:**
    *   *Project Context:* Codebase, architecture, coding standards, prior decisions.
    *   *Tool Access & Approvals:* MCP, API, CLI, permissions, human approval.
    *   *Automated Checks:* Acceptance tests, security testing, release criteria.

### Alibaba Cloud "Qoder" Platform
Qoder operates under **"Two Modes, One Workspace"**:
*   **Editor Mode:** Human-led, standard daily coding with next-edit suggestions and agent chat.
*   **Quest Mode:** Agent-first window for end-to-end delivery where developers define the goal and review results.
    *   **What a Quest is:** Spec-first (requirements & design before code), Experts mode (parallel multi-agent), and Durable tasks (create, pause, resume, fork).

### Key Qoder Features & Workflows
*   **Spec Mode ("Fixes the path"):** "Propose a plan first, then proceed after confirmation." Best for new features and well-defined requirements; the plan becomes review evidence. Spec-driven runs provide closed-loop evidence scoring 3–4 on AI Development.
*   **Goal Mode ("Fixes the destination"):** "Set a goal to work toward until completion." The agent iterates and judges itself after every round (up to 10 turns by default). Built for tasks like "raise test coverage above 80%" to buy iteration overnight without manual supervision.
*   **Repo Wiki:** Acts as a "Second Brain" for team or One-Man Company (OPC). AI summarizes code changes to maintain one shared understanding across the team.
    *   *Citation:* Generated wiki can be referenced by agents as a source.
    *   *Auto Export:* Written into `.qoder/repowiki` inside the project so it travels with the repo.
    *   *Auto Update:* Regenerates as codebase changes instead of going stale in a doc folder.
*   **Better Harness:** Project-level opt-in where LLMs audit agent conversations to grade setup quality:
    *   *Task Understanding:* Does agent know what done means before it starts?
    *   *Controlled Execution:* Are actions bounded, reversible, and reviewable?
    *   *Change Validation:* Can a change be proven correct with evidence?
    *   *Reliable Delivery:* Does same input produce same result tomorrow?
    *   *Learning Capture:* Does what you learned survive into next run?
    *   *Reflection Tip:* Use AI to perform reflection at end-of-day (e.g., around 10 PM) to avoid blowing context windows during active coding.

## 4. Hackathon Logistics: The Atlas API & Travel Use Cases

### The Atlas Tools
Atlas connects 140+ airlines to travel builders through a single API layer, running against the Atlas Sandbox (real routes, fares, and schedules).
*   **Airline Capabilities in Scope:** Fare search, Verify & book, Payment & ancillaries, Post-booking.
*   **Two Integration Paths:**
    1.  **Path 01 (Integrate via ATRIP):** Standard platform integration (Register & sign in → Generate Sandbox credentials → Configure and test with API docs).
    2.  **Path 02 (Integrate via Atlas Skill):** Fast Agent integration via open-source (Open GitHub repo → Follow README to set up Skill → Connect and call Sandbox).

### Hackathon Objectives
1.  **Find next high-value use cases:** Go beyond generic chat to identify travel problems where agentic decision-making creates materially better outcomes.
2.  **Prove them on real travel data:** Test ideas against real routes, fares, and schedules in Atlas Sandbox.
3.  **Learn from builders:** Observe how developers combine reasoning, tools, controls, and UX.
4.  **Create a path beyond WIT Singapore:** Surface prototypes that could become pilots, partnerships, or product capabilities with real industry relevance.

### Strategic Advice & Travel Opportunities
*   **The "Moat" Has Changed:** Investors at WIT recognize that "product" is no longer a moat in itself. Builders must identify what company attributes offer enduring potential.
*   **Open-Source Leverage:** Participants are encouraged to build on top of existing open-source ideas and GitHub repositories. You are not restricted to closed or proprietary concepts.
*   **Production Readiness:** While Alibaba Cloud deployment isn't strictly forced, code should follow system development best practices and avoid outdated/fixed local dependencies.
*   **Industry Disruption:**
    *   *Hospitality Tech / B2B:* Hotels want agentic AI to build direct customer relationships and loyalty, bypassing OTAs.
    *   *OTAs:* Undergoing self-disruption to avoid being disrupted by newcomers.
    *   *Sports & Activities:* Highlighted as the "sexiest part of travel."

## 5. Judging Criteria & Benchmark

Scored on a **3-Minute Demo** across **10 Sub-Dimensions** totaling **40 Points** (no conversion).

### The 40-Point Breakdown
1.  **Innovation (30%, Max 12 Points):**
    *   Business / Form (0–4)
    *   Scenario / Experience (0–4)
    *   Operations / Cost (0–4)
2.  **Feasibility (30%, Max 12 Points):**
    *   Operating Scale (0–4)
    *   Compliance & Safety (0–4)
    *   Cost Controllability (0–4)
3.  **Use of Qoder (20%, Capped at 8 Points):**
    *   AI Development (0–4)
    *   Agent Technology (0–6)
    *   *Eligibility Rule:* Core functionality **80%+ built with Qoder** — otherwise this category scores 0.
4.  **Demo (20%, Max 8 Points):**
    *   Completeness (0–4)
    *   Presentation Quality (0–4)
    *   *Scoring Tiers:* Both dimensions are scored strictly in 4 / 2 / 0 tiers.

### The AI Multiplier (Innovation Only)
Applies only when a base dimension scores 2 or more. Innovation total stays capped at 12. A base score below 2 blocks the multiplier entirely ("technique cannot rescue a weak idea").
*   **x2 (Impossible without AI):** An agent judging escrow release or a partial refund on its own.
*   **x1.5 / x1:** Standard/appropriate application of AI agents.
*   **x0.5 (Misuse of AI):** Using AI for the sake of using AI (e.g., forcefully using an LLM to run a SQL query, or free-form generation inside a funds-settlement step).

### The Level-4 Benchmark (Travel Example)
Scored four ways in the same industry:
*   **Level 1 (Common practice):** Detect a delay, suggest alternative flights (what most teams will submit).
*   **Level 2 (Interesting touch):** Generate a travel plan from someone's social media posts.
*   **Level 3 (Notable advance):** A firewall for hotel inventory — reconcile room state across channels and fix discrepancy before double booking happens.
*   **Level 4 (Major breakthrough):** Treat itinerary as a dependency graph — autonomously re-plan every downstream leg and settle fare difference in real time.
