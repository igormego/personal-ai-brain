# Roadmap

The roadmap deliberately grows autonomy in layers. The long-term destination is multi-agent; the implementation order is evidence-driven.

## v0.1 — Core Agent read path

Goal: remove manual Core consolidation.

Implemented and CI-tested:

- Python package and CLI
- read-only GitHub adapter
- Project OS project/workstream discovery
- YAML validation
- stale/missing/sync findings
- attention-aware Core report
- unit tests and CI

The first live run against the private Project OS remains a runtime acceptance check rather than a blocker for continued platform development.

## v0.2 — Platform contracts and state integrity

Goal: define a stable multi-agent platform boundary before adding runtime orchestration.

### v0.2a — Agent Contract + Registry

Implemented and merged:

- declarative agent contract schema
- explicit responsibility, inputs and outputs
- tool permissions and resource boundaries
- approval policy declaration
- future event consume/emit surface
- local YAML agent registry
- Core Agent registered as the first `read_only` agent
- deterministic contract tests and CLI inspection

### v0.2b — Project rollups and state integrity

Implemented:

- validate multi-workstream project rollup shape
- detect root/workstream path drift against the Project Map
- detect active workstreams missing from `rollup_policy.generated_from`
- detect root rollups older than active child workstreams
- detect `synced` rollups sitting above unsynchronized active child states
- detect hard status contradictions such as a completed project with an active workstream
- feed integrity findings into the normal `core-report`
- deterministic read-only rollup proposals
- refuse deterministic narrative synthesis when multiple blocking workstreams are active
- machine-readable Core snapshot output in JSON/YAML
- CLI output for rollup proposals and machine snapshots
- Project State v0.2 schema/type/storage validation
- attention-aware freshness policy
- freshness disablement for intentionally deferred/parked/future work
- per-project/workstream `freshness_days` override support
- configurable freshness-policy YAML

**Implementation exit gate:** reached once CI passes for the final schema/freshness slice.

The remaining live validation against the private Project OS is intentionally carried into v0.3 as the first runtime acceptance task. This avoids blocking the platform foundation on access to Igor's personal machine while still requiring a real-data run before persistent automation is considered proven.

## v0.3 — Persistent Homelab runtime — manual slice verified

Goal: move the proven read-only platform from interactive development into a reliable local runtime.

Original scope (manual runtime, secrets, live reads, logs and health are now verified;
periodic Core scheduling, notifications and n8n remain deferred):

- clone/install on the Ubuntu Homelab host
- read-only GitHub token/secrets handling
- first live `core-report`, `core-snapshot` and rollup-proposal run against the private Project OS
- fix any real-data schema/policy mismatches discovered by that run
- Docker packaging
- persistent read-only service/container shape
- scheduled/manual trigger
- run logs and retention
- health checks
- notification of meaningful findings only
- evaluate n8n as orchestration/event glue after the basic runtime is reliable

**Exit gate:** Core Agent runs reliably without an interactive ChatGPT session and produces useful, low-noise state output from real Project OS data.

## v0.4A — Universal read-only Homelab control bridge

Current approved slice: a transport-independent capability registry with strict
request validation, private GitHub Issues as the first adapter, persistent polling,
local audit and deduplication, and six bounded read-only capabilities. No source
writes, shell execution, media handlers or integration framework is added.

Homelab implementation and internal transport verification are distinct from the
final ChatGPT connector acceptance. The rolling handoff records both explicitly.
See [control bridge](control-bridge.md). Repo Steward remains a separate future step.

Verified maintenance on 2026-10-23: Core credential rotated; all four fresh Core
capabilities completed. The existing Issues adapter uses the canonical owner with
redirects still denied; one internal read-only Issue roundtrip completed. Core and
transport remained healthy in the 2026-10-25 post-expiry check. See the
[credential inventory](credential-inventory.md) and [control bridge](control-bridge.md).
Remote Transport Activation into the Windows Local Bridge and external ChatGPT
acceptance remain a separate pending gate.
## Future — Repo Steward

First write-capable specialist agent.

- inspect documentation after verified milestones
- propose README/architecture/roadmap/state changes
- open PRs instead of directly changing main
- approval boundaries and audit trail

**Exit gate:** safe repository maintenance through reviewable PRs.

## v0.5 — Career Evidence Agent

- consume verified portfolio signals
- map evidence to CV/LinkedIn/interview stories
- distinguish evidence from inference
- draft updates without inventing impact

## v0.6 — Domain agents

Introduce only where real repetitive workflows exist.

Candidates:

- Homelab / Media Agent
- Recipe Ingestion / Curation Agent
- Trading Intelligence Agent

Each gets its own contract, tools and permission envelope.

## v0.7 — Events and orchestration

- event schema transport
- task/event queue if justified by volume
- routing rules
- retries/idempotency
- cross-agent handoffs
- policy engine
- orchestrator/router that consumes the Agent Registry

Example:

```text
Recipe milestone
→ state update
→ Core refresh
→ Repo Steward docs check
→ Career Agent portfolio evaluation
```

## v1 — Multi-agent Personal AI Brain

The sci-fi version, earned rather than faked:

- orchestrator/router
- multiple specialist agents
- durable memory
- local event-driven runtime
- tool ecosystem
- approval/policy framework
- observability
- selective autonomous actions
- conversational control surface

## Memory & Knowledge track

PostgreSQL, pgvector, embeddings, Ollama/local models and semantic search remain a parallel future track. Add them when agents actually need long-term knowledge retrieval; do not block operational agents on RAG infrastructure.
