# Reviewed release activation (#83, #77)

SOURCE ONLY. Existing Control transport, effect.execute, approval HMAC, Unix
socket and read-back contracts are reused. This is a concrete reusable effect,
not generic root shell, not a new agent/queue. The effect switches an atomic
current symlink to a pre-staged immutable application generation. It never
executes application code, changes permissions, invokes sudo or starts services.
A separate fixed non-root application unit may consume the stable pointer.
Previous releases are retained; rollback is another reviewed signed plan.

The owner-approved registry maps logical release slots and numeric generations
to exact SHA256 manifest digests. Its canonical SHA256 is the effect policy
version: any registry change invalidates old approvals. Each manifest maps all
regular package files to SHA256; unexpected files, symlinks, hardlinks,
non-immutable ownership/modes, mismatches and size violations are denied. A
host-private durable request reservation precedes each attempted effect, even
if the prior current generation returns after rollback. A partial/duplicate
reservation never retries. Control's existing correlation ledger remains
authoritative for returned status. A failed/uncertain effect needs read-back.

## One-time operator bootstrap (not executed by merging this PR)

Respect current #83 staged ARR cutover; do not replace the existing Control
image or builder blindly. Coordinate one reviewed activation with the Runtime
owner. Verify installed versions, exact rollback pins, private ledger mounts,
no running tasks, and actual local SSH. Use existing local-homelab-admin.
Provision a dedicated non-root pab-release service user, a narrowly shared
pab-effects group for the socket peer, and the root-owned trusted module tree
/opt/personal-ai-brain/effects/src. Do not add Docker, sudo, SSH keys or network.
Install reviewed deploy/pab-release-effects.service only after checking the
effective systemd constraints. The reviewed systemd unit provisions /run/pab-effects as service-owned,
peer-group, setgid 2750, recreated on service restart. Verify its effective
mode, normal SIGTERM socket cleanup and retained directory inode on restart.
Its only socket is effect.sock. A crash may leave a stale socket: stop and
reconcile locally; never unlink an unknown live socket. Mount the socket
**directory** read-only into Control, not the socket inode, so normal service
restart can recreate the listener. The actual Control
UID is verified locally and configured, never guessed. The service requires
a private 0700 state root, slots and reservations owned by pab-release;
each slot directory 0700, lock file 0600, current symlink pointing to the
exact initial registered release. All production consumers must use a
separately granted read-only view of the pointer/package; if consumer UID
differs, plan a reviewed publication/read surface rather than loosening
the private state tree. A synthetic slot is the first acceptance.

Root-owned /etc/personal-ai-brain/effects has releases.json, peer.json and a
32–64 byte verification.key root:pab-release mode 0640, using a private
supplementary group distinct from the shared socket peer group (no key
in Git/Issues). Control must not belong to that private group. The installed local-only `release_admin` module is the approval signer.
Run as the authorized operator under the dedicated service identity (with its
private key group). No Control or Work Runner invocation of this CLI.
`python3 -m personal_ai_brain.release_admin prepare demo 2` emits a review
packet with exact plan, request ID and current/candidate manifest digests.
Review that packet, then pass it on stdin to
`python3 -m personal_ai_brain.release_admin approve --confirm <plan_hash>`
within its five-minute validity. It rechecks both releases and emits only
the scoped request ID and effect arguments. The existing private Issues
transport can submit those arguments; never transfer the HMAC key.
This deliberately still requires independent approval; no unattended signer
or policy-managed artifact publisher is activated here. Example
registry (synthetic only):
    {"schema_version":"0.1","slots":{"demo":{"target_id":1,"generations":{"1":"<manifest SHA256>","2":"<manifest SHA256>"}}}}
peer.json contains exactly {"allowed_uid": <verified Control UID>}.
Root-owned package tree /opt/personal-ai-brain/releases/demo/1 and /2 contains
manifest.json with {"schema_version":"0.1","files":{"main.py":"<file SHA256>"}}.
Root publishes reviewed, immutable bytes; do not execute model workspace
artifacts or grant workspace writes to this tree. Source pin/CI and staging
of real application bytes remain separate operator-approved steps.

Start service and verify actual socket ownership/peer denial, valid signed
synthetic switch, exact read-back, rollback, old receipt replay and crash
retention. Verify no root/Docker/network access. Only after that review the
Control-only image delta with fixed /host-effects/effect.sock socket mount and
PAB_EFFECT_SOCKET_ENABLED=1; default is off. Do not mount the key, package or
reservation tree into Control. Preserve all existing seven-layer rollback
pins, ledger and Work mounts, and validate old capabilities. Use one real
ordinary-ChatGPT correlated effect.execute with separately approved receipt.
Host service/Control activation, release publishing/signing and application
timer/config bootstrap are not implied by source CI.

## Operational direction

SerialZone #66 is a real consumer once its source is host-accepted and the
application runner/schedule and approved immutable release promotion are
connected. New applications reuse registered release slots rather than
per-question tools. No arbitrary remote shell, unreviewed Internet, self-grant,
automatic sudo or install of arbitrary GitHub code. The scoped Work Runner
PR #114 remains the general data/staging lane. Keep #83 and #77 as authorities;
no parallel agent, broker transport or general administrative service.
