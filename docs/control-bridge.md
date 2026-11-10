# v0.4A read-only control bridge

The separate control worker accepts bounded requests without an active SSH or
Codex session. GitHub Issues is the first transport, not the capability API.
`control.py` owns strict request validation and the capability registry;
`github_issues.py` owns the narrow private Issues adapter; `control_worker.py`
owns polling, durable reservations, audit and result delivery. No new dependency,
database, listener, Docker socket, shell handler or dynamic plugin loader is used.

## Generic registered-capability discovery helper — SOURCE ONLY (2026-11-10)

A pure `capability_discovery.discover_registered(registry)` helper returns only
the actually present registry metadata (ID, description, risk, side-effect class,
argument schema). Optional work, media and host resources are absent until
their independent trusted providers/grants are configured. This is **not** a
new control capability: the currently pinned production control protocol and
registry remain unchanged, and ordinary ChatGPT cannot yet invoke discovery
through the worker. A later reviewed integration must explicitly expose this
helper without weakening authentication or the existing candidate-image pins.
No new host access, execution authority, network endpoint or credentials.

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
| `project_os.status` (live ChatGPT transport-accepted 2026-10-27; recovery gate pending) | `{"project_id":"homelab"}` or `{"project_id":"homelab","workstream_id":"media-automation"}` | One bounded status/gate/next action from Core project map; explicit freshness and source findings |

Every registry entry declares an ID, description, risk level, argument schema and
validator, handler and side-effect class. Only `read_only` / `none` entries can
register in this version. Core handlers reuse deterministic collection functions
and the existing GET-only reader, without writing Core files or source content.
Local private audit writes and Issues result/close operations are infrastructure
effects of the bridge, not user-selectable mutation capabilities.

## Assistant Project OS status and request-conflict checkpoint — 2026-10-26

The read-only `project_os.status` source and tests were merged in PR #32
(`c4ca92f0`; 494 tests passed). Only logical IDs are accepted: `project_id`
and optional `workstream_id`. The capability reuses the existing Core
collection and reports project phase, gate, exact next action, verification
age/freshness and source integrity finding codes. No caller-controlled repository,
path, URL, ref, shell command or domain write is added.

PR #33 (`f9f93ab4`; 497 tests passed) strengthens the existing durable
`pab-control/v0.1` request identity: new request records bind the UUID
to a canonical SHA-256 of the *complete* validated request. Same-ID,
different-body replay now fails closed with `request_conflict` and no
handler invocation. Historical reservations without a verifiable payload hash
are preserved, not migrated or re-executed, and their replays fail closed.
An exact duplicate of a post-upgrade request still reuses saved result truth.

