# v0.4A read-only control bridge

The separate control worker accepts bounded requests without an active SSH or
Codex session. GitHub Issues is the first transport, not the capability API.
`control.py` owns strict request validation and the capability registry;
`github_issues.py` owns the narrow private Issues adapter; `control_worker.py`
owns polling, durable reservations, audit and result delivery. No new dependency,
database, listener, Docker socket, shell handler or dynamic plugin loader is used.

## Contract for any caller

Create an issue in the **private** `igormego/personal-project-brain` repository.
Use the exact title `pab-control/v0.1`. The body must be a raw JSON object, without
Markdown fences or prose. Generate a fresh lowercase canonical UUID and a current
RFC3339 timestamp with timezone for each new request:

```json
{
  "schema_version": "0.1",
  "request_id": "REPLACE_WITH_FRESH_UUID",
  "capability": "runtime.health",
  "arguments": {},
  "requested_by": "chatgpt",
  "requested_at": "REPLACE_WITH_CURRENT_RFC3339_TIMESTAMP"
}
```

The placeholders are intentionally invalid: replace them before submission.
Exactly these six fields are required. Maximum request size is 8 KiB. Duplicate
JSON keys, nonfinite numbers, unknown fields, unknown capabilities and invalid
arguments are rejected. Requests older than seven days or more than five minutes
in the future are rejected. `requested_by` is audit metadata, never authorization;
it accepts 1–64 letters, digits, dots, underscores or hyphens.

The authenticated private repository access, the fixed capability allowlist,
read-only risk policy, strict validation and narrow handlers form the boundary.
Title and caller strings only route/describe requests. All users able to submit
issues to this private repository may invoke this same read-only capability set.
Do not grant repository access expecting `requested_by` to restrict it.

| Capability | Arguments | Output |
|---|---|---|
| `runtime.health` | `{}` | Core heartbeat availability and last manual run status/time |
| `core.health` | `{}` | Fresh source-read status and state/finding/proposal counts |
| `core.snapshot` | `{}` | Fresh machine snapshot and health summary |
| `core.report` | `{}` | Fresh Markdown report and health summary |
| `core.rollup_proposals` | `{}` | Fresh proposals, never applied |
| `runtime.logs_tail` | `{}` or `{"lines":30}` | Fixed Core service log, 1–100 lines, at most 4096 output bytes |

Every registry entry declares an ID, description, risk level, argument schema and
validator, handler and side-effect class. Only `read_only` / `none` entries can
register in this version. Core handlers reuse deterministic collection functions
and the existing GET-only reader, without writing Core files or source content.
Local private audit writes and Issues result/close operations are infrastructure
effects of the bridge, not user-selectable mutation capabilities.

## Result and idempotency

The worker posts a JSON result in a fenced issue comment and closes the issue.
Results include `schema_version`, `request_id`, `capability`, `status`, execution
timestamps, a sanitized error code and result data. Status is one of
`capability_completed`, `capability_execution_failed`, `request_rejected` or
`duplicate`. Invalid envelopes use `invalid-issue-N` as a correlation ID. Source
validation findings remain successful capability results with `source_status:
findings`; inability to read sources produces an explicit execution failure.

Transport results are bounded; large outputs have `truncated: true`, a JSON text
preview and `full_result: local_private_audit`. The preview is not a complete
parseable snapshot. The complete sanitized result stays in the private request
audit on Homelab. Known credentials and common token/authorization patterns are
redacted before persistence or delivery. Exception messages are never echoed.
No arbitrary file, URL, repository, ref, command or service argument is accepted.

One shared host audit directory is the authority. `flock` serializes workers using
that directory, and fsynced atomic request records reserve IDs **before** invoking
a handler. Completed IDs and reopened issues never execute again. A killed worker
can leave a reservation in `processing`: on rediscovery it becomes an interrupted
execution failure, with no automatic rerun. An operator may submit a new ID after
inspection. This guarantees at-most-once invocation within this single-host
deployment; it does not claim impossible exactly-once completion across crashes.

Delivery retries reuse saved results. A lost response to comment creation can
produce a duplicate result comment after retry, but cannot repeat the capability.
Never delete the audit ledger to clean logs, restore an older ledger, or run a
second worker with an independent directory against the same queue. These actions
would discard the deduplication boundary. Multi-host/distributed execution is out
of scope. Request/issue audit records have manual retention and must be preserved.

