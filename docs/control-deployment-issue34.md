# Pinned Homelab control deployment (Issue #34)

**Verified 2026-10-27:** the existing control worker uses a durable Compose
configuration and immutable local image ID. This is an overlay on the preserved
runtime baseline, not a deployment of current main. No Windows worker changes.

| Role | Immutable local image ID |
| --- | --- |
| Accepted control | `sha256:06d57e7a8f32995bddc077dcbd94dad850ace15c531914bf38626e08c3484770` |
| Guard-preserving fallback | `sha256:b9ddc1c9e32244dff975dc6eca7e0eac04b39a2244ed1b4dad28b88d527125b1` |

These are local Docker image IDs, not registry manifest digests. Both images must
remain on this host. Do not prune them. The original image is also retained.
The fallback retains request/payload conflict protection but lacks
`project_os.status`. Rollback changes code only; never restore an older ledger.

## Durable host files

The operator-owned directory `~/.config/personal-ai-brain/control-deployment-34/`
is mode 0700; its files are mode 0600. It contains:

- `compose.base.yaml`: frozen copy of the verified deployed baseline Compose.
- `candidate.override.yaml`: [accepted pin](../docker/control-issue34.override.yaml).
- `fallback.override.yaml`: [guarded fallback](../docker/control-issue34-fallback.override.yaml).
- `deploy.env`: existing UID/GID and absolute mount/credential **paths only**.
  No credential values or copied credential files.
- Private hash/metadata verification evidence, without token values.

The base snapshot preserves the existing service security configuration and
mounts. The effective configuration differs only by removing `build`, setting
an immutable image ID and `pull_policy: never`. Its resolution was compared for
both images with Docker Compose 5.5.0. `!reset` requires a compatible Compose
version; validate `config --quiet` before any future operation.

The source checkout remains clean at `eeb749c89937810829b48b9f5697c70dd4e424cf`.
Do not invoke `scripts/control.sh up`: it builds the older checkout and bypasses
this pin. The stable files are independent of that checkout and of `/tmp`.
Old staging files are retained for evidence; the running container's Compose
labels no longer reference them. Future approved changes must update the pin
and this runbook together, retaining the prior image and current ledger.

## Operator commands

Run only with authorized Homelab maintenance access. Read-only preflight:

```sh
d="$HOME/.config/personal-ai-brain/control-deployment-34"
docker image inspect sha256:06d57e7a8f32995bddc077dcbd94dad850ace15c531914bf38626e08c3484770 --format '{{.Id}}'
docker compose --env-file "$d/deploy.env" --project-directory "$d" \
  -p personal-ai-brain-control -f "$d/compose.base.yaml" \
  -f "$d/candidate.override.yaml" config --quiet
```

**Mutates only the control service:** reconcile/start the accepted pinned image.
Append `--force-recreate` before `control` only when a deliberate recreation is
required. Verify idle execution and retain hash/permission evidence beforehand.

```sh
docker compose --env-file "$d/deploy.env" --project-directory "$d" \
  -p personal-ai-brain-control -f "$d/compose.base.yaml" \
  -f "$d/candidate.override.yaml" \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 control
docker exec personal-ai-brain-control-control-1 \
  python -B -m personal_ai_brain.control_worker health
```

**Rollback mutation:** after verifying the fallback image is present, run the
same command using **only** `fallback.override.yaml` in place of the candidate
override. Keep the same project, base, environment, credentials and ledger.
If a request is uncertain, preserve its reservation; never automatically retry
it or alter its ledger. Reconcile health before further requests.

## Acceptance evidence

- Initial container `323dd62de0ec` replaced with `433f9c2f635b` at
  20:55:34 UTC; the image ID remained unchanged. A second graceful stop/start
  at 20:56:57 UTC repeated the queued-request check after correcting the test
  caller's timestamp format. Runtime and transport both healthy afterward.
- All 73 original audit files (32 requests, 41 issues) retained identical bytes,
  ownership, modes, inodes, sizes and modification times. Credential metadata,
  effective mounts and security limits were unchanged. All 15 unrelated
  containers retained IDs, images, start times and restart counts.
- Private transport #75 replay reused existing truth with no execution;
  #76 changed payload returned `request_conflict` with no execution. Original
  request ledger hash/metadata remained unchanged across both restarts.
- Valid requests #77 (`project_os.status`) and #78 (`runtime.health`) queued
  while stopped completed after startup. Each had one correlated result comment
  and closed. Exactly two new request execution records were added.
- #73/#74 were rejected without execution: the test caller initially emitted
  seven fractional timestamp digits; the protocol accepts at most six. These
  audit records remain preserved. Final totals: 34 request and 47 issue records.
- **Component-tested:** 44 existing control tests passed against the exact
  deployed image, network disabled, non-root/read-only, no production mounts.
  This covers interrupted reservations and delivery retry without reexecution.
  Earlier staging also verified candidate/fallback ledger roundtrip (Issue #34).

Live evidence proves graceful service restart, durable ledger replay/conflict
and queued delivery recovery. It does not claim a whole-host reboot, a forced
mid-handler production crash, or an injected ambiguous GitHub POST. Those
failure paths remain isolated component evidence; do not manufacture them by
editing production ledgers. Independent ChatGPT acceptance #54-#58 remains the
external caller evidence. Gateway adapter Issue #25 is a separate open gate.
