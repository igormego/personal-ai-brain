# Personal Assistant MVP

_Status: approved product direction; implementation not yet started._

## Product goal

Build one personal assistant for Igor with **one backend identity and shared durable context**, reachable through at least:

1. ChatGPT as the rich/default conversational surface;
2. WhatsApp as the low-friction mobile surface.

The product goal is not “another chatbot”. It is a conversational front door to Project OS, Personal AI Brain and selected domain systems.

The assistant should reduce coordination work. Igor should be able to say what he wants in natural language without first deciding which project, chat, database or homelab service owns the request.

Examples:

- “Where did we stop with the homelab?”
- “Save this idea for later.”
- “Find me a darker sci-fi under two hours that I have not seen.”
- “What do I need for the bolognese we discussed?”
- “Remind me tomorrow to do X.”

## North-star UX

```text
                    Igor
              ┌──────┴──────┐
              │             │
           ChatGPT       WhatsApp
              │             │
              └──────┬──────┘
                     ↓
             Assistant Gateway
                     ↓
              Intent + policy
                     ↓
        ┌────────────┼────────────┐
        ↓            ↓            ↓
    Project OS      Media       Recipes
        ↓            ↓            ↓
      GitHub       Homelab     Supabase
        │
        ├─ Calendar / reminders
        ├─ research / web
        └─ later approved capabilities
```

The two surfaces are adapters, not separate assistants. Durable user/project/domain truth remains in the authoritative systems rather than being copied into per-channel chat memory.

## Architectural principles

1. **One assistant, multiple surfaces.** ChatGPT and WhatsApp use the same backend contracts and durable state.
2. **Natural-language front door, explicit capability backend.** The model may interpret intent, but execution happens through narrow typed capabilities.
3. **Domain ownership stays intact.** The assistant does not replace Project OS, Media, Recipe Intelligence or their specialist ownership.
4. **Read before write.** Read-only capabilities land before write-capable actions in each domain.
5. **No raw infrastructure access for the assistant.** Prefer capabilities such as `media.request_movie(tmdb_id)` over direct Radarr/SSH access.
6. **Ambiguous outcomes are durable states.** No silent replay of uncertain writes.
7. **Shared context is federated, not duplicated.** Project State, media.db, Supabase and other authoritative stores remain authoritative.
8. **Low-friction UX does not weaken permissions.** WhatsApp must not become an unbounded remote-control path.
9. **Sensitive domains require a separate privacy decision.** Medical/health context is out of WhatsApp MVP scope by default.
10. **Do not build a framework before a useful vertical slice works.**

## Relationship to current Personal AI Brain

The accepted Local Bridge v0.1, durable task identity, idempotency, bounded execution, approval semantics and audit patterns are useful foundations.

However, the Local Bridge is currently a bounded **coding/Codex** path, not the Personal Assistant runtime. The new assistant must not silently repurpose `codex.submit` as a general-purpose action bus.

The immediate existing PAB milestone remains:

> Remote Transport Activation + independent external ChatGPT acceptance for the already implemented Local Bridge transport.

After that boundary is accepted, Personal Assistant work can reuse the proven transport/idempotency/security patterns while introducing its own explicit assistant capability contracts.

## MVP capability scope

### v0.1 — useful, mostly read-only assistant

Target capabilities:

- **Project OS**
  - current project/workstream status;
  - current gate / exact next action;
  - detect stale state and say so;
  - route a request to the correct specialist domain.

- **Media**
  - check whether a movie is known / watched / in library / on watchlist;
  - retrieve bounded personal-media context;
  - recommend candidates using read-only state.
  - no automatic request/download write in the first slice.

- **Recipes / cooking**
  - retrieve known recipe/cooking context;
  - find relevant Recipe Intelligence records;
  - respect temporal boundaries such as “current pantry is unknown”.
  - no autonomous promotion/edit of canonical recipe data in the first slice.

- **General assistant**
  - ordinary conversational reasoning and web research remain on the ChatGPT surface where possible rather than duplicating those capabilities locally.

### Later capabilities

Each requires a separate contract/acceptance step:

- idea/inbox capture;
- reminders and calendar writes;
- media request/reacquisition;
- Recipe Intelligence inbox writes;
- proactive notifications;
- scheduled summaries;
- richer memory/knowledge retrieval;
- other Personal AI Brain agents.

## Surface roles

### ChatGPT

Primary rich interface:

- long conversations;
- reasoning/research;
- project planning;
- multi-step tasks;
- explicit approvals;
- visual/file-heavy work.