**Live status transport accepted (2026-10-27); operational and full Gateway gates remain open.**
The earlier external Issue #51 returned `unknown_capability` against the old
worker. The operator subsequently deployed the approved six-module control image,
including the fail-closed source-read fix PR #36, without altering the Core,
Windows bridge, unrelated services or production audit. The operator's
[deployment evidence](https://github.com/igormego/personal-ai-brain/issues/34#issuecomment-6434876689)
pins the active image to
`sha256:06d57e7a8f32995bddc077dcbd94dad850ace15c531914bf38626e08c3484770`.

Independent ChatGPT submissions [#54–#58](https://github.com/igormego/personal-ai-brain/issues/34#issuecomment-6434937635)
proved bounded Project OS project/workstream replies, correct correlation and
issue closure, an unavailable-project failure, identical replay using saved truth
and changed-payload `request_conflict`. Real source quality was surfaced:
Homelab project freshness was `unknown` (`state_required_field_missing`) and
media-automation was `stale`, **not** falsely treated as current. A subsequent
fresh `runtime.health` [Issue #59](https://github.com/igormego/personal-project-brain/issues/59)
also completed and closed.

**Do not treat this as full Gateway acceptance or permanent deployment readiness.**
[Platform gate #34](https://github.com/igormego/personal-ai-brain/issues/34)
remains OPEN pending operator-controlled restart/recovery and moving the
deployment Compose override out of a temporary `/tmp` staging directory.
The accepted historical ledger must not be rewritten or replayed. The pure
ChatGPT-side codec PR #35 is not an activated native authenticated adapter;
outbound delivery into a normal ChatGPT conversation remains unverified.
[Personal Assistant Gateway #25](https://github.com/igormego/personal-ai-brain/issues/25)
stays OPEN. No WhatsApp adapter or additional permissions are enabled.

## Optional Assistant movie planning bridge — source-only, NOT deployed (2026-10-27)

The source-level `media.plan_movie` hook is registered **only when a trusted
`media_plan_provider` is supplied** to `build_registry`. The production control
worker currently supplies no such provider; an external Issues request will still
receive `unknown_capability`. There is no `media.request_movie` write capability.

The source adapter in `assistant_media_plan.py` invokes only the existing Homelab
`tools/movie-intelligence/plan-cli.mjs` using a fixed, operator-owned Node binary,
absolute script path, exact sanitized selector fields, no shell, no inherited
environment, and a bounded execution timeout/output. It validates an allowlisted
non-mutating result. The Homelab CLI itself calls only the accepted Seerr
`plan_movie_request` path; it has **no `--execute` flag** and no arbitrary network
request, command or server options.

Production integration is NOT authorized by this source merge. The operator must
first verify a pinned immutable CLI/source revision, Node >=24.14, existing private
Movie Intelligence DB read-only access, Seerr/Radarr GET connectivity, least-privilege
credentials and isolation; no new mounts, users or secrets are introduced here.
Also preserve the pending [control restart/durable-Compose gate #34](https://github.com/igormego/personal-ai-brain/issues/34)
before another control image update.

A returned `requestable` plan means only that the existing wrapper's read-only
preflight regards a movie as requestable. It does **not** authorize a Seerr POST,
acquisition, a new Radarr search or a download. [Media Request Issue #38](https://github.com/igormego/personal-ai-brain/issues/38)
owns future production planning acceptance and the separate, durable authorized
movie-write capability.

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

## Current pinned Homelab deployment

For the accepted Issue #34 runtime, use the [durable pin and rollback runbook](control-deployment-issue34.md). Its stable configuration supersedes the generic build commands below for that host.

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

## Canonical owner maintenance — 2026-10-23

PR #21 (`2236ac3`) fixes the stale owner without accepting redirects or changing
credential permissions. The running adapter verifies the private
`igormego/personal-project-brain` repository directly. Issues read/write was
verified by a correlated `core.health` request, result comment and automatic
closure; private Contents remains denied for the transport credential. Complete
fine-grained grant enumeration was unavailable; no broader permission audit is claimed.

Internal maintenance request `5fe3183a-dbe8-4a78-924d-0ccacfe2363f` completed in
private Issue #42 with existing source findings (12 states, 3 errors, 6 warnings,
2 proposals). This accepts the existing Homelab control path, not external ChatGPT
transport into the new Windows Local Bridge. A 2026-10-25 read-only refresh confirmed
healthy transport, both healthy containers and successful fresh Core reads.

The runtime checkout preserves base `f7ad9ba` on local deployment branch
`codex/runtime-issues-owner-20261023`, with only PR #21 cherry-picked as `eeb749c`.
The control image layers only the corrected module onto the prior image;
`personal-ai-brain:control-before-owner-fix-20261023` remains available for rollback.
The normal `personal-ai-brain:control` tag points to the corrected image. Core was
recreated for credential refresh; unrelated services were not restarted. Updating
the preserved runtime baseline to current main is outside this maintenance scope.

## Typed ARR API reads — source-only (2026-11-10)

The existing `media.resource_read` registry may now route independent, explicitly
granted `arr/radarr` and `arr/sonarr` resources through the same typed
`ResourceQueryBroker` as `movies/watchlist`. The allowlisted views are
`items` (movie/series identity, monitored status, current quality profile,
and Radarr `hasFile`) and `profiles` (profile ID/name). Limit is 1–10 per
page; signed, principal/view/limit/content-generation-bound cursors reject
changing inventories. No caller-controlled API path, URL, method, server,
command, config value, write flag or arbitrary field is accepted.

The new `ARRReadProvider` performs bounded **GET only** against operator-pinned
Radarr/Sonarr `/api/v3/movie`, `/api/v3/series`, and `/api/v3/qualityprofile`.
It refuses redirects, partial/malformed/oversize responses, and strips raw
upstream fields including media paths, secrets, download commands and config
details. A live GET is **not** a 30-minute static host snapshot; cursors pin the
validated content digest rather than the per-request collection clock.

**No production activation or API credentials are included in this source PR.**
The existing Control Worker still does **not** inject an ARR provider or any
additional API secret, so all ARR selectors remain unavailable. Before a
separately authorized deployment, the operator must review private owner
authentication, an independent read grant per service, sustained network access,
secret isolation (preferably outside the broad Control worker), and **explicit
title/profile retention in private GitHub Issues and durable host ledger**.
Do not treat a hardcoded `principal="owner"` in a source-only injected query
as sufficient authentication for a production title-disclosure grant.

The following **effect** phase is still separate: a typed, exact-ID change plan
with current-state readback, explicit scoped user approval, durable reservation,
one bounded ARR update and independent read-after-write verification. Even
`monitored=true` may cause automatic downloading. No ARR PUT/POST/DELETE,
settings mutation, bulk operation or movie acquisition is authorized by this
GET-only implementation. This source milestone does not replace domain-owner
review of Radarr/Sonarr API semantics or the existing PAB #38 Seerr workflow.

## Source-only typed effect-plan boundary (2026-11-10)

The source-only `effect_plan.plan_effect` pure function accepts an exact
resource/operation/target proposal and a **trusted operator-constructed**
`EffectGrant`, obtains the current typed field from a trusted injected GET
provider, then returns a five-minute expiring plan with expected/desired
values, principal, policy version and a SHA-256 plan hash. The same planning
path handles distinct typed resource/field combinations; it does not install a
handler for each user utterance. It refuses unchanged targets, invented fields,
unsanctioned operations, wrong callers and malformed/ambiguous reads.

**This is not an approved effect or an executable capability.** No live
request can invoke it yet; it does not authorize the user, persist a durable
reservation, apply an API mutation, provide read-back, contact a host or spend
OpenAI API credit. Those follow-on requirements belong to Issue #83, with
separate reviewed operator grants and exact user authorization.


## ARR read-only host snapshot integration — source-only activation candidate

This is the **same** `media.resource_read` capability and `ResourceQueryBroker`
introduced in PR #95, not a second worker or a per-question handler. With
source-only `arr_snapshot_bridge.py`, an **independent trusted host one-shot**
fetches GET-only `items` and `profiles` from one fixed operator-pinned
Sonarr or Radarr instance using its locally managed key. It independently
validates each sanitized schema, then atomically publishes a fixed
`{sonarr,radarr}-{items,profiles}.json` in a dedicated root-owned directory.
The existing unprivileged Control process receives **only that directory as a
read-only mount at `/arr-audit`**. The Control app has **no** ARR network path,
ARR API secret or Docker/SSH access. This is a bounded READ resource, not an
Effect Broker or a Sonarr library repair.

### Host-only operator configuration (example; illustrative IDs)

The operator installs a private **root-owned mode 0600** file at
`/etc/personal-ai-brain/arr-read/sonarr.json` (or `radarr.json`;
not in a repository):

```json
{
  "service": "sonarr",
  "origin": "http://<TRUSTED_ARR_SERVICE>:8989",
  "api_key_file": "/<OPERATOR-APPROVED-PRIVATE-PATH>/sonarr-key",
  "reader_gid": 1000
}
```

The service is one host-only, non-interactive command:
`python -m personal_ai_brain.arr_snapshot_bridge --service sonarr`
(or `--service radarr` with its independent private config). Restrict its network to
the exact approved ARR address/port, its filesystem writes to the fixed
`/opt/personal-ai-brain/arr-audit` directory, and its credential read to the
approved host-only secret. It must **not** be invoked with user/model-selected
origin/key/path. Root-only publisher unit plus a bounded timer are deployment
choices requiring a separate operator review; do not grant root to a model.
A second independently configured service can publish Radarr using the same
module, with its own operator-managed key; never combine arbitrary endpoints.

**Destination requirements**: owner root, group the Control reader, directory
mode 0750, files owner root/group Control reader mode 0640, atomic replace,
no symlinks, all named paths fixed, directory read-only inside Control. The
Control reader independently checks schema and at most two-minute freshness;
stale/absent/malformed sources are denied. Run a real second-generation
publication test and check file visibility across the read-only mount (a
single-file bind can pin a stale inode). Do not copy the full raw ARR response:
the published snapshot contains only the preapproved bounded fields.

### Explicit Control runtime gates (all OFF by default)

The existing `PAB_MEDIA_RESOURCE_ENABLED=1` master must remain set for media
resources. Then **per-service independent grants**:
`PAB_ARR_SONARR_ENABLED=1` and/or `PAB_ARR_RADARR_ENABLED=1` attach
only the fixed mounted-snapshot providers. Additionally
`PAB_ARR_DISCLOSURE_APPROVED=1` must be set by the operator only after the
owner explicitly consents to **names, IDs, monitored/profile flags and titles**
appearing in *persistent private GitHub Issues/results* and the host's durable
Control audit/ledger, where authorized repository collaborators may have
read access. The HMAC cursor secret is a separately operator-mounted
`/run/secrets/pab_arr_cursor_key` (32–64 bytes; **never** use the GitHub
token as the HMAC key). An absent/invalid secret or disclosure grant fails
startup closed. The operator must check that the previous Control image,
mounts, credentials, Docker security parameters and ledger are preserved
before turning on any flag.

The existing private GitHub transport binds ARR title reads to the
**authenticated issue author** `id=55916200/login=igormego`; the
user-controlled `requested_by` field cannot substitute for it. This check
applies to both `arr/sonarr` and `arr/radarr` before durable execution.
All other existing media reads retain their prior behavior. The same
`resource_id/view/limit/cursor` query contract and content-bound HMAC
pagination works across both service families, and it is always read-only.

### Exact external acceptance gate

Before claiming `LIVE_ACCEPTED`, inspect privately the actual ARR host API
credentials, originating provider user, secret owner/ACLs, service origin and
network policy; install only the reviewed fixed collector, Control module and
read-only directory mount. Start with the `profiles` view (name/profile ID),
then one authorized `items` page (max 10) through a **normal ChatGPT** private
`media.resource_read` request and one separately correlated result. Verify
the real title-disclosure grant, source freshness, output redaction, signed
pagination, owner-author check, denial for a forged collaborator and missing
grant, and the old `sonarr/overview`, `work.submit/status` and
`homelab.audit` capabilities. Roll back only the added collector/flags/mounts
on failure, preserve all prior durable ledgers; never claim CI alone deployed
a live adapter. **No monitored toggle, download, quality change or API write**
is permitted by this activation. #83 Effect Broker remains separate.

**User-visible limit:** `items` + `profiles` supports audits of monitored
status and profile assignment; it does **not yet** expose episode-level missing
counts, logs or queue failure details. An unverified statement like
“Sonarr is broken because X” cannot be inferred from these two views alone.
Additional diagnostic resources should be added by their resource class and
explicit grant, not with a new tool for every natural-language question.
