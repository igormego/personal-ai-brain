# Sonarr overview operator adapter

Status: **source-only / not deployed**, PAB Issue [#63](https://github.com/igormego/personal-ai-brain/issues/63).
This implements the operator side of PR #73's existing `media.resource_read`
contract. No new capability, production Registry wiring, CLI, scheduler, credential
store, or Sonarr write operation is added.

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

**Operator prerequisite (not implemented in this source change):** create
a pinned, host-only collection runner with existing-key access, a hard external
deadline, private atomic snapshot publication, and a reviewed fixed read-only
container mount. Validate file ownership compatibility with the control UID,
existing control rollback image, complete failure/staleness behavior, and
read-only bind semantics. Prepare the exact immutable image/Compose diff before
asking for separate owner approval to deploy. No API key, network reachability
to Sonarr, or elevated Docker access should be added to control.

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
