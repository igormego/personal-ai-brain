# AGENTS.md

## Project mission

Build a local-first personal multi-agent platform incrementally, with explicit state, tool boundaries and verifiable autonomy.

## Instruction refresh

Before any task that will write to GitHub, read the current `AGENTS.md` from the repository default branch. Do not rely on an older chat copy of these rules when the repository version is available.

## Token and context efficiency

Optimize for useful verified work per token. Do not trade away correctness, safety, or required freshness.

- Reuse verified durable state/evidence when still sufficient; do not re-check the same stable fact by default.
- Re-verify only when freshness matters, evidence conflicts, state is stale/unknown, the action is high-risk, implementation may have changed, or Igor asks.
- Avoid duplicate documentation; update the authoritative artifact instead of creating near-copies.
- Prefer concise deltas, exact next actions, and links/references over repeating full history.
- Retrieve narrowly and avoid repeated full-file reads or broad scans when a known range/result is enough.
- Stop research once evidence is sufficient; add more only when it materially improves confidence or coverage.
- Prefer deterministic reusable operations, cached/structured artifacts, and existing Skills where they safely reduce repeated LLM work.

### Risk-based execution and verification

- Classify the actual change, not the entire project: routine text/docs/metadata, ordinary tested code, or security/authorization/runtime/data/deployment. Scale review and preflight accordingly.
- For routine, reversible changes, inspect the focused diff and run only applicable mandatory checks; do not launch a full environment/security audit when nothing relevant changed.
- For ordinary code, run focused tests plus repository-required CI. For security boundaries, credentials, remote execution, deployments or destructive changes, retain the full relevant preflight and regression evidence.
- Fixed sandbox validators, required CI, permission boundaries and explicit approval gates are never optional merely to save tokens. Reuse previously verified unchanged evidence rather than re-proving it in every task.
- Continue a Codex task for the same bounded change; use a fresh task for a genuinely unrelated milestone instead of accumulating irrelevant context. Keep durable state in the authoritative project files.
- Prefer autonomous execution of already authorized, low-risk steps, and concise result/problem/next-action reporting. Avoid ceremonial planning, repeated confirmation and full log dumps unless they materially help.

If extra retrieval, verification, or documentation will not materially change the answer, reduce meaningful risk, or create reusable value, skip it.

## Temporal context awareness

When reusing older conversation context, Project State, notes, tool output, or prior plans, account for elapsed time instead of treating every past statement as current.

- Treat time-bound or ephemeral state as potentially stale: today's meal, current pantry/fridge contents, open food or drink, current plans, in-progress tasks, mood, weather, temporary availability, live service status, and similar "now" facts.
- Compare timestamps and relative wording such as `today`, `tomorrow`, `current`, and `now`. Do not silently carry a temporary state forward just because it appears in context.
- Durable facts and decisions may be reused until superseded. Ephemeral facts should be refreshed, qualified as last-known, or re-confirmed when currentness materially affects correctness.
- Prefer wording such as `last known on <date>` when useful rather than presenting stale state as present reality.
- Do not create granular memory, logging, or documentation solely to implement this rule. This is primarily an interpretation rule, not a requirement to store every transient event.

This policy applies across chats, agents, Project State reading, research, planning, and operations.

## Current implementation priority

Core Agent v0.1 is read-only.

Do not add write-capable GitHub actions, LLM frameworks, databases, queues or orchestration libraries unless the current milestone explicitly requires them.

## Coding principles

- Python 3.11+.
- Prefer the standard library for simple infrastructure.
- Keep external dependencies minimal.
- Keep domain logic separate from adapters.
- Deterministic validation should remain deterministic.
- Every external action must have an explicit permission boundary before it is enabled.
- Never commit tokens, API keys or private secrets.
- Tests should cover state/attention edge cases before autonomy is expanded.

## Git workflow

Use a risk-based workflow. A branch is a safety tool, not a mandatory ceremony.

### Routine Sync Lane — direct to `main` allowed

Direct commits to the default branch are allowed for small, deterministic, easily reversible synchronization of already verified reality, including:

- updating `.project/state.yaml` after a meaningful verified milestone;
- synchronizing README/roadmap/current-state documentation to implementation that is already verified;
- small wording, link, metadata, or documentation corrections;
- low-risk generated/reporting metadata that does not change agent behavior or permissions.

Routine Sync Lane must not be used for code behavior changes, dependencies, CI behavior, tool permissions, GitHub write capability, agent autonomy, deployment configuration, security-sensitive changes, major architecture decisions, or ambiguous changes.

### Change / Review Lane — branch + PR required

Use a dedicated branch and pull request for changes with meaningful implementation, security, autonomy, or review risk, including:

- Python or other executable code changes;
- dependency or build/CI changes;
- new tool adapters/integrations;
- write-capable actions or expanded permissions;
- agent autonomy/policy changes;
- Docker/deployment/runtime changes;
- database/schema/memory infrastructure;
- major architecture changes, broad refactors, deletes or renames.

Keep unrelated changes out of the branch. Run applicable tests/checks before merge.

### Branch lifecycle ownership

If you create a branch, you own its lifecycle.

- If Igor has already approved the intended change, do not ask for a second approval merely to merge the resulting PR.
- After applicable checks pass and the implemented scope still matches the approved change, merge the PR as part of completing the task.
- Prefer squash merge unless there is a reason to preserve individual commits.
- Delete the merged branch when the available GitHub tooling supports branch deletion.
- If branch deletion is unavailable, explicitly report the leftover merged branch instead of silently leaving cleanup to Igor.
- Do not merge if checks fail, the scope materially changed, or new consequential risk appeared; surface that instead.
- Use draft PRs only for genuinely unfinished work, not by default.

## Project OS

This repository owns `.project/state.yaml`.

Meaningful verified milestones should update that state as part of milestone closure. Ordinary discussion or speculative design does not require a state write.

## Architecture direction

The future system may include an orchestrator, agent registry, event bus, memory/RAG and multiple domain agents. Those are target layers, not permission to introduce premature framework complexity into the current milestone.
