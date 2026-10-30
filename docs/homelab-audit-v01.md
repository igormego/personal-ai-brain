# homelab.audit v0.1 — host snapshot, not an admin API

**Status (2026-10-30): LIVE read-only control capability, operator acceptance CLOSED; snapshot refresh remains manual.**
Source milestone [#65](https://github.com/igormego/personal-ai-brain/issues/65)
was followed by an explicitly authorized control-only operator rollout. Independent
private GitHub verification confirmed authenticated `homelab.audit` results
[#84](https://github.com/igormego/personal-project-brain/issues/84) and
[#88](https://github.com/igormego/personal-project-brain/issues/88), invalid-input
and conflict denials #85/#86, and post-restart duplicate #87 without reexecution.
[Issue #67](https://github.com/igormego/personal-ai-brain/issues/67) was
**closed on 2026-10-30** after the trusted local operator independently verified
the actual approved running image, healthy runtime/transport, unchanged 91 recovery
ledger files, seven correlated subsequent records (#87–#90) and preservation of
15 other containers. [Final operator attestation](https://github.com/igormego/personal-ai-brain/issues/67#issuecomment-6522448073)
documents the host read-only evidence. The stored snapshot collected at
2026-10-29T19:02:22Z is **expired and correctly rejected**; no automatic refresh
exists. A stored preactivation `control.image_id` is **not** proof of the
currently running image. Private ChatGPT plugin **v0.2.0** supports foreground
read-only `project_os.status` and `homelab.audit` via the existing GitHub
control transport; it does not grant host administration or Docker access.

## Why two trust zones?

The running personal-ai-brain control container is intentionally non-root,
read-only, without a Docker socket or administrative SSH key. Giving the LLM
general host tools or mounting `/var/run/docker.sock` would break that boundary.

Instead:

1. **Host-side operator-owned deterministic collector** runs outside the agent
   container with separately authorized local host permissions.
2. The collector makes only fixed read-only local probes: `/proc/meminfo`,
   `os.cpu_count()`, `statvfs` for root and separately mounted `/data`,
   Docker `ps --format '{{.Status}}'` and **one named control-container**
   `inspect` using fixed selected Go-template fields. Docker does not receive
   arbitrary model input. Collection emits no container names, URLs, mounts,
   environment, commands, hostnames, IPs, serial numbers, key paths or secrets.
3. Collector produces a **strict allowlisted summary** in a private JSON file,
   using atomic replacement, file mode 0600 and parent directory 0700.
4. A separately reviewed operator activation bind-mounts that directory **read
   only** into the existing control container as `/host-audit` and sets
   `PAB_HOST_AUDIT_ENABLED=1`. No default compose change is included.
5. Only then the control worker registers read-only `homelab.audit` with
   **exactly `{}` arguments**, serving the private source snapshot. The
   snapshot is not a live Docker query.

The collector's Docker CLI access is itself a powerful host permission.
Provision only a trusted deterministic collector identity with an operator-reviewed
scope. Docker's group/socket access is effectively host-root-equivalent; do not
share that identity/socket/credential with Brain, the control container,
the GitHub Issues caller, or any untrusted code. If safe collector access cannot
be established, leave Docker fields `unavailable` and do not enable it.

## Returned fields (fixed schema v0.1)

- `collected_at`: UTC second-precision timestamp, no older than 30 minutes and
  no more than two minutes into the future.
- `cpu_logical`: logical CPU count.
- `memory_mib.total/available`: memory size/headroom.
- `disk_free_gib.root/data`: free space on root and, if mounted, `/data`.
- `docker.status/running/unhealthy`: numeric container summary only; unknown
  counts are `null` when Docker cannot be safely queried.
- `control.status/image_id/read_only_rootfs/privileged/no_new_privileges/
  docker_socket/published_ports`: the **one fixed** control container's
  security posture; unknown fields are `null`. The immutable local image ID
  is not necessarily a registry manifest digest.
- `freshness=current`, `source=host_collector_snapshot_not_live_probe`:
  added by the verified reader, not trusted from stored input.

Snapshot bytes are capped at 4096. Duplicate JSON keys, unknown keys,
NaN/Infinity, invalid types, bad image IDs, symlinks, untrusted ownership,
group/world-writable snapshot files, stale results, missing data and oversized
files are rejected. Rejecting a snapshot never prompts a model to run Docker.
Only the existing private request/reservation/result protocol writes
its own durable audit metadata.

## Developer/offline acceptance

```sh
python -m pytest -q tests/test_host_audit.py tests/test_control.py
python -m compileall -q src tests
```

Tests inject fake Docker responses, verify the exact fixed queries and validate
the full existing `pab-control/v0.1` duplicate/replay and fail-closed status.
No live Docker, SSH, credentials or paid LLM inference is used in tests.

## Revised timer design after operator preflight (2026-10-30)

[Issue #71](https://github.com/igormego/personal-ai-brain/issues/71#issuecomment-6522853626)
found that the original PR #70 service unit was **BLOCKED**: its proposed
output directory does not exist, the accepted collector and snapshot live under
the operator home, and the system Python does not import the PAB module.

**Use the revised template below, not the original PR #70 unit.** It preserves
`ProtectHome=yes` while binding only the **approved, pinned collector file**
read-only and the **already mounted snapshot directory** read-write to
service-private destinations under `/run`. Python runs the pinned standalone
file with `-I -B`, without any new package install or import path. All four
placeholders (approved user, Python executable, collector file, existing
snapshot directory) require trusted host-side verification and rendering.
The same underlying snapshot remains visible to the unchanged read-only worker;
no host file, package, permissions, or mount is changed by this source update.

**Next gate: operator read-only review, NOT activation.** Validate the accepted
collector checksum, script entrypoint, exact existing path identities, unit
namespace mapping, interpreter and security settings on the real host with
systemd 259. Run `systemd-analyze verify` on the exact rendered units in
an offline staging location. If a bind mapping or standalone Python execution
cannot be proven safe, report BLOCKED rather than relaxing sandbox settings.
The two units remain not installed; activation requires a separate owner
approval after a READY report. Do not repeat completed #67 transport tests.

## Optional host refresh timer — source-staged, NOT activated (2026-10-30)

The standalone host-side collector is already supported and the live read-only
worker has been operator-accepted (#67). To remove manual refresh as a recurring
availability dependency, the repository now contains **source-only** systemd
templates:

- [`ops/systemd/pab-host-audit-refresh.service.in`](../ops/systemd/pab-host-audit-refresh.service.in):
  `Type=oneshot`, the **same exact** `personal_ai_brain.host_audit collect`
  executable and fixed private snapshot target. It requires operator rendering
  of `@APPROVED_COLLECTOR_USER@` and an **absolute**
  `@APPROVED_PYTHON@` interpreter path. The service is not installable as-is,
  has no `[Install]` section, writes only the pre-existing private snapshot
  directory, and does not start/restart Docker or PAB control.
- [`ops/systemd/pab-host-audit-refresh.timer`](../ops/systemd/pab-host-audit-refresh.timer):
  after host boot +2 minutes, then every 10 minutes after collection finishes;
  the timer requires a separate, explicit enable/start. It cannot grant any
  read/write permission, collect its own Docker credentials, or automatically
  deploy the worker.

**Preflight and required independent approval before ANY live change:** A trusted
local `local-homelab-admin` operator must inspect actual paths and the **already
accepted snapshot collector version** (avoid silently executing a newer
unreviewed checkout); verify the chosen Python executable works under the
approved collector UID, which must own the existing 0700 snapshot directory and
publish 0600 files; inspect the current Docker access model as *host-root-equivalent*
and **never add a new Docker group/socket/credential grant merely to make the
timer pass**. Confirm `ProtectHome=yes` does not block the selected Python
installation and `ReadWritePaths` matches the real approved directory; if not,
stop and request review rather than weakening the sandbox. Inspect systemd
version/available hardening flags and verify the locally rendered service and
timer with `systemd-analyze verify`. Validate effective unit diff against
source; no shell interpolation, arbitrary arguments, secrets or source checkout
drift. If a host-side permission or service prerequisite is not met, leave the
timer uninstalled.

**Separate deployment gate:** Once the owner explicitly approves the exact
rendered unit files, UID, interpreter and rollback, the trusted local operator
may stage/install **only** those two files, reload systemd and enable/start
**only the timer**. Do not run `docker compose up`, change the immutable
control image, mount, source code, ledger, other containers, or ports. After
activation, verify a fresh timestamped, strictly validated snapshot and
subsequent scheduled collection. The control container keeps a read-only
`/host-audit` mount and no Docker socket. The timer alone cannot assert a
live measurement; every result is still the **time-stamped last collection**.

**Failure and rollback:** failed/late collection must **not** overwrite the
last valid file or trigger automatic escalations/restarts; the existing
30-minute reader freshness gate remains authoritative. On any unit failure,
unexpected permissions, identity/source mismatch, or unsafe output: disable/stop
**only the timer** and restore the previously approved unit state; preserve
the private snapshot, control image, ledgers and evidence. Do not suppress
staleness or silently retry requests to bypass source failure.

Offline checks: `python -m pytest -q tests/test_host_audit_timer.py
tests/test_host_audit.py`. These verify the source-only configuration and the
existing fail-closed snapshot reader, **not live unit compatibility or host
activation**. Nothing in this source milestone enables a scheduler or widens
model/host permissions.

## Operator acceptance procedure (historical; accepted and closed)

The rollout, live transport checks and recovery completed on 2026-10-29;
the final read-only host/ledger attestation passed and #67 closed on 2026-10-30.
The steps below are **historical acceptance guidance**, not instructions to
repeat tests or redeploy. Any new scheduler or privileged host-side change still
requires separate operator review and authorization.

1. Recheck current PAB and Homelab `AGENTS.md`, inspect deployed host checkout,
   actual pinned control image and Compose override per
   [control deployment #34](control-deployment-issue34.md). Do **not** use
   `scripts/control.sh up` against the old checkout.
2. Before mutation, verify host permissions/resource availability, private
   collector identity and a rollback to the exact existing image/Compose pin.
   Operator must explicitly approve the eventual unit/timer or scheduled task,
   source version, mount and service redeploy. No unreviewed Docker privileges.
3. Stage collection **outside** the running control container using the exact
   approved version. The operator-owned directory is private (0700), collector
   snapshot mode 0600; ensure the unprivileged control UID can read the
   intended bind mount without opening the directory to the world. The
   host-side command is:
   ```sh
   python -m personal_ai_brain.host_audit collect \
     --output /var/lib/personal-ai-brain/host-audit/snapshot.json
   ```
   This **writes one local private snapshot**; it never changes Docker state.
   Parent directory must already exist. It must be executed only under the
   operator-approved identity; no automatic scheduling is installed here.
4. After inspecting the exact staged file and verifying no secret or unexpected
   fields, stage **a separate, reviewed control-only override** with an immutable
   image pin, read-only `/host-audit` bind mount and
   `PAB_HOST_AUDIT_ENABLED=1`. Preserve all original mounts, credentials,
   locks, ledgers, image rollback and other containers. If this requires
   changing the existing protected UID/mount ACL, stop and request approval.
5. Apply only in an explicitly authorized maintenance window. Confirm health
   and control-source parity, then submit one authenticated private Issue
   `pab-control/v0.1` with `capability=homelab.audit` and `arguments={}`.
   Verify expected source timestamp/shape and author/correlation. Test unknown
   arguments, stale snapshot, duplicate/conflict, restart/recovery and rollback
   without re-running destructive operations.
6. After live control acceptance, the private ChatGPT plugin was separately
   upgraded to v0.2.0 for foreground read-only access. Do not conflate this
   with production deployment of Brain v0.7, a timer or backend LLM runtime.

**Boundary:** source acceptance is not production deployment or authorization to
run a privileged system service. The next goal-driven Operator Agent can consume
this summary without requiring a separate laptop, but must not self-enable
additional tools or write to the host.
