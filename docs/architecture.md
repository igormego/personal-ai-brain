# Architecture

## v0.1 — implemented slice

```text
GitHub Project OS
      ↓
GitHubReader (read-only)
      ↓
CoreAgent
      ├─ discovery
      ├─ YAML parsing
      ├─ integrity checks
      └─ attention/state interpretation
      ↓
Markdown / CLI report
```

No LLM, database, scheduler or write-capable tool is required for this slice.

## v0.4A — bounded control plane

```text
Caller → private GitHub Issues adapter → strict control request
       → read-only capability registry → explicit Python handler → Core
       ← correlated sanitized result ← private audit / deduplication ledger
```

Transport, capability and permission are separate. The registry declares risk,
argument validation and effects; only read-only handlers register in this slice.
The Issues worker uses a separate credential, cannot write Project State content,
and has no shell, Docker socket, host administration or dynamic execution path.
Request IDs are reserved durably before invocation. Interrupted execution is not
automatically retried. See [control bridge](control-bridge.md) for exact semantics.

## Target architecture

```text
                         ┌────────────────────────┐
                         │ Human / Chat / UI      │
                         └───────────┬────────────┘
                                     │
                         ┌───────────▼────────────┐
                         │ Orchestrator / Router  │
                         │ policy + task graph    │
                         └───────────┬────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
      ┌───────▼───────┐      ┌──────▼──────┐       ┌──────▼──────┐
      │ Core Agent    │      │ Repo Steward│  ...  │ Domain Agent│
      └───────┬───────┘      └──────┬──────┘       └──────┬──────┘
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                         ┌───────────▼────────────┐
                         │ Tool / Connector Layer│
                         │ GitHub, Supabase,     │
                         │ Homelab APIs, etc.    │
                         └───────────┬────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     ┌────────▼────────┐   ┌────────▼────────┐    ┌────────▼────────┐
     │ Durable State   │   │ Event / Audit   │    │ Memory/Knowledge│
     │ Project OS etc. │   │ log / queue     │    │ DB/vector/RAG   │
     └─────────────────┘   └─────────────────┘    └─────────────────┘
```

## Key boundaries

### Durable operational state

Project/workstream truth lives in explicit durable state such as Project OS YAML and project databases. Chat history is context, not the operational source of truth.

### Agent registry

Each future agent should declare:

- responsibility;
- inputs;
- outputs;
- tools it may use;
- resources it may read;
- resources it may write;
- approval requirements;
- events it consumes/emits.

### Tool adapters

External systems should be wrapped behind narrow adapters instead of giving every agent arbitrary access.

Examples:

- GitHub reader/writer;
- Supabase client;
- Homelab service adapters;
- notification adapter;
- calendar/mail integrations.

### Policy / approvals

Write and action permissions are separate from reasoning capability.

A model being able to decide something does not imply it is permitted to execute it.

### Event layer

A later event bus/queue can turn milestones into workflows, for example:

```text
project_state.updated
→ Core Agent refresh
→ portfolio signal detected
→ Career Evidence Agent evaluates
→ Repo Steward checks documentation drift
```

Do not introduce the queue before there is real event volume.

### Memory & Knowledge

The earlier PostgreSQL + pgvector + Ollama concept belongs here.

Possible responsibilities:

- documents/notes ingestion;
- embeddings and semantic retrieval;
- structured entities/relationships;
- long-term agent context;
- provenance-aware memory.

It must not overwrite explicit operational state with inferred memory.

## Deployment

Core runs in a detached non-root Docker service with manual operations and private
host logs/results. A separate Compose project runs the outbound-only control
poller with its own durable private audit directory. It mounts Core state read-only
and receives the separate Issues and source-read credentials as file secrets.
General scheduling, notification and infrastructure-write capabilities remain deferred.

## Builder execution proposal

[Builder Execution Plane](builder-execution-plane.md) reconciles the verified
Homelab environment and proposes the next Work Runner gate. It does not enable
execution permissions. [Credential inventory](credential-inventory.md) is the
single platform credential metadata authority; values are never recorded there.

## Work Runner v0.1

[Local Work Runner](work-runner-v01.md) implements the accepted builder execution
boundary with one fixed deterministic task, a constrained systemd identity,
per-task Bubblewrap isolation and fsynced task truth/artifacts. It has no model,
remote task intake, GitHub publication or deployment capability.

## Next reviewed slice — guarded Homelab resources and effects (#83)