## Deployment and credentials

From the repository on Homelab:

```sh
sh scripts/runtime.sh up
sh scripts/control.sh up
sh scripts/control.sh health
sh scripts/control.sh logs
```

`control.sh down` stops only the control Compose project. Core remains available.
Both run detached with `restart: unless-stopped`, non-root UID/GID, read-only roots,
dropped capabilities and no-new-privileges. The control service has no ports or
Docker socket. It reads `/core-state` through a read-only bind; only `/control`
is persistently writable. Core exposes its existing heartbeat at
`/state/heartbeat.json` so the separate worker can read it without shell/Docker
access. A crash can leave a heartbeat file; it expires after 30 seconds.

Existing Core token: `~/.config/personal-ai-brain/github-token`, unchanged.
Dedicated transport token: `~/.config/personal-ai-brain/github-control-token`.
Both are mode-600 files mounted as read-only file secrets, never token values in
environment variables, command arguments, image build context or Git. Worker
configuration contains only paths. Fine-grained transport permissions:

- Resource owner `Az1mutt`; only repository `personal-project-brain`.
- Metadata read; Issues read/write; no Contents permission or other write scope.
- The separate existing Core token continues to provide source Contents read.

The adapter checks the private repository identity before discovery and immediately
before posting results; it fails closed if the repository is public or inaccessible.
It refuses redirects and restricts routes/methods to metadata/issue reads,
result comments and closing issues. It cannot change issue bodies, source files,
repositories or permissions. Revocation/expiry appears in transport health.

## Polling, health and audit

Default polling is every 30 seconds. One 100-issue page is scanned per cycle with
a rotating cursor; pull requests and other titles are ignored. Larger backlogs
increase latency; changing page membership can defer discovery until the next full
sweep. Calls are serial, HTTP timeouts are 15 seconds, and mutative calls are
spaced by at least one second. Retry backoff grows from 60 to at most 900 seconds;
a longer server Retry-After/rate-reset deadline takes precedence. There is no
inner retry storm. SIGTERM finishes the current bounded request and stops before
the next one; handlers have a 120-second deadline and shutdown grace is 210 seconds.

`control.sh health` distinguishes process availability from `healthy`,
`transport_unavailable`, `authentication_permission_failure`, `transport_policy_denied`
or `worker_failure`. It also reports the last request status after a successful
poll. Docker checks local liveness only; transport failure and schema findings do
not mark a running worker dead. Rich health exits 0 for healthy transport, 3 for
transport/worker status requiring attention and 1 for unavailable process. A worker
heartbeat expires after 180 seconds to allow one bounded capability to finish.

Private state is under `~/.local/state/personal-ai-brain/control/`:

- `requests/<UUID>.json`: validated arguments, caller audit metadata, request and
  execution timestamps, source issue reference, execution status and sanitized result.
- `issues/<number>.json`: rejected requests or completed execution record, and delivery status.
- `status.json`: latest transport health and pagination cursor.
- `worker.lock`: shared execution lock; do not replace while a worker runs.

Docker lifecycle logs use bounded local logging. Core lifecycle logs remain in
the existing runtime directory. No raw private reports belong in the public repo.

## Acceptance boundary

Deterministic tests and Codex-originated private transport probes verify the
Homelab implementation. They do **not** establish ChatGPT connector acceptance.
A ChatGPT project chat must independently create a fresh `runtime.health` request,
read its worker-produced correlated comment and verify issue closure, then repeat
with `core.health`. `core.snapshot` is optional. Mark overall acceptance complete
only after that external caller test is recorded; see the rolling handoff.

No Telegram, n8n, MCP, LLM, media/recipe handlers, source-state writes, service
restart capability or arbitrary execution API is implemented in this milestone.

## Remote Work Submission v0.1 extension

The accepted bounded write exception adds `work.submit` and `work.cancel`, plus
`work.status`, while preserving the six original read capabilities. The existing
private Issues credential and caller boundary are reused. See
[Remote Work Submission](remote-work-submission-v01.md) for exact arguments,
durable handoff semantics, ownership/mount boundaries and the separate external
ChatGPT acceptance gate. The earlier read-only registry restriction describes
v0.4A; only these named local-work effects are now additionally permitted.
