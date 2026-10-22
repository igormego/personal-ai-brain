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