**Status: architecture contract only, not deployed or authorized host access.**
This is the first security review for the existing
[guarded resource/effect broker issue](https://github.com/igormego/personal-ai-brain/issues/83),
under the [general workspace](general-agent-workspace-v01.md) and existing
[control bridge](control-bridge.md). Do not deploy merely because this text exists.

### Reuse map and trust boundaries

| Existing component | Verified/sourced role | Reuse boundary |
| --- | --- | --- |
| `pab-control/v0.1` private GitHub Issue transport | Correlated request UUID, durable reservation, replay/conflict check, bounded result delivery | Reuse request/ledger/owner-authenticated transport; no second queue or generic shell |
| `control.build_registry` and `media.resource_read` | Source-only opt-in; strict selector currently only `sonarr/overview` | Extend a **typed resource family**, not one handler per question |
| `media_snapshot.py` + host Sonarr overview collector | Credential-free sanitized fixed-path reader and separate host-side GET adapter, not live activated | Preserve host-only credentials, read-only mount and explicit collector admission |
| `workspace.python.v1` (#77) | LIVE_ACCEPTED bounded offline Python sandbox | May analyze explicitly granted sanitized inputs; **no** private host files, network, secrets, Plex, Docker or SSH |
| Homelab Movie Intelligence + Media Automation | Own canonical media.db/identity, Plex availability, Radarr/Seerr and episode diagnosis semantics | Domain adapters and fixes stay with the Media owner; PAB owns grants/audit |
| Notebook Meshnet SSH / temporary browser tunnels | Operator-only access demonstrated via separate machine-local config | Neither path is available to cloud ChatGPT or its GitHub connector |

The first consumers are [movie availability audit #26](https://github.com/igormego/homelab-infrastructure/issues/26)
and [Plex/Dexter evidence #47](https://github.com/igormego/homelab-infrastructure/issues/47).
The historical Plex Watched shelf uses metadata placeholders; it must **never**
be counted as playable real media. ČSFD lifetime-watchlist membership is not
authorization to download. A Plex red-trash item indicates unavailable media
metadata, not independently proven file deletion.

### Read contract: first end-to-end user value

The **caller specifies a typed purpose and narrowly authorized identity**, e.g.
an aggregate movie watchlist/availability view or a selected Plex item / Sonarr
episode. The caller does **not** specify URLs, paths, SQL, arbitrary host
commands, Docker operations, credentials, environment, or HTTP methods.
The trusted broker must enforce:

- A resource class, allowlisted selector schema and immutable source/provider
  version; an authenticated principal and per-resource read grant. `requested_by`
  is audit metadata only, never authorization.
- A fixed host-side adapter using only vetted passive GETs / SQLite read mode
  with live-WAL-safe semantics. APIs whose GET initiates provider searches or
  scans are excluded. Production secrets are never mounted into the control
  container or serialized into Issues.
- Canonical TMDb/IMDb and exact Plex/Sonarr identifiers; ambiguous or
  conflicting identity stays unresolved. Source-specific inventory completeness,
  physical file accessibility, stale timestamps, active download/request status,
  and source-error/unknown distinctions are retained.
- A bounded, sanitized, versioned response with collection time, provenance,
  completeness, fixed category counts and limited per-item evidence. For large
  private title sets, use a **reviewed bounded cursor/pagination contract**
  (no silent truncation represented as completeness); keep raw personal rows
  out of public repositories and CI artifacts. The private transport is not
  itself a public media catalog.
- Explicit operator enablement, read-only data mount and independently accepted
  real ChatGPT request/result correlation. A passing synthetic CI test or a
  working notebook SSH tunnel alone is not host deployment acceptance.

The existing fixed `sonarr/overview` contract remains unchanged unless a
reviewed PR and regression tests explicitly change it. The initial PAB adapter
may provide only aggregates while per-title selectors/pagination are reviewed;
do not invent missing titles or claim a complete cross-system audit from a
partial response.

### Effects: a **separate** permission gate

Read success cannot authorize a write. An eventual fix starts as a **reviewed
immutable plan** with exact target identities, before-state fingerprints,
permitted effects, bounded impact, operator-approved artifact/source version,
expiry, rollback expectations and one-use owner consent. A deterministic trusted
host broker validates those bindings and durably reserves the request **before**
one execution. It then independently reads back the resulting state. Any
ambiguous external outcome remains uncertain, with **no automatic retry**.

In particular, Plex trash cleanup, real media deletion, subtitle edits,
downloads/grabs, Radarr monitoring/quality changes, service restarts and network
exposure are **not authorized** by this architecture review. No unrestricted
ChatGPT-origin shell/SSH, arbitrary script promotion, credentials in GitHub,
new public API or second queue/ledger is introduced.

**Next implementation/acceptance order:** source-only typed resource adapter +
negative tests and required CI → separately reviewed host-only collector/
credential boundary → independently correlated ChatGPT read acceptance for #26
or #47 → later action-specific synthetic/reversible effect acceptance under #83.
The Media owner retains exact domain evidence and desired repair behavior.

### Source-only movie watchlist overview — first reviewed implementation slice

The existing opt-in `media.resource_read` now also recognizes the fixed
`movies/watchlist-overview` selector with **no caller-supplied path, URL, SQL,
command, cursor or execution flag**. A separately approved trusted operator
may later provide one sanitized, complete snapshot; the deployed worker is **not
yet configured to produce or mount one**. This source change alone does not
expose `media.db` or Plex from ChatGPT.

The new versioned `0.1` packet requires all four authoritative sources
(`movie_db`, `plex`, `radarr`, `seerr`) to report `ok`, a fresh collected
timestamp, `complete=true`, counts of catalog total/watched/watchlist/overlap,
Plex's number of **physically playable main-Movies** titles, and an exact
partition of the personal watchlist into `playable`,
`managed_or_requested`, `reviewable_missing`, `unresolved`.
The category sum must equal `catalog.watchlist`; contradictory counts,
missing providers, malformed fields, stale results or unknown extra data fail
closed. The old `sonarr/overview` contract and existing private file reader
remain unchanged. Only aggregates are model-visible in this initial slice.

**Host-side domain contract (not implemented by this PR):** source-specific
adapters must prove Plex accessibility of actual media parts (not the Watched
placeholder shelf), stable TMDb/IMDb identity, current Radarr file/queue and
Seerr request status before assigning categories. Unknown, duplicate or
conflicting matches belong in `unresolved`, never silently in
`reviewable_missing`. Neither a lifetime watched flag nor a watchlist record
is authorization to download. The operator must build/validate the sanitized
snapshot privately and explicitly approve a read-only control mount/deployment;
then a separate ordinary-ChatGPT correlated request must verify runtime
acceptance. Per-title private pagination and any guarded effect require their
own later review; no automatic cleanup, scans or downloads are enabled.


### Fixed-scope Movie snapshot handoff (Issue #83, source-only gate)

The existing `media.resource_read` transport/registry and durable GitHub
Issues request ledger are reused unchanged. The control worker previously
had only one fixed `/media-audit/snapshot.json` file for Sonarr; the Movie
resource now has a **separate**, constant, never caller-supplied
`/media-audit/movies-watchlist-overview.json` path. Those are two **pre-sanitized,
bounded, credential-free** resource files, not a general directory browser.

**Explicit two-level activation** (operator permission/deployment is NOT part
of this PR): `PAB_MEDIA_RESOURCE_ENABLED=1` is the already-existing master
opt-in for the fixed Sonarr reader; a Movie snapshot additionally requires
`PAB_MEDIA_MOVIES_ENABLED=1`. A request cannot override either switch,
select any other file, set a URL or reach a host-side API. When denied, missing,
stale, malformed, too large or overly permissively readable, the resource
fails closed with a fixed error. Reading one resource never falls back to
the other's snapshot; neither enablement flag installs collectors.

**Separate host-side operator acceptance required:** Movie Intelligence owns
its fixed-scope collector and the creation of **one private atomic snapshot**
(no raw titles/IDs and no LLM-facing API keys). A designated operator must
review the exact committed producer/consumer source pins, proof of bounded GET
and read-only SQLite inputs, source freshness, file owner/0600 permissions,
private parent directory, immutable read-only container mount, and rollback
of any added flag/mount. A host collector can run without any control grant.
No snapshot data/host configuration/secrets may be placed in public GitHub.
A passing CI integration fixture does not prove host UID parity, deployed
collector freshness or actual ChatGPT delivery.

**Acceptance gate:** independently verify a fresh operator-published file
and one genuine ordinary ChatGPT `media.resource_read` request with exact
`{"resource_id":"movies/watchlist-overview"}` → authenticated private
control Issue → correlated, bounded sanitized aggregate → duplicate/replay
read-back without changed execution truth. Explicitly verify the original
`homelab.audit` and Sonarr configuration remains unchanged. No resource
read grants any effect. The accepted **125/125 unresolved ČSFD watchlist**
aggregate is evidence of missing canonical mapping, not 125 absent films
or authority to request them.

### Typed private item queries (source-only)

The existing registry can accept an explicitly injected typed query broker.
Its first registered schema is `movies/watchlist`, view `items`, with a
page limit of 1–10 and an authenticated cursor bound to the source generation,
view, limit and fixed owner context. A changed or stale snapshot fails closed;
cursors signed by an earlier process key cannot be resumed after a restart.
The dedicated fixed snapshot reader is separate from the aggregate directory.
No production worker injects this provider or grants this resource.

Private GitHub Issues comments and the host execution ledger persist request
results. A private mailbox does not make title-level replies ephemeral.
Enabling real item reads therefore needs separately reviewed owner approval
of disclosure, retention and access, a dedicated validated 0700/0600
snapshot producer and read-only mount, independent resource grants, cursor
key lifecycle and authenticated-principal binding. The `owner` label in the
source-only injection is not authentication. The existing worker redactor
may redact secret-like title text; no title is an effect or download command.
No private title snapshot, grant, plugin update or deployment is included.
