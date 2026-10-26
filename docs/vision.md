# Vision

Personal AI Brain is a local-first personal multi-agent platform.

Its purpose is not merely to answer questions over notes. It should become an operational layer that understands durable project state, coordinates specialist agents, uses tools safely, maintains useful memory, and helps execute Igor's real workflows.

## Two complementary products — decision recorded 2026-10-26

We are intentionally building **two related but distinct products**, not choosing
one at the expense of the other:

1. **Personal Assistant (the everyday product).** One natural-language entry
   point across ChatGPT and, later, WhatsApp. It helps Igor plan his day, capture
   ideas, resume hobbies, use current project context and delegate bounded work
   through approved capabilities. Its success is measured by useful outcomes,
   reduced coordination and less manual context-switching. Product direction:
   [Personal Assistant MVP](personal-assistant-mvp.md).
2. **Personal AI Brain / Agent Platform (the product-building system).** Project
   OS, specialist ownership, reusable capability contracts, safe tool access,
   agent execution paths, durable state, permissions, tests, audit and deployment
   gates. It is valuable **in its own right**: it enables Igor to build, evaluate,
   extend and maintain the Personal Assistant and other project agents without
   manually managing every integration and handoff.

These tracks are complementary: the Assistant requests an approved ability; the
platform and relevant specialist own its implementation and safeguards. An example
of the **future desired workflow**, not an already enabled autonomous feature:
Igor asks for gaming-database recommendations; the platform identifies the gaming
specialist, scopes the integration, prepares/tests a bounded capability, obtains
any required approval, verifies a controlled deployment and only then makes the
capability available through the Assistant. Specialist chats can remain human work
surfaces; they are not presumed externally callable APIs.

**Engineering-value guardrail:** Each implementation milestone should deliver
a demonstrably useful capability, remove a concrete blocker/risk, or enable an
identified reusable platform function with an explicit consumer and verifiable
acceptance. Avoid creating infrastructure merely because a new abstraction is
interesting. At the same time, **exploration and learning are legitimate goals**
and need not immediately become deliverables, quotas or tickets. Keep separate
labels for exploration, approved work, tested code and deployed capability.

**Boundary:** This clarifies product ownership and prioritization; it does not
merge the two tracks, create a new autonomous agent/workstream, expand permissions,
authorize automatic deployment or supersede current milestone gates.

## End-state

The intended mature system contains specialized agents with explicit responsibilities, for example:

- **Core Agent** — cross-project state, priorities, blockers and dependencies;
- **Repo Steward** — documentation/state consistency and safe repository maintenance;
- **Career Evidence Agent** — converts verified project milestones into career/portfolio signals;
- **Homelab Agent** — observes and later operates allowed infrastructure/media workflows;
- **Recipe Agent** — ingestion, classification, review and recommendation workflows;
- **Trading Intelligence Agent** — market-data, alerts, risk and explainable plan support;
- **Memory & Knowledge Agent** — structured knowledge, retrieval and long-term contextual memory.

A later orchestrator should decide which specialist agent/tool is appropriate for a task and coordinate multi-step workflows.

## Human role

Igor remains the owner of goals, risk tolerance and high-impact decisions.

The platform should remove coordination overhead, repeated checking and manual information transport — not hide consequential actions.

Autonomy increases by demonstrated reliability:

```text
read
→ analyze
→ recommend
→ draft
→ low-risk write
→ constrained action
→ coordinated multi-agent execution
```

## Local-first

The preferred runtime is Igor's Homelab. Cloud models/APIs may provide intelligence, but orchestration, state, logs, policies and sensitive integrations should remain controllable locally where practical.

## Memory

Structured data, documents, embeddings and semantic retrieval remain part of the vision. They form a Memory & Knowledge subsystem that agents can query; they are not a substitute for explicit Project State or operational truth.

## Portable distribution (future goal; not an active implementation gate)

The platform Igor is building should eventually be useful to **another person without six months of custom engineering**. The first reference user archetype is someone who owns **only a normal laptop** (Igor's partner is a plausible first pilot), has no always-on server, and should not need to learn Docker, Python, SSH or agent architecture to use a personal assistant.

The portable distribution must not assume Igor's homelab, GitHub repositories, databases, media collection, credentials, or existing personal state. These belong to Igor's instance, not to the product. User-specific data, preferences, authorization and integrations must be separable from reusable platform code/configuration.

The desired user experience is simple onboarding, one assistant identity, conversational access, optional modules and an understandable update/backup/exit path. The user should be able to start with a small useful assistant and add capabilities later, rather than provision a complete engineering lab.

Candidate operating profiles to evaluate when portability becomes an active gate:

- **Laptop-only:** the smallest feasible local install, or a thin conversational client with an explicitly selected remote inference/backend service; no dedicated GPU assumed. A laptop that is asleep/offline cannot run local background workflows.
- **Privately hosted:** the user's own always-on PC/NAS/server when they have one; optional, not a universal prerequisite.
- **Shared host / managed backend:** a separate, explicitly consented instance on another person's server or a hosted service. Igor's server might host a pilot for his partner, but must not become a hidden dependency. Separate user data, credentials, approvals, logs, resource budgets and administration rights are required. Being on the same Wi-Fi is not an authorization boundary.

A first portability acceptance should demonstrate that a non-technical laptop-only user can onboard with **their own isolated profile and data**, use one genuinely useful capability, and recover from normal restarts without custom one-off Codex edits. Test on a clean environment and record actual setup friction and operating costs. This is a target, not something already implemented.

This direction does **not** activate a new workstream, commit the project to SaaS/commercialization, or displace current Personal Assistant and remote transport milestones. The architecture and hosting tradeoffs remain open until a concrete pilot is ready.

## Exploration mode

Design discussions and speculative infrastructure ideas are valuable even when Igor is away from the homelab, in bed, or unable to run Codex. They need not immediately result in code, a new workstream, or a scheduled task. Do not impose an arbitrary ratio or quota of infrastructure milestones to user-facing features. Keep exploration open, distinguish **idea / candidate / approved work / verified implementation**, and promote only ideas that Igor actively chooses to build. Prefer focused exploration over unsolicited elaborate blueprints.
