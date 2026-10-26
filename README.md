# Personal AI Brain

A local-first personal agent platform for coordinating Igor's projects, tools, knowledge and future autonomous workflows.

The long-term target is a real multi-agent system. The current implementation stays deliberately conservative: read-only agents consume durable Project OS state, validate it deterministically and expose structured outputs before any autonomous write path is introduced.

## Why this exists

Project work happens in specialist chats and repositories. Project OS gives each project/workstream a durable YAML state. Personal AI Brain is the automation layer that will increasingly read, coordinate and act on that state without Igor manually carrying deltas between chats.

Current flow:

```text
specialist chat
    ↓
owned Project / Workstream State
    ↓
GitHub
    ↓
Core Agent
    ├─ schema + freshness validation
    ├─ integrity/conflict checks
    ├─ rollup proposals
    └─ machine-readable snapshot
    ↓
cross-project health / gates / next actions
```

Future flow:

```text
                       ┌─ Repo Steward
                       ├─ Career Evidence Agent
Project State → Core ──┼─ Homelab Agent
                       ├─ Recipe Agent
                       ├─ Trading Agent
                       └─ Memory / Knowledge Agent
                              ↓
                       orchestrated actions
```

## Personal Assistant direction

The first user-facing product direction is a single Personal Assistant reachable through ChatGPT and, later, WhatsApp, with shared backend capabilities and durable domain state rather than separate channel-specific bots. See [Personal Assistant MVP](docs/personal-assistant-mvp.md).

## Core Agent

Implemented:

- read-only GitHub Contents API adapter;
- discovery through `personal-project-brain/core/project-map.yaml`;
- support for project rollups and specialist workstream states;
- deterministic validation of missing, stale and unsynchronized state;
- Project State v0.2 schema/type/storage validation;
- attention-aware freshness policy with per-project override support;
- multi-workstream rollup integrity/conflict detection;
- deterministic read-only rollup proposals;
- machine-readable JSON/YAML Core snapshots;
- distinction between active, background, deferred, parked and future work;
- Markdown Core report generation;
- CLI;
- automated tests and CI;
- persistent Docker/Compose runtime with manual jobs, durable logs and separate liveness/source health.

The rollup proposal layer intentionally refuses to invent a shared narrative when multiple blocking workstreams are active. In that case it proposes only unambiguous structural/freshness fields and marks semantic synthesis as required.

Freshness is also intentionally attention-aware: active work is checked more aggressively, while deferred/parked/future work does not generate stale-warning noise merely because it is old.

Explicitly **not** implemented yet:

- GitHub writes from Core Agent;
- LLM planning/synthesis;
- autonomous project actions;
- scheduling and notifications;
- database/vector memory;
- multi-agent orchestration.

## Agent Contract + Registry

Agent manifests live under `agents/` and define responsibility, inputs/outputs, tool permissions, readable/writable resources, approval boundaries and future event contracts.

Core Agent is the first registered agent and remains strictly `read_only`.

```bash
personal-ai-brain agents validate
personal-ai-brain agents list
personal-ai-brain agents show core-agent
```

See [Agent Contract v0.1](docs/agent-contract.md).

## Project State quality

Built-in freshness defaults match [`config/freshness-policy.yaml`](config/freshness-policy.yaml).

Use an explicit policy file when desired:

```bash
personal-ai-brain core-report \
  --freshness-policy config/freshness-policy.yaml

personal-ai-brain core-snapshot \
  --freshness-policy config/freshness-policy.yaml \
  --format json
```

Project Map entries may override freshness with `freshness_days`, including `null` to disable freshness checking for a specific entry.

See [Project State Quality v0.2](docs/state-quality.md).

## Quick start

Requirements:

- Python 3.11+
- a GitHub token with read access to `Az1mutt/personal-project-brain`

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

export GITHUB_TOKEN="..."
personal-ai-brain core-report
```

Generate machine-readable state for future automation/orchestration:

```bash
personal-ai-brain core-snapshot --format json --output reports/core.json
personal-ai-brain core-snapshot --format yaml --output reports/core.yaml
```

Inspect deterministic rollup proposals without writing them:

```bash
personal-ai-brain rollup-proposals
personal-ai-brain rollup-proposals --format json
```

Run tests:

```bash
pytest -q
```

Do not commit tokens or other secrets.

## Homelab runtime

The minimum v0.3 read-only runtime runs detached with Docker Compose. It uses the
existing token as a file secret and retains private results outside the checkout.

```sh
sh scripts/runtime.sh up
sh scripts/runtime.sh core-report
sh scripts/runtime.sh rollup-proposals
sh scripts/runtime.sh core-snapshot
sh scripts/runtime.sh health
sh scripts/runtime.sh logs
```

See [Homelab runtime operations and health contract](docs/homelab-runtime.md).
Project State findings remain visible without marking the runtime dead.

## Read-only control bridge

The v0.4A worker accepts strict JSON requests through private GitHub Issues and
returns bounded, sanitized results. Its capability registry is independent of
GitHub. Core remains read-only; the separate transport can only post issue results
and close requests. See [control protocol and operations](docs/control-bridge.md).
ChatGPT-originated end-to-end acceptance is tracked separately in the handoff.

## Watched Event Ledger

Watched Event Ledger v0.1 adds the additive SQLite event foundation, deterministic
deduplication and delivery state machine, plus a pure read-only Plex/Tautulli
observation normalizer. It contains no Plex or Trakt write client and does not
copy the completed historical migration. See the
[ledger contract](docs/watched-event-ledger.md).

Run one bounded restart-safe Tautulli reconciliation batch for an already
bootstrapped private runtime:

```sh
personal-ai-brain watched-reconcile --help
```

Scheduling, secrets and the first accepted cursor watermark remain deployment
concerns; the command itself performs no Plex or Trakt write.

## Repository structure

```text
.project/                         Project OS state for this project
agents/                           declarative agent contracts
config/                           deterministic runtime/policy configuration
src/personal_ai_brain/            Python package
src/personal_ai_brain/sql/        packaged additive SQLite migrations
tests/                            deterministic unit tests
docs/vision.md                    long-term destination
docs/architecture.md              architecture and boundaries
docs/agent-contract.md            agent permission/contract model
docs/state-quality.md             Project State schema/freshness rules
docs/watched-event-ledger.md      watched-event and delivery contracts
docs/roadmap.md                   staged implementation plan
database/                         reserved for future memory/state services
docker/                           minimal read-only runtime image
compose.yaml                      detached Homelab service
scripts/runtime.sh                manual operations
```

## Architecture philosophy

1. **Manual before autonomous.**
2. **Read before write.**
3. **Deterministic rules where deterministic rules are enough.**
4. **Use LLM judgment only where semantic reasoning adds value.**
5. **Every agent owns a narrow responsibility.**
6. **Durable state lives outside chat memory.**
7. **Agents should coordinate through explicit state/events, not hidden assumptions.**
8. **Capability and permission are separate: a tool existing does not imply an agent may use it.**
9. **A proposal is not permission to write.**
10. **Freshness is a policy signal, not a claim that project content is wrong.**
11. **The sci-fi version is allowed — after every autonomy boundary has earned its way in.**

## Memory / RAG

The repository originally started as a local knowledge/RAG concept using PostgreSQL, pgvector and Ollama. That idea is retained, but repositioned as a future **Memory & Knowledge subsystem** of the broader agent platform rather than the platform itself.
