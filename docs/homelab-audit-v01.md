# homelab.audit v0.1 — host snapshot, not an admin API

**Status (2026-10-29): LIVE read-only control capability; snapshot refresh is manual.**
Source milestone [#65](https://github.com/igormego/personal-ai-brain/issues/65)
was followed by an explicitly authorized control-only operator rollout. Independent
private GitHub verification confirmed authenticated `homelab.audit` results
[#84](https://github.com/igormego/personal-project-brain/issues/84) and
[#88](https://github.com/igormego/personal-project-brain/issues/88), invalid-input
and conflict denials #85/#86, and post-restart duplicate #87 without reexecution.
[Issue #67](https://github.com/igormego/personal-ai-brain/issues/67) remains OPEN
for the final operator evidence of the running image and post-replay host/ledger
invariants; GitHub results alone do not attest current Docker state. The snapshot
expires after 30 minutes unless manually refreshed; no scheduler exists. A
stored preactivation `control.image_id` is **not** proof of the current image.
The existing ChatGPT plugin is still Project OS status-only: no `homelab.audit`
plugin tool, host administration, Docker socket or autonomous action was enabled.

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

## Operator acceptance procedure (historical gates; final closeout pending)

The first controlled rollout and live transport checks completed on 2026-10-29;
the operator's final post-replay host/ledger attestation is still pending in #67.
The steps below preserve the approved procedure for future reviewed changes; **do
not repeat the already verified acceptance requests or redeploy just to close #67**.

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
6. Only once the live control path is accepted, expose the new status capability
   through a reviewed ChatGPT plugin/adapter update. Do not claim it is already
   callable by the current Project OS-only plugin, or that the independent
   Brain v0.5 LLM tool schema already supports it.

**Boundary:** source acceptance is not production deployment or authorization to
run a privileged system service. The next goal-driven Operator Agent can consume
this summary without requiring a separate laptop, but must not self-enable
additional tools or write to the host.
