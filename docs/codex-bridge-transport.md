# Local Bridge outbound Issues transport v0.1

Implementation for [Issue #22](https://github.com/igormego/personal-ai-brain/issues/22).
External ChatGPT acceptance completed on 2026-10-26. Project State records the
accepted v0.1 milestone and its bounded limitations.

## Fixed boundary

Windows polls only the private `igormego/personal-project-brain` repository over
outbound HTTPS. Exact title `pab-codex/v0.1` separates it from the existing Homelab
`pab-control/v0.1` worker. Redirects, public repositories, arbitrary URLs and extra
request fields are rejected. GitHub is a mailbox, not execution authority.

The trusted mapping is `personal-ai-brain` to the canonical Windows checkout and
existing private Windows ledger, then `wsl-codex-v01`, Debian user `pabbridge`, the
accepted root-deny/offline sandbox, and a UUID-specific Linux checkout. The caller
cannot choose paths, repository URLs, backend, distro, environment, credentials,
approval policy, flags, model endpoints, retries or validator. Managed Codex auth
stays with the accepted user; the dedicated GitHub token never enters WSL.

## Request

Use the exact title `pab-codex/v0.1` and one raw JSON object (no Markdown fences):

```json
{
  "transport_version": "pab-codex/v0.1",
  "operation": "codex.submit",
  "request_id": "<new lowercase canonical UUID>",
  "task_id": "<new lowercase canonical UUID>",
  "project_id": "personal-ai-brain",
  "execution_profile": "local-homelab-admin",
  "goal": "<bounded coding goal>",
  "acceptance_criteria": "<bounded acceptance criteria>",
  "base_sha": "<current main: 40 lowercase hex characters>"
}
```

Both text fields are 1–8000 characters; total UTF-8 body is at most 20000 bytes.
Duplicate JSON keys, nonfinite numbers and unknown fields are rejected. The base
must equal the trusted local HEAD; the worker does not fetch or change that HEAD.
Malformed requests close without echoing their bodies or executing anything;
a sanitized rejection reason remains in the private delivery store.

The v0.1 trusted completion validator accepts changes only to these existing
tracked files; both map to the same fixed pytest selection:

| Remote-edit allowlist | Fixed test selection |
| --- | --- |
| `src/personal_ai_brain/state_quality.py` | `tests/test_state_quality.py` |
| `tests/test_state_quality.py` | `tests/test_state_quality.py` |

This pure state-quality module is the smallest useful coding surface for external
acceptance. Documentation-only requests and all other source/test/config paths
are rejected. In particular, broker, transport, runtime and security policy are
not remotely editable. The installed Windows worker supplies the policy directly;
request/model text and task checkout code cannot replace it.

The fixed command is `/usr/bin/python3 -m pytest -q -p no:cacheprovider
--basetemp <validator-owned-task-local-temp> tests/test_state_quality.py`. It runs
inside the unchanged root-deny, network-disabled Linux sandbox. The full suite,
including Unix-socket tests, remains the CI and exact-tree Homelab implementation
regression gate, not the remote completion command.

At most two files and 3000 changed lines are allowed. HEAD, untracked files,
symlinks, regular files and `git diff HEAD --check` are checked before and after
tests. The complete patch digest must remain unchanged, including same-size
mutations. Test output is capped at 1 MiB, never transported, and removed with
validator-owned temporary files. The deadline remains 180 seconds; failures never
produce successful completion. Only a fixed test-count summary is returned.
The result reports a local patch; `commit_sha` is absent. Requests must leave
changes uncommitted. No task changes are pushed, PR'd, merged or deployed.

The first external acceptance must make a harmless real coding change within
this allowlist and add appropriate state-quality coverage; docs-only is insufficient.

## Results, replay and failure

A single authoritative comment has a stable first-line marker
`<!-- pab-codex-result:v1:<request_id> -->` and a bounded JSON envelope containing
transport version, request/task IDs, project, base, status, attempts,
approval_required, sanitized failure_class and permitted optional diff/test fields.
Only a subset of `public_result` is exposed. Prompts, paths, model summaries, events,
logs, environment values, auth and private ledger data are omitted. Comment
reconciliation requires the expected exact body and repository-owner author ID;
an arbitrary model string or another collaborator's marker is not authoritative.

The accepted Ledger remains the only task execution truth. Same request or same
task/payload reuses truth; conflicting payload rejects without a new turn. One
request replayed in another Issue closes against its original authoritative
comment rather than posting another. A new request ID for the same task may get
its own correlated result without another turn. Same-request conflicts close
with a private rejection record; task conflicts receive a failed result with zero
new attempts. Uncertain/interrupted tasks never replay. A previously active task
returns uncertainty without starting another backend or changing unrelated tasks.

Delivery phases are durably stored before side effects. Crash after execution but
before delivery resubmits to the Ledger for stored truth. Before every POST the
worker first reconciles bounded comments, then persists `posting`. If a POST is
ambiguous and no matching comment is visible, delivery stops as
`delivery_uncertain`; it NEVER blindly retries. This deliberately sacrifices
automatic delivery after an unresolved POST to preserve at-most-one authoritative
comment. The owner must investigate; deleting delivery records is not recovery.
After a comment appears, restart reconciles it and retries only idempotent Issue
closure. Close failures cannot create another comment. Conflicting or multiple
matching comments fail closed for owner review.

`approval_required` is delivered and the Issue closes as blocked. No approval
response or remote approval endpoint exists. The underlying task/ledger is retained.

Polling is normally 30 seconds; errors wait within 30–300 seconds. At most ten
100-item pages and 256 KiB per response are accepted; exceeding bounds stops the
cycle rather than processing partial comment history. Delivery and rejection
records are capped at 4096 each; retention is manual and preserves dedup truth.
A nonblocking OS file lock covers the entire worker lifetime, with Task Scheduler
also configured to ignore overlapping instances.

## Owner credential and startup

Create a NEW fine-grained PAT for `igormego`, only `personal-project-brain`, Issues
read/write and Metadata read-only; all other repository/account permissions off.
Run `deploy/store_bridge_transport_token.ps1` in the owner's terminal. Hidden
input writes only `%LOCALAPPDATA%\PersonalAIBrain\bridge-v01\transport\github-token`
with ACL limited to the owner, SYSTEM and Administrators. Nothing is copied from
Homelab, builder, Core, SSH or Codex credentials. Do not pass the token in arguments,
environment variables, repository files, WSL workspaces, logs or chat.

`python -m personal_ai_brain.codex_bridge_transport_runtime check` verifies ACLs,
private Issues reads, explicit Contents 403, and an empty new-protocol activation
queue. Complete grant selection is owner-confirmed; read probes alone cannot
prove that every excess permission is absent. Issues writes are not exercised by
manufacturing an external acceptance request.

After merge, run `deploy/install_bridge_transport.ps1`. The narrowly scoped task
`PersonalAIBrain-CodexTransport-v01` uses the existing project venv's `pythonw.exe`
in the interactive bridge owner's identity, at that user's logon. It runs with
limited privileges, no password storage, no SYSTEM execution, no inbound listener,
and no changes to interactive Codex. Windows must be powered on, awake, networked,
and the bridge owner logged on with existing WSL/managed auth usable. It is not a
pre-login or always-on server. Credential/startup errors fail closed; health is
sanitized in the same protected transport directory. The durable execution ledger
remains its existing sibling `ledger` directory.

To stop, use Task Scheduler to stop/disable only this named task. Preserve the
ledger, delivery records and task workspaces. Do not use `wsl --shutdown`, kill
unrelated processes or remove uncertain task state. The old Homelab transport and
its credentials are not modified by this installation.

## Acceptance boundary

Internal tests use fixture Issues and fake execution only. Before READY, verify
Windows and exact-tree Homelab regressions, CI/security review, reviewed main
installation, private credential checks, worker startup/restart with zero processed
requests, and existing Homelab protocol health/filter coexistence.

Stop at `READY_FOR_EXTERNAL_CHATGPT_ACCEPTANCE`. Do not create the real acceptance
Issue from the implementing Codex task and do not close Issue #22 or mark Project
State complete. An independent ChatGPT caller must create/read the real coding
request, then test identical duplicate and conflicting payload with zero added
turns. Remote approvals, review/correction loops, autonomous publication/deployment,
API/local-model providers and routing remain out of scope.

## External acceptance — 2026-10-26

Independent ChatGPT-originated acceptance completed after READY:

- private Issue #44 submitted request
  `0349729a-b6d2-4524-ab0e-492088304024` / task
  `8ee760bf-ee69-4804-b9e9-c532cac1decc` on base
  `ce2ca72a8735ef6b2e674d5ba1e985ecc8d88586`;
- the Windows worker consumed it and the Local Bridge executed exactly one task
  attempt through the WSL Codex backend;
- the result was `succeeded`, changed only the two v0.1 allowlisted files and
  reported `9 tests passed in restricted Linux sandbox`;
- Issue #44 received one correlated authoritative result comment and closed;
- identical replay Issue #45 closed without a second result comment;
- conflicting same-task Issue #46 returned `task_conflict` with `attempts=0`
  and closed, proving no second task execution;
- malformed Issue #43, missing required `base_sha`, was rejected and closed
  without execution before the corrected request;
- existing Homelab control remained independent: external `runtime.health`
  Issue #47 and `core.health` Issue #48 both completed and closed normally.

The generated coding patch remained local/uncommitted. No push, PR, merge,
deployment or remote approval was performed by the coding task.

Accepted v0.1 scope remains intentionally narrow: fixed `personal-ai-brain`
mapping, fixed execution profile/backend, and remote edits limited to
`state_quality.py` plus `test_state_quality.py`. Multi-project routing,
remote approvals, automated review/correction, autonomous publication/deployment,
local/API provider routing and budget-aware scheduling remain separate milestones.
