# Remote Codex v0.2 — Local installation and acceptance handoff

**Status:** source available for review; NOT installed, activated or independently
accepted. [Issue #41](https://github.com/igormego/personal-ai-brain/issues/41)
is the authoritative execution gate. v0.1 remains live/accepted and must stay
untouched until v0.2 acceptance is complete.

## Why this is a separate opt-in worker

The implementation uses **another exact Issues title** (`pab-codex/v0.2`),
private delivery directory and durable task ledger. Existing
`PersonalAIBrain-CodexTransport-v01` ignores this title and retains its
two-file completion validator and original private audit.

The v0.2 worker reuses the private outbound-only Issues mailbox. The token
still cannot read/write repository Contents; the new worker receives **no new
GitHub scope**. A GitHub Issue is an untrusted task request, not permission
to choose a local checkout, test command, credentials, model, server or
sandbox. Request `requested_by` is not an authentication mechanism.

## Fixed initial contract

Create a private issue only **after the operator has marked the v0.2 worker READY
for independent external acceptance**. Title: `pab-codex/v0.2`.
Body is raw JSON (no Markdown fences):

```json
{
  "transport_version": "pab-codex/v0.2",
  "operation": "codex.submit",
  "request_id": "REPLACE_WITH_NEW_UUID_V4",
  "task_id": "REPLACE_WITH_NEW_UUID_V4",
  "project_id": "personal-ai-brain",
  "execution_profile": "sandboxed-repo-dev",
  "goal": "Create a bounded ordinary application feature with offline tests",
  "acceptance_criteria": "Complete the requested change within the trusted code scope and pass the pinned offline suite",
  "base_sha": "REPLACE_WITH_CURRENT_TRUSTED_LOCAL_MAIN_SHA"
}
```

Accepted initial projects: `personal-ai-brain` and
`homelab-infrastructure`. A request may select ONLY one fixed project ID.
The installed operator policy maps it to
`C:\Projects\RemoteCodex-v02\sources\personal-ai-brain` or
`C:\Projects\RemoteCodex-v02\sources\homelab-infrastructure` and a fixed offline test profile;
neither checkout nor test command is a request field.

Fresh lowercase UUIDv4 IDs and the *exact* 40-character lowercase canonical
local `main` HEAD are mandatory. Stale or mismatched HEAD fails closed; the
worker does not fetch, pull or rebase on demand. To support new code files,
the validator includes bounded untracked regular files in the verified patch,
not just edited tracked files. The output is a **local uncommitted WSL patch**.
No commit/push/PR/merge/production deployment occurs.

## Local owner/operator gate (requires Codex Local / local-homelab-admin)

1. Refresh [AGENTS.md](../AGENTS.md) and Issue #41. Inspect
   `C:\Projects\RemoteCodex-v02\sources\personal-ai-brain` and
   `C:\Projects\RemoteCodex-v02\sources\homelab-infrastructure` main HEADs, clean trusted source
   and saved local/uncommitted work. Never discard user changes to make a
   deployment appear ready.
2. Confirm the v0.2 source/CI hashes and the only intended shared v0.1 code
   changes in `codex_bridge_core.py`, `codex_bridge_client.py`,
   `codex_bridge_wsl.py` and `codex_bridge_transport.py`. Verify
   existing v0.1 acceptance/replay tests and Windows+WSL runtime tests.
3. Inspect the local Debian `pabbridge` identity and Codex managed auth;
   confirm the root-denied network-disabled `bridge-workspace-only` sandbox
   is effective, not merely claimed in config. The two fixed test gates require
   Python/pytest for PAB and Node 24 plus Movie Intelligence tests for Homelab.
   Never install global packages, copy SSH keys or add network privileges
   just to satisfy an unready test profile.
4. Provision **only** new
   `%LOCALAPPDATA%\PersonalAIBrain\bridge-v02\ledger` and
   `...\transport` directories with protected Windows owner/SYSTEM/Admin
   ACLs. Preserve the existing
   `bridge-v01\ledger` and `bridge-v01\transport` and
   their already accepted task/delivery truth. Reuse the existing private
   Issues-only token **in place**; do not copy it or grant repository Contents
   or broad scopes.
5. Invoke the source preflight from the reviewed checkout:
   `python -m personal_ai_brain.codex_bridge_v02_runtime check`.
   It verifies protected ACLs, private mailbox, Contents denied, empty
   **v0.2** queue and the two canonical local main branches. It does not
   validate WSL test-tool availability or actually start a model turn.
6. Only after prior checks succeed, install a separate limited user-context
   Windows Task Scheduler entry for
   `pythonw -m personal_ai_brain.codex_bridge_v02_runtime serve`,
   owned by the same interactive user. Require logon, do not run as SYSTEM
   or administrator, do not open inbound ports. Keep the v0.1 scheduled
   task and audit unchanged. Record the task startup, and run one controlled
   restart/recovery exercise before external acceptance.
7. Run **independent external ChatGPT-originated** requests in a new issue for
   each project; choose harmless real source+test changes. Verify exactly one
   correlated owner result, the fixed test summary, correct per-project
   checkout/HEAD, no credential leak, no repository publication, no Homelab
   SSH, and successful v0.1 health coexistence. Duplicate/same-task replay
   must produce no extra execution; changed payload with the same UUID must
   fail closed. A simulated uncertain delivery must never start another turn.
8. Record sanitized evidence and exact remaining blockers on Issue #41.
   Only mark v0.2 LIVE when both projects and recovery are accepted.

**Stop:** Unexpected local drift, mismatched source pin, unavailable sandbox,
missing test tool, ACL mismatch, v0.1 ledger health deviation, inability to
preserve current work or delivery uncertainty. A merged PR alone is
NOT local Windows/WSL activation.

## Scope and follow-up

The v0.2 code worker is intentionally for source/tests/docs in disposable WSL
workspaces. Changes to authentication, agent bridge and deployment policy are
blocked by the fixed trusted validator. Domain work requiring live Homelab,
production credentials, Docker, Seerr/Radarr actions or direct SSH belongs to
a **separate authorized operator/domain capability**, not a widened GitHub
coding Issue. PR publication by a separate trusted reviewer/publisher is a
future milestone; do not grant the model GitHub write tokens.

## Dedicated source ownership

Use independent operator-created HTTPS clones under `C:\Projects\RemoteCodex-v02\sources`.
Never use linked worktrees, alternate object stores, or symlinks to development
checkouts. The original development checkouts and v0.1 installation are preserved.
The PAB clone also supplies the v0.2 installed runtime, using a separate Python
environment; do not repoint the v0.1 editable installation or Scheduled Task.

The operator pins each clone to reviewed `main`, with exact canonical origin
`https://github.com/igormego/<repository>.git` and HEAD equal to the locally
recorded `origin/main`. Runtime checks reject dirty/untracked files, wrong origin,
wrong branch and unapproved HEAD. Remote execution performs no fetch or repair.
Only a separate operator action may fetch, review and fast-forward sources.
Store no credentials/private configuration in these clones. Apply protected
owner/SYSTEM/Administrators ACLs and verify the WSL root-deny sandbox cannot read
or write either Windows source clone before activation. Task code receives only
committed Git objects in a separate disposable Linux checkout.
