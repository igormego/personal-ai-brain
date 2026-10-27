# Owner-reviewed retained patch staging v0.1

Issue #46 adds a separate trusted **operator CLI**, not a worker capability:

```powershell
python -m personal_ai_brain.codex_bridge_review <successful-task-uuid>
```

Run only from reviewed operator code, using the existing local owner environment.
There is no mailbox handler, push command, approval flag, GitHub token or PR API in
this tool. Remote coding tasks cannot invoke publication. The existing workers,
source clones, original WSL patches and ledgers are read-only inputs.

The CLI selects a single-attempt succeeded task from the protected v0.2 ledger,
checks a delivered successful correlated result and repository identity, collects
the actual tracked regular-file text patch **inside the accepted WSL sandbox**,
re-runs the trusted fixed sandbox validator and compares the real files/counts
with ledger evidence. It verifies the patch before/after tests and audit snapshots.
New/untracked files, staged changes, remotes, symlinks, binary data and unsupported
Git states fail closed in this first exporter. No repository code executes in
Windows, where publishing credentials might exist. Secret-pattern scanning is an
additional heuristic; it cannot replace owner review of the complete diff.

Packets live at `%LOCALAPPDATA%\PersonalAIBrain\patch-review-v01\<task-id>` under
protected owner/SYSTEM/Admin ACLs. Each includes `REVIEW.md`, `manifest.json`,
`patch.diff`, and an isolated `checkout`/`owner-review/<task-id>` branch at the
original commit, with no remote and no new commit. Only verified patch data is
applied; no existing source/development/task directory is changed. A source clone
with the wrong base, origin, branch or dirty state is rejected. Read-only public
GitHub metadata records current main: when it differs, the historical review
packet is retained with **stale_base** blocking publication. It is not a repaired,
rebased or publication-ready checkout.

Repeated preparation revalidates ledger/workspace evidence and the packet and
checkout checksums. A partial packet, changed manifest, modified checkout or
hash conflict stops for operator inspection; it is never overwritten/repaired.
A local lock serializes packet creation. No uncertain publishing operation can be
retried because publication is not implemented in this source milestone.

## Owner approval and next milestone

Each future publication approval must explicitly bind the repository, task ID,
original base, exact patch SHA256 and proposed branch. Approval of the staging
implementation is not approval to publish any coding patch. The initial retained
PAB/Homelab packets remain unpublished until separate per-patch approval.

Before a future trusted publisher is enabled: verify current repository/main and
reviewed patch again; reject stale base without automatic rebase; bind approval to
immutable content; persist branch/commit/PR identity before writes; reconcile
existing remote truth after interrupted push or ambiguous PR creation; stop on
uncertainty instead of retrying publication. Publishing credentials must remain in
the separate operator environment, never WSL task workspaces or coding processes.
Only draft PR creation is in the future scope; no automatic merge or deployment.
Neither the CLI nor this document installs such a publisher.
