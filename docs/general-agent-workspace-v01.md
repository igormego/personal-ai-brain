# General Agent Workspace v0.1 — opt-in execution acceptance (#77)

**Status: SOURCE ONLY, NOT ACTIVATED.** This change adds an operator-disabled
`workspace.python.v1` profile to the *existing* durable Work Runner. It does
not create a new daemon, ledger, queue, MCP server or host shell. This document
describes the **one-time operator bootstrap** needed to make a whole class of
novel scratch Python tasks available through the already-authenticated
`work.submit/status/cancel` control transport.

## What it unlocks, and what it does not

Once live-accepted, ChatGPT can submit **new Python computations** on
caller-inline, explicitly non-sensitive data without adding a handler for each
request. The model can generate different scripts per goal. The code runs only
inside rootless/no-network Bubblewrap on size-capped task-local tmpfs. It
cannot interact with Sonarr/Plex/Docker, read host files, use GitHub credentials,
install dependencies, reach private networks or deploy code. Future **resource
broker** grants are a separate reviewed extension; caller fields never
authorize access to existing host data. The status includes a SHA/size/path
manifest and at most 1024 bytes of small safe UTF-8 previews. Larger outputs
are retained in the existing private builder audit pending a separately
reviewed artifact-delivery route.

A successful task means only that the sandboxed script exited zero and its
declared output envelope passed deterministic validation. It does **not**
mean the natural-language requirement was semantically verified or that a
production system was modified.

## Existing transport, unchanged protocol

Outer issue: the already authenticated private `pab-control/v0.1` with
`capability="work.submit"` and `arguments={"contract":...}`. Use the same
separate `work.status` (new issue/request UUID) for progress and results.
The entire control body and nested contract must be under 8192 UTF-8 bytes.

The nested Work Contract uses existing fields `schema_version="0.1"`,
`task_id` (fresh canonical UUID), `task_type="workspace.python.v1"`,
`repository="Az1mutt/personal-ai-brain"` (**legacy fixed contract
identifier, not an arbitrary repo selector**), `base_ref` (the established
operator-allowlisted immutable Work Runner base SHA), `goal`,
`acceptance_criteria`, `requested_at`, `deadline_seconds <= 180`,
plus **exactly** `script` and `inputs`. Every script is a UTF-8 string
up to 4096 bytes; `inputs` is a list of up to eight
`{"path":"name.txt","content_b64":"..."}` objects (combined decoded data
<=4096 bytes). These are **user-inline inputs**; the caller must not
claim they come from a privileged Resource Broker. No host path, URL,
command, network endpoint, Docker/SSH parameter or credential is a field.
Existing fixed fixture and coding contracts continue to parse unchanged.

A script must print **one JSON object only** (not conversational logs):
`{"files":[{"path":"answer.txt","content_b64":"<canonical base64>"}]}`.
The output has 1–16 entries, <=16 KiB of stdout; names must be safe,
bounded relative paths. Only the trusted runner decodes and stages files on
the host *after* the sandbox exits. Example body for the script itself:

```python
import base64
import json

answer = "Výpočet hotový."
print(json.dumps({"files": [
    {"path": "answer.txt",
     "content_b64": base64.b64encode(answer.encode()).decode("ascii")}
]}))
```

Actual task results are correlated via durable task ID/hash, not via model
claims. Non-text outputs are retained as private artifacts and represented
only by digest/size metadata.

## One-time acceptance/installation gate — performed by trusted local operator

The existing `local-homelab-admin` Codex Local user-context is the only
historically verified way to inspect the real Homelab. The Remote Codex
`pab-codex/v0.2` repository sandbox is **not** permitted to use SSH, edit
runner/security modules or install a host service; do not send it this job.
A foreground ChatGPT conversation has no direct authenticated LAN terminal.

Operator, before any mutation:
1. Read the current AGENTS.md, #77, this runbook, current deployed
   `docs/remote-work-submission-v01.md` and the actual Homelab state.
   Verify SSH in the established user context. Compare current live builder
   binaries/profile, control container pin, units, user/group and mount
   boundaries against the merged candidate. Stop on drift.
2. Ensure no submitted/reserved/running builder task is in flight; preserve
   the existing private builder ledger/workspaces/artifacts and control
   ledger, and record **private** pre-change hashes/backup/rollback of the
   exact installed root-owned code and control image pin. Do not wipe
   old records or run the historical broad installer blindly.
3. Stage only the reviewed Python modules required by the new profile:
   `work_contract.py`, `work_runner.py`, `work_sandbox.py`,
   `work_generic.py`, `workspace_artifacts.py`,
   `work_handoff.py`, plus their existing trusted dependencies.
   Stage matching **control-only** modules in an immutable candidate image.
   Do not modify Plex/ARR/Core/other containers or copy new credentials.
4. **Keep `generic_enabled=false` initially.** Verify all unchanged
   fixture, coding, `work.submit/status/cancel` and Core/control regressions.
   Run the negative tests and a synthetic script under the **actual**
   `pab-builder` identity using the real Bubblewrap namespaces (not the
   mocked CI command). Confirm the sandbox cannot read secrets/host
   mount/other task, reach network/socket, spawn survivors or write to
   host disk. Inspect the **effective** `--tmpfs /workspace` cap,
   read-only `/workspace/inputs`, resource limits and denied writes.
   Re-test restart/replay/conflict and interrupted task without reexecution.
5. Only if checks pass, apply a **single operator-authorized isolated**
   update to the existing builder service and only the necessary control
   image, preserving all mounted state/credentials and rollback pin.
   Set root-owned `generic_enabled=true` on the trusted builder profile
   with matching ownership/modes, restart only the authorized services
   and verify previous capabilities still work. If any unexpected change,
   restore previous pinned binaries/config/image with the ledger intact.
6. From **ordinary ChatGPT via connected private GitHub**, send ONE new
   benign `work.submit` task, then a separately correlated `work.status`.
   Verify completion contains artifact digest/size and a short answer
   preview; exact duplicate produces **zero additional execution** and
   same-ID/different-payload fails closed. Run three different previously
   unseen goals through the same contract without a bespoke handler edit.
   Check no Python script or script output is executed by the control
   process, and no host resources are accessible.
7. Record sanitized evidence in #77 and then update Project State. Mark
   live only after all host and external ChatGPT gates pass. Long-term
   unattended automation, remote approval, arbitrary resource discovery,
   host mutations and WhatsApp remain **separate** acceptance gates.

**Do not copy real secrets/user-private data into task scripts or inline
input bodies:** private GitHub Issues and private Work Runner audit retain
those bytes. The first external acceptance must use non-sensitive fixtures.

## Invariants

- `generic_enabled` defaults **false**; no existing service is enabled by
  merely merging this code.
- One existing durable worker and task ledger; no new "agent swarm".
- Sandbox cannot write host directory; anonymous tmpfs prevents disk-fill
  through unlimited arbitrary files.
- The only outward script channel is bounded stdout JSON; all output file
  paths and types pass trusted validation. Raw stdout is not returned to
  ChatGPT.
- Resource Broker grants, generalized file import, large artifact retrieval
  and real domain writes are **not implemented** by this milestone.
- Failure, cancellation and crash preserve durable attempts; never silently
  rerun an uncertain generic task.
