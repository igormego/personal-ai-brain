# Codex bridge durable-core checkpoint evidence

Status: incomplete; remote transport disabled.

Checkpoint: 7d11bbcb26156f2760bbff2a8fad33197348b389.
Branch: codex/bridge-durable-core.

## Exact-tree Linux validation

2026-10-22: Git bundle of checkpoint transferred via existing ssh homelab.
Cloned into owned disposable /tmp/pab-bridge-test.mOq9PAIl/repo.
git rev-parse HEAD matched checkpoint exactly.
A disposable venv installed the declared .[dev] dependencies.
Full pytest: 251 passed in 1.70 seconds. No production checkout/service modified.

## Windows validation

16 focused ledger tests passed (latest run: 0.72 seconds).
Native full-suite collection is inappropriate: existing Linux modules use fcntl.

Real AppServer + execute + Ledger smoke task:
task ef22e806-af0a-4a97-a8d7-acf755d253b4.
Managed ChatGPT authentication; explicit MXC and bridge-workspace-only overrides.
Status succeeded. One actual turn/start call.
Identical duplicate returned existing truth; no second turn/start.
This was reply-only, not the required real coding acceptance.

Prior persisted-history forensics:
thread 01a1655c-0e5f-7d62-a84b-a7db969e31e7;
turn 01a1655c-13af-7f20-aa91-c9f6e88be47e;
runtime notLoaded, persisted turn interrupted.
No resume, turn/start or approval response during read-only history inspection.

## Remaining acceptance gaps

The client still targets the canonical cwd directly; task-specific disposable
worktree preparation/base verification is not implemented.
Explicit cancellation and complete crash/recovery integration matrix are pending.
RPC event dispatch currently drops unrelated notifications while waiting for a
response; approval during RPC becomes a generic exception instead of the
persisted approval_required path. No approval is accepted, but this needs repair.
Actual turn counts are verified only for reply success + identical duplicate;
ledger unit tests are not substitutes for live crash acceptance.
Outbound controlled TCP acceptance, static/secret scan, offline environment
checks, internal change/test/commit artifact, CI and security review remain pending.
Do not deploy or enable submissions from this checkpoint.
Project State is intentionally unchanged.

## Dispatcher repair follow-up

The notification-dropping defect listed above is superseded by this repair:
one stdout reader correlates response IDs to private wait queues and dispatches
notifications/server requests independently. Approval policy runs in the
dispatcher and persists approval_required even while an RPC waits.

Windows byte-range lock setup no longer reads the locked byte before acquiring
the lock. A real approval test exposed that race; the successful repaired test:
task fb938d9c-5ae0-4256-977f-474206029260;
thread 01a1656f-b33a-79c3-bf6e-d45fac188c93;
turn 01a1656f-b9bd-72a3-ac5d-7184ec9f51c9.
One actual turn/start, terminal bridge state approval_required,
failure_class owner_approval_required, no approval response sent.
Earlier diagnostic tasks each started once and ended uncertain; none was replayed.

Windows focused suite now has 27 passing tests, including response/event ordering,
concurrent RPC correlation, malformed notification, EOF, interruption notification,
and ledger approval persistence during RPC wait.
Disposable-worktree execution, complete live crash/cancellation matrix and real
coding acceptance remain unimplemented/unaccepted. No deployment authorization
is inferred from these focused checks.

## Acceptance stop: malformed response ID terminates dispatcher

2026-10-22, exact implementation HEAD
c6118deca37906f082a55d776c0ea7894b2b145f, deterministic in-memory
protocol probe using the existing test Pipe/client helpers.

While thread/read was pending, supplied:
1. malformed unrelated response with id=[] and a result object;
2. valid turn/completed interruption notification;
3. valid response matching the pending RPC.

Observed:
- pending RPC failed with RuntimeError: dispatcher_failed_TypeError;
- reader terminated;
- valid interruption notification was not delivered;
- two input messages remained unread.

Cause: AppServer._read indexes pending with message["id"] before validating
that the JSON-RPC response ID is a supported scalar. An array-valued ID is
unhashable. The outer exception handler terminates all dispatch instead of
isolating the malformed unrelated message.

This violates malformed-message isolation and the no-message-loss acceptance
invariant. Per the continuation's explicit STOP condition, no live worktree,
crash/cancellation or coding acceptance followed. No PR/push/merge/deployment
or Project State completion occurred.

Required repair: validate inbound message envelopes/ID types before correlation;
isolate malformed messages into bounded sanitized diagnostics; add a regression
covering malformed response followed by a valid interruption event and matching
RPC response. Reassess bounded-queue overflow handling in the same dispatcher
review before resuming the remaining acceptance.

## Malformed-message repair and live regression

Central classify() validates decoded envelopes before correlation/dispatch.
Responses require a positive exact Python int ID (bool/float/containers rejected),
exactly one result/error, and a structured error with integer code/string message.
Server requests accept exact int or nonempty string IDs; notifications omit IDs.
Malformed messages and handler failures generate sanitized diagnostics in a
64-entry ring, separate from valid-event delivery. No raw malformed payloads
are retained. Unknown/late/duplicate responses cannot overwrite a waiter.
EOF/transport failures wake pending RPCs; message-level faults do not.

Windows focused suite: 46 passed in 1.00 seconds.
New fault coverage includes 16 malformed envelope variants, duplicate/unknown
responses, 200-message malformed flood and handler exception containment.
All injected cases retain subsequent valid traffic and keep the reader alive.

Real MXC approval regression:
task 91a4abfe-287f-4ee8-93e2-2d0a01a6d3a6;
thread 01a16577-7bcb-70d2-874c-d91296833eba;
turn 01a16577-8211-7822-8570-b75e90d70d6c.
State approval_required; failure_class owner_approval_required;
one actual turn/start; zero approval responses; no protocol diagnostics.
Remote transport remains disabled. Full durable-core acceptance remains pending.