The long-term goal is that the user can remain in a normal ChatGPT conversation and the assistant routes to PAB/domain capabilities without asking which technical subsystem to use.

### WhatsApp

Primary low-friction interface:

- quick requests;
- idea capture;
- short status questions;
- lightweight reminders;
- notifications and follow-ups;
- voice-note support later if justified.

WhatsApp is an adapter. It must authenticate the user, normalize inbound messages into the same assistant request envelope, and return bounded responses.

Provider choice is intentionally not locked yet. Candidate implementation paths include direct WhatsApp Business Platform / Cloud API or a managed gateway such as Twilio. n8n may be useful as an integration layer, but should not become the assistant’s source of truth or policy engine.

## Unified request envelope

The first implementation should define one transport-independent request shape, conceptually:

```text
assistant_request
- request_id
- user_id / trusted identity
- surface: chatgpt | whatsapp
- message
- attachments metadata (optional)
- created_at
- correlation / reply target
```

The normalized request then flows through:

```text
surface adapter
→ authentication
→ request normalization
→ intent classification
→ capability policy
→ capability execution
→ result normalization
→ surface response
```

Execution capabilities should receive structured inputs rather than raw conversational text whenever practical.

## Permission model

Initial policy direction:

```text
READ / lookup                 → autonomous when identity is trusted
safe deterministic WRITE      → later, capability-specific acceptance
externally consequential WRITE→ explicit approval unless separately accepted
DELETE / destructive action   → explicit approval
ambiguous result              → stop, persist ambiguity, no silent retry
```

The exact policy is capability-owned and must be accepted before enabling the action.

## Privacy boundary

For the MVP:

- do not send raw secrets or credentials through either surface;
- do not expose direct Radarr/Sonarr/SSH/admin access;
- minimize durable conversation storage;
- use authoritative stores instead of copying full personal datasets into an assistant database;
- treat WhatsApp as a separate privacy boundary;
- exclude medical/health workflows from the WhatsApp MVP unless Igor explicitly approves a later design.

## First useful vertical slice

Do **not** start by implementing all domains.

Recommended first slice after the current Remote Transport acceptance:

> **Personal Assistant Gateway v0.1 — one normalized request contract, one trusted ChatGPT-side request path, and one read-only Project OS capability.**

Acceptance example:

1. Igor asks from the external conversational surface: “Where are we with Recipe Intelligence?”
2. The request is authenticated and normalized.
3. The router selects a read-only Project OS capability.
4. The capability reads current authoritative state.
5. Stale/conflicting state is surfaced rather than invented away.
6. The correlated answer returns to the originating conversation.
7. Duplicate delivery produces no duplicate execution.
8. Restart/recovery preserves request truth.

This proves the assistant architecture without adding another LLM framework, database or broad permission surface.

## WhatsApp slice

After the gateway/request contract is accepted:

1. perform a narrow WhatsApp provider feasibility spike;
2. receive one inbound text from Igor;
3. map the trusted sender to the assistant identity;
4. submit the same normalized request envelope;
5. execute the same read-only Project OS capability;
6. return the bounded response to WhatsApp;
7. verify duplicates, retry behavior, logs and privacy;
8. keep all write capabilities disabled.

Success means the **same request contract and capability** work from ChatGPT and WhatsApp. Do not create a parallel WhatsApp-specific brain.

## Non-goals for MVP

- general autonomous multi-agent orchestration;
- unrestricted filesystem/SSH access;
- autonomous GitHub publication/deployment;
- a new vector database just to “have memory”;
- replacing existing Project State/domain databases;
- exposing every domain at once;
- medical assistant over WhatsApp;
- proactive behavior before inbound request/response is reliable.

## Success criteria

The MVP is successful when Igor can naturally ask a useful personal-context question from ChatGPT or WhatsApp and receive a trustworthy answer from the same backend capability without knowing which project/service stores the truth.

The product should reduce this question:

> “Which chat/system do I need to use?”

toward:

> “I just tell my assistant what I want.”

## Ownership and next step

This document is a product/design handoff, not accepted implementation state.

Recommended specialist owner:

**Personal Assistant — ChatGPT & WhatsApp**

That specialist should use the Personal AI Brain repository and current PAB Project State as its technical foundation, while respecting the existing Agent Platform owner’s security and transport boundaries.

First specialist task:

> Read current PAB state and the accepted Local Bridge/remote-transport documentation. Close or explicitly defer the current external ChatGPT transport acceptance gate, then design the smallest Personal Assistant Gateway v0.1 slice above. Do not introduce WhatsApp implementation until the transport-independent request/capability contract is clear.
