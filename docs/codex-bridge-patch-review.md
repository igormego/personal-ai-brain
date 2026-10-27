# Owner-reviewed retained patch staging v0.1

Issue #46 adds a separate trusted **operator CLI**, not a worker capability:

```powershell
python -m personal_ai_brain.codex_bridge_review <successful-task-uuid>
```

Run only from reviewed operator code, using the existing local owner environment.
The original exporter has no mailbox handler, push command, approval flag, GitHub token or PR API. Remote coding tasks cannot invoke publication. The existing workers,
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

## Trusted draft publisher (Issue #46)

`personal_ai_brain.codex_bridge_publish` is a separate **interactive Windows
operator tool**. Neither worker imports it. No service, mailbox handler, credential
installation, sandbox expansion or automatic publishing is added. Run reviewed
operator code in the owner session; never run this CLI from a coding task.

The workflow requires no manual file forwarding: the operator selects a task UUID,
and trusted code loads protected ledger evidence and retained patches directly.

### Read-only preflight and explicit transfer

```powershell
# Local staging writes only; no GitHub writes, commits or owner authorization.
python -m personal_ai_brain.codex_bridge_publish transfer <task-uuid> <exact-current-main-sha>
python -m personal_ai_brain.codex_bridge_publish preflight <task-uuid> <exact-current-main-sha>
```

`transfer` is an explicit operator-reviewed procedure, never an automatic rebase.
It first revalidates the original packet/task. In a new checkout it fetches only
public main, requires the exact supplied head, proves original-base ancestry and
identical affected base blobs, and applies the original patch without changing
its bytes. If affected files changed, stop for a new separately reviewed coding
task; this first version does not resolve conflicts or adapt patches. A new
credential-free WSL workspace receives Git objects and the patch; the accepted
fixed offline validator reruns there. No model executes and neither worker ledger
is involved. Fresh validation must exactly match the original evidence. Both old
and new workspace/packet are retained, and the original packet is never edited.

New packets live under the protected original packet root's `publisher-v01`
subdirectory. A bounded directory key hashes task UUID + full target SHA; the
manifest retains full identity and rejects collisions. `REVIEW.md`, `patch.diff`,
`manifest.json` and `checkout/` describe the new target. The patch checksum can
remain identical when affected blobs are unchanged: **target SHA is independently
bound by approval**. Repeated preflight reruns both provenance and target sandbox
validation. Partial state, stale main, unexpected files or drift stop without
repair. No repository code executes in Windows.

### Explicit owner approval and publication

After the owner reviews the complete target packet and diff:

```powershell
# AUTHORIZATION WRITE: owner types the exact displayed challenge in a real console.
python -m personal_ai_brain.codex_bridge_publish authorize <task-uuid> <reviewed-target-sha>
# EXTERNAL WRITE: only after explicit per-patch owner authorization.
python -m personal_ai_brain.codex_bridge_publish publish <task-uuid> <reviewed-target-sha>
```

There is no `--approve`, stdin approval, Issue approval or arbitrary JSON approval
input. `authorize` requires `CONIN$`/`CONOUT$`, displays the complete authorization
binding, and requires an exact digest challenge. The record is Windows DPAPI sealed
to the owner identity and stored under the owner-protected packet root. It binds
repository, task UUID, original base/checksum, target commit, exact transferred
checksum, validation evidence and exact head/base branches. Reverification occurs
before authorization and publication. Approval of the tool is never approval of a
patch. The Windows owner session and trusted operator source are the authority;
this does not defend against malicious code already running as the same Windows
owner outside the sandbox. Task code has no such execution route or packet access.

The publisher alone acquires the existing Windows Git credential-manager credential
in memory; it verifies GitHub identity `igormego` and exact allowlisted repository.
It never writes credentials to packets, WSL, task context or command lines. No
credential/permission changes or fallback token discovery occurs. GitHub API
redirects are denied. Missing credentials block publication; the CLI does not log
in or create a token. Ordinary preflight does not acquire publishing credentials.

Only after exact approval does a new isolated `publish-checkout` receive the patch
and a local commit. Commit diff must equal reviewed patch bytes. A durable atomic
journal (fsynced data and Windows write-through replacement) records binding,
commit and `push_intent` **before** the first external write. Push uses an exact
empty-ref lease: it cannot replace an existing remote branch. PR creation has a
separate durable `pr_intent` and always `draft=true`. No merge/deploy endpoint exists.

On retry the journal is checked against exact remote branch/commit and all matching
PRs, including closed PRs. An already matching open draft is adopted; conflicting
branches, multiple/closed/non-draft PRs or uncertain missing remote outcomes stop.
A recorded push/PR intent is never blindly retried. Stop and inspect remote state
with the operator; do not delete the journal or guess. No autonomous cancellation,
force overwrite, cleanup or duplicate PR creation is provided.

Main must still equal the approved target immediately before push and PR creation.
GitHub can advance main concurrently; the returned PR base is checked too. A race
can leave an approved branch or draft needing operator reconciliation; it does not
authorize rebasing, changing patch content, retrying a POST, merging or deployment.
The initial retained task patches remain unauthorized until individual approval.
Real end-to-end draft-PR acceptance is a separate owner-authorized gate.
