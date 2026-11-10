# General Agent Workspace v0.1 — opt-in execution acceptance (#77)

**Status: LIVE_ACCEPTED (2026-11-03) for bounded offline `workspace.python.v1` through normal ChatGPT → private GitHub Issues → authenticated control → existing durable Work Runner → isolated sandbox → artifact status.** This change adds an operator-disabled
`workspace.python.v1` profile to the *existing* durable Work Runner. It does
not create a new daemon, ledger, queue, MCP server or host shell. This document
describes the **one-time operator bootstrap** needed to make a whole class of
novel scratch Python tasks available through the already-authenticated
`work.submit/status/cancel` control transport.

## Verified live acceptance (2026-11-03)

Codex Local operator reported the reviewed builder/control deployment completed with
`generic_enabled=true`, `coding_enabled=false`, original durable ledger preserved,
and local fixture/three generic/replay-conflict tests passing. Normal ChatGPT
independently submitted three new distinct private GitHub tasks:

- [#98](https://github.com/igormego/personal-project-brain/issues/98) → [#99](https://github.com/igormego/personal-project-brain/issues/99): inline-number aggregation, correct `answer.json` preview and SHA-256, one execution.
- [#102](https://github.com/igormego/personal-project-brain/issues/102) → [#104](https://github.com/igormego/personal-project-brain/issues/104): word-frequency count, correct `frequency.json` preview/hash, one execution.
- [#103](https://github.com/igormego/personal-project-brain/issues/103) → [#105](https://github.com/igormego/personal-project-brain/issues/105): CSV-to-Markdown ranking, correct `ranked.md` preview/hash, one execution.

An exact retry through [#100](https://github.com/igormego/personal-project-brain/issues/100) returned `created=false` with `attempts=1`; a changed payload with the same task ID through [#101](https://github.com/igormego/personal-project-brain/issues/101) was rejected as `task_id_payload_conflict`. These results verify the external transport, durable identity and bounded artifact return, **not** arbitrary host authority, autonomous coding, semantics verification by the worker or native plugin write access. See [Issue #77 acceptance](https://github.com/igormego/personal-ai-brain/issues/77#issuecomment-6600365441).

The following installation procedure is retained as the historical operator acceptance/runbook. Do not repeat activation merely because the historical steps are written in imperative form; re-check current installed state before future upgrades.

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

## SOURCE-ONLY: trusted registered host resource into existing workspace (#77/#64)

A separate optional contract `workspace.resource-python.v1` lets the caller
request **one exact registered logical input**, `homelab/host-snapshot`, while
supplying its sandbox script. It cannot provide replacement input bytes, a path,
network endpoint, credentials or claim its own provenance. Existing
`workspace.python.v1` caller-inline tasks remain unchanged.

The *trusted builder*, after a durable single-attempt task reservation and only
when **both** `generic_enabled` and **separate** `resource_enabled` are true in
the **operator-owned profile**, reads a fixed private, read-only host snapshot
from `/opt/personal-ai-brain/worker-resources/host-snapshot.json` (not from
the request). It reuses `host_audit.read_snapshot` ownership/no-symlink/
schema/freshness validation. It stages the complete bounded sanitized snapshot
under `/workspace/inputs/host-snapshot.json` through existing `InputGrant`
and Bubblewrap read-only mount. A durable, non-sensitive receipt with source,
collection time, bytes and SHA-256 is saved **before script execution** and
appears alongside any successful artifact manifest. Existing one-attempt,
idempotency, collision-safe staging and preview bounds remain in effect.

**No new approval is implied by source merge.** Default is off, no profile
change, host mount, credential, container or network action has been performed.
The existing trust model of the private GitHub mailbox grants read access to
its allowed owners/collaborators; this host summary contains only fields already
exposed by the separately accepted `homelab.audit`. Never generalize the same
egress behavior to personal media titles, sensitive sources or logs without a
separately approved authenticated owner grant, retention/redaction design and
further tests.

Before any host rollout, the trusted operator must verify the exact installed
builder binary/source pin, user/group/ACL and immutable fixed mount provenance,
the fresh snapshot schema and confidentiality class, the existing ledger, the
pin-preserving rollback, and Bubblewrap's **real** isolation. Existing builder
profile remains off for this resource type. Acceptance needs one fresh
normal-ChatGPT `work.submit` with the new contract and a correlated
`work.status` showing an actual task-local resource receipt *without* copying
snapshot bytes into private GitHub Issues. A merged PR or fixture tests alone
do not constitute live source-to-sandbox acceptance.


## Deployment blocker #77: private collector UID → builder read-only bridge

The 2026-11-10 live preflight found that the existing collector atomically
publishes `snapshot.json` (private collector UID, 0700 directory / 0600 file)
while the builder runs under a different UID. `host_audit.read_snapshot` accepts
only root or the reader's own UID. Opening the collector directory via ACL
or bind mount alone does not safely cross this identity boundary. **Do not
loosen the reader validator or give the builder access to the collector
account's directory.**

The reviewed `work_snapshot_publish` is a **single-shot, root-only**
publisher; it is not a new agent, sandbox, queue or ledger. An operator-owned,
0600 configuration at
`/etc/personal-ai-brain/builder/snapshot-publisher.json`
must contain exactly:

```json
{
  "collector_source_path": "<ACTUAL_HOST_COLLECTOR_DIR>/snapshot.json",
  "collector_uid": 1001,
  "reader_gid": 1002
}
```

These example IDs are **illustrative placeholders only**; the actual trusted
host UID/GID and host-side path must be checked privately from the running
systemd unit and its BindPaths. Never commit them, infer a host pathname from
the collector container path, or make the file/model request choose a path.
The publisher's target is hardcoded to
`/opt/personal-ai-brain/worker-resources/host-snapshot.json`.

**One-time operator installation after source review:** create the fixed target
directory as **root-owned, builder-group, mode 0750** and verify all its
parents permit appropriate traversal without allowing builder-group writes.
Install only the trusted pinned code and root-owned config; create a minimal
root `Type=oneshot` systemd service invoking
`python -m personal_ai_brain.work_snapshot_publish` **with the exact trusted
Python environment**. For repeat refresh, use a bounded systemd timer
(e.g. every 2 minutes) or an equivalently operator-controlled one-shot
trigger. Only this service may read the collector's private source. Constrain
the unit with `NoNewPrivileges`, `ProtectSystem=strict`,
`ReadWritePaths` limited to the fixed target directory, no network, and
minimum capabilities required for reading collector-owned input and setting
the target group. Do **not** make the entire builder or sandbox root and do not
move any collector credentials.

Each invocation securely opens all path components without following links,
checks the input directory/file owner and private modes, validates the
complete bounded `host_audit` schema and 30-minute freshness, then publishes
a compact verified copy using temp-write → `fsync` → **atomic replace** →
directory `fsync`. The output is **root-owned, builder-group, mode 0640**,
so the builder can read it but cannot modify it. Publication never follows
a model-supplied path/URL or creates the directory. A bad, missing or stale
source produces a fixed failure; the previous generation is left in place
to expire and will be rejected by the existing reader.

**Activation sequence (same owner-approved limited #77 gate):** first run
the privileged publisher once with fresh source and verify the builder can
read the result through the existing `host_audit.read_snapshot` **without**
access to the collector source. Verify a second source generation becomes
visible and stale/bad input stops safely. Only then pin and update the minimal
required builder and Control contracts from PR #99, with rollback images and
the existing ledger intact; enable only `resource_enabled=true` on the
trusted builder profile. Run real Bubblewrap isolation, one new correlated
ChatGPT `work.submit`/separate `work.status` with source receipt, one
denied forged input, and compatibility for `workspace.python.v1`. If any
precondition fails, leave the grant disabled; do not retry an uncertain task.

No disk/backup/NTFS, generic host privileges, network exposure, ARR title
disclosure, model-paid API calls or side effects are included. This is
only the **transport for one sanitized resource class**. Subsequent resource
families should reuse a typed operator-owned registry and this same
provenance-bound path, rather than expanding source ownership trust or adding
a new agent per natural-language question.
