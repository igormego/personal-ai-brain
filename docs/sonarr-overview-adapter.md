# Sonarr overview operator adapter

Status: **source-only / not deployed**, PAB Issue [#63](https://github.com/igormego/personal-ai-brain/issues/63).
This implements the operator side of PR #73's existing `media.resource_read`
contract. The host-only runner below is source-only. No new capability, production
activation, scheduler, credential store or Sonarr write operation is added.

## Verified inventory — 2026-11-01

The trusted local-homelab-admin operator used the existing SSH alias and bounded
authenticated GETs. Sonarr is running **4.0.17.2952**, image tag 4.0.17, API v3,
on the existing host loopback port 8989 with no URL base. Forms authentication is
configured; a GET without the key returned 401. The existing key was read only
in host process memory, never printed, copied to the workstation or committed.

GET system/status, series, episode?seriesId, queue and health returned 200.
The inventory contained 64 series and 3196 unique episode records with matching
series identities. A queue page of size 1000, sorted by added ascending and
including unknown-series items, returned all 58 records; statuses were ok/warning.
Health returned one warning. These are historical observations, not live status.

Endpoint semantics were checked against the installed-version upstream source:
[EpisodeController](https://github.com/Sonarr/Sonarr/blob/v4.0.17.2952/src/Sonarr.Api.V3/Episodes/EpisodeController.cs)
and [QueueController](https://github.com/Sonarr/Sonarr/blob/v4.0.17.2952/src/Sonarr.Api.V3/Queue/QueueController.cs).
Series and per-series episodes are unpaginated. Queue is paginated; unsupported
sort keys can fall back, so the adapter uses the verified `added` ordering.

## Boundary and meaning

`SonarrOverviewCollector(existing_key)("sonarr/overview")` is for trusted
operator code only. Its fixed target is host loopback, API v3; the key is sent
in X-Api-Key, never in the URL. Sonarr's key is **not read-only scoped**: the
adapter enforces GET locally. Keep it outside Brain/control and supply only a
sanitized snapshot across that boundary in a separately approved integration.
No URL, route, credential or method comes from the resource selector.

Only the verified exact version is accepted. Proxies and redirects are disabled,
non-200/non-JSON responses fail, duplicate JSON keys and nonfinite values fail.
Each response is bounded to 8 MB, the collection to 64 MB, 256 series and 100000
episodes. Requests have a maximum 10-second socket timeout; a 120-second elapsed
budget is checked between/after requests (not a hard process deadline). A future
operator runner must also enforce an external hard deadline.

Queue uses a **single bounded page** of at most 1000 records. Its page metadata,
total and returned count must agree; larger or partial queues fail closed rather
than silently truncating or claiming zero. A second full queue read checks IDs
and tracked statuses; a second series read checks IDs and monitoring flags.
There are no retries. Concurrent changes can still occur within/between endpoint
reads: this is a bounded observation, not an atomic Sonarr database snapshot.

- `episodes.total`: all returned episode records, including specials/future dates.
- `have`: records with hasFile=true, not a filesystem existence/integrity audit.
- `missing`: no file, both series and episode monitored, known airDateUtc at or
  before collection start. Future/unknown air dates and unmonitored records are
  excluded; missing is a candidate finding, never permission to search/download.
- Queue warning/error counts use trackedDownloadStatus; health counts use type.
- IDs must be unique and episodes must belong to the requested series. Unknown
  statuses, malformed dates/types, missing required fields or read failure reject
  the entire packet. Extra upstream metadata is discarded, never forwarded.
- The existing schema validates the packet, preserves the collection-start UTC
  timestamp and 30-minute freshness limit, caps findings at 12 and marks truncation.

Existing Media Automation policy remains authoritative: 2160p-first/1080p fallback
where configured, no mass upgrades/reacquisition, automated Usenet/manual CZ/SK
torrents. The adapter does not alter monitoring, profiles, files or queue entries.

## Source-only private snapshot consumer (2026-11-01)

The read-side control bridge is now staged in source, not deployed:
`media_snapshot.read_media_snapshot` consumes only a fixed operator-mounted
`/media-audit/snapshot.json` path. It requires a small (<=4096-byte),
single-linked regular file, private 0600 permissions, an expected owner UID
(root or the container process UID), strict unique-key JSON and a complete,
fresh (<=30-minute) `sonarr/overview` schema. The existing Registry
`media.resource_read` then returns only typed, bounded sanitized facts.
Missing, partial, stale, malformed, symlink or exposed files fail closed.

`control_worker.serve` opts in **only** if trusted deployment configuration
sets `PAB_MEDIA_RESOURCE_ENABLED=1`; no end-user arguments or paths can
enable the capability. The default production startup is unchanged. Only
the normalized snapshot may enter control; never mount the Sonarr API key,
collector environment or configuration into the worker.

The source runner and candidate preparation below implement the next source
pieces. They remain uninstalled and have not executed against production.

## Next gate and rollback

After source review/CI, separately approve a host-only pinned collector runner,
existing-key access, private atomic snapshot publication and a credential-free
read-only snapshot provider/mount in control. Review ownership, external deadline,
freshness failure and exact image/Compose rollback before any deployment. Do not
pass the Sonarr key into control or silently update the production checkout.
Then explicitly authorize one correlated real resource read and plugin exposure.
Source rollback is reverting this adapter change; runtime rollback is currently
unnecessary because nothing was deployed. Keep #63 open for real user-facing
read acceptance and separately scoped future mutations.

Offline validation: `python -m pytest -q tests/test_sonarr_overview.py tests/test_assistant_media_resources.py`.

## Runner and deployment preflight — 2026-11-01

**Verdict: BLOCKED for production activation; source/offline preparation ready.**

Read-only host observations:

- Existing control image/rollback is
  `sha256:1ca7a20c4de75abe60c2d75037d943a960b438cf2a1c0829f6d501370541f538`,
  healthy, unchanged start time 2026-10-30T12:04:18Z, user 1000:1000.
- The accepted control.py/control_worker.py/host_audit.py bytes were read from
  the running container without execution and match retained acceptance evidence.
- Existing private state parent is UID1000/0700. Planned Sonarr directory does
  not exist. A future UID1000 directory0700/file0600 is compatible with PR #75's
  strict eUID ownership check without ACLs/group grants or control UID changes.
- Existing Sonarr credential source **and its immediate directory are 0775**,
  UID/GID1000. The runner rejects group/world-writable credential sources. No
  chmod, key extraction/copy, configuration change or privileged bypass occurred.
  This is a real prerequisite, not a reason to weaken the loader. Before staging,
  the owner must approve an exact existing-source permission remediation after
  verifying Sonarr access; no recursive permissions change or new credential.
- Docker 29.7.2, Compose 5.5.0, systemd 259.5, host Python 3.14.4. Existing host
  audit timer remains active. No new Sonarr service/timer or snapshot exists.
- Read-only `compose config` with the existing protected deploy.env and both
  accepted files plus an in-memory hypothetical override preserved all original
  mounts/security/other services. Only the media enable flag and read-only mount
  were added; the current image was used solely for this structural check.
  This is **not** a rendered activation override or a tested candidate image.

### Host-only execution boundary

`sonarr_snapshot_runner` accepts operator-owned config/directory paths, never
request arguments. It reads no key in the parent. A forked child validates the
bounded existing XML config, rejects symlinks/hardlinks, wrong owners, writable
source/parent, duplicate keys, DTD/entities, wrong port/base, then calls the
existing collector. Key stays in child memory; no key argv/env/copy, Python/FD
diagnostic output, or core dump. Parent receives at most 4096 sanitized bytes.

The parent kills a noncooperative collector with SIGKILL after **150 seconds**,
waits/reaps it and never publishes timeout/partial output. The optional source-only
oneshot unit adds a 160-second whole-service timeout, 2-second stop timeout and
control-group kill as a second boundary. No timer is supplied or enabled.
The unit permits only loopback networking, preserves ProtectHome/ProtectSystem,
and maps only the pinned code, existing config and private output directory.
Actual namespace/BPF enforcement remains an isolated staging acceptance check.

Output directory must preexist, owned by runner UID, exactly0700. Descriptor-relative
publication pins it; a nonblocking private lock rejects overlaps. Existing target
must be regular, single-linked, same owner and0600. New temporary file is0600,
fsynced, atomically renamed to snapshot.json and the directory fsynced. Collection
failure leaves old bytes/timestamp untouched; expiry still fails closed after30m.
A post-rename directory-fsync error is **uncertain durability**: a complete new
packet may exist. Inspect it; never silently retry or restore. Crash leftovers
are private evidence, not automatically pruned.

`scripts/prepare_sonarr63_candidate.py` creates separate local review artifacts:

1. Minimal control image context: exact accepted base module hashes are mandatory;
   only control.py, control_worker.py, assistant_media_resources.py and
   media_snapshot.py are added/replaced. It deliberately excludes main's unrelated
   media.plan_movie branch, host collector and credential loader. Dockerfile inherits
   the current immutable base and changes no user/command/dependencies/other files.
2. Optional `--runner-output` builds a six-file host bundle with SHA-256 manifest,
   including isolated `python3 -I -B run.py` bootstrap. It has no control code or
   secrets and does not depend on the production checkout or host package install.

**No image has been built.** Manifest `candidate_image: null` is intentional.
The local environment has no Docker executable; building/tagging/exporting on the
Homelab would violate this milestone's read-only-host limit. The generated pinned
base build recipe must be tested against the approved builder in isolated staging;
record its actual resulting image ID before rendering the Compose template.

### Exact activation gates and rollback

1. Separate owner approval of the exact credential-source/parent permission fix
   and isolated staging (not production). Recheck current hashes/UID/health first.
   Do not copy the key or relax loader checks. Verify native and unit-private source
   paths; a safe /run alias does not prove the original source directory is safe.
2. Stage the hash-pinned host bundle in a new private Sonarr-only directory under
   the existing PAB state parent and create its separate snapshot subdirectory0700.
   Do not reuse/modify the host-audit directory/timer. Render and verify the optional
   oneshot with actual paths before any unit installation. No scheduler implied.
3. Build the minimal control context offline from the verified local base; no pull,
   checkout update or dependency installation. Record image ID, inspect effective
   user/entrypoint/modules/security and test the existing capabilities plus media
   reader against synthetic fresh/stale/invalid snapshots in isolated containers
   without credentials, production ledgers or network. Stop on any baseline drift.
4. Separately approve first host collection and a second bounded invocation at
   least one minute later. Require valid advancing timestamps, preserved0700/0600,
   correct UID and byte-stable old snapshot on injected **offline** failures. Test
   stale rejection with synthetic time/data; do not corrupt production snapshots.
   No live GitHub request is authorized by this preparation milestone.
5. Render `ops/sonarr63/compose.override.yaml.in` using the **built** immutable ID
   and new directory, layered after both current accepted Compose files with the
   existing deploy.env. Require effective diff: image, PAB_MEDIA_RESOURCE_ENABLED=1,
   and one /media-audit:ro directory bind with create_host_path=false, nothing else.
   Capture current image, Compose/env hashes and ledger/container evidence privately.
6. Present exact rendered hashes, immutable ID, isolated tests, snapshot and rollback
   evidence for **separate production activation approval**. Only then recreate
   control using --no-deps --no-build --pull never; no other service. Verify health,
   read-only source access, original capabilities/ledgers/mounts and existing audit
   timer. A single correlated media.resource_read transport probe and plugin
   exposure each remain separately scoped; do not repeat #67's acceptance suite.

Rollback: stop only any newly introduced Sonarr runner (no scheduler is currently
proposed), let/force its bounded child terminate, and recreate **control only** with
the original two Compose files and unchanged deploy.env, omitting the Sonarr
override. Their existing image pin is the rollback ID above, retained locally.
Preserve all ledgers, new snapshots, source manifests and old images; no down/prune,
volume removal, key rotation, host-audit timer operation or Sonarr mutation.
Remove only newly installed Sonarr unit after stopping it if that unit was part of
the approved rollout, then daemon-reload; no cleanup of unrelated units.

Rollback Compose file hashes observed read-only:

- compose.base.yaml: `dfdb6558d7423b8166137deac2928a2f89950b9f4422d3be0442700de9a5afe6`
- accepted host-audit override: `1d642898fc332241ee48f0190129b9ea78a282f9d675fd9589efecd9343fbc5e`

No production rollout can be called READY until credential permissions, actual
candidate image and isolated unit/mount execution are verified. Keep Issue #63 open.
