# Codex bridge durable-core checkpoint evidence

Status: incomplete; remote transport disabled.

Checkpoint: 7d11bbcb26156f2760bbff2a8fad33197348b389.
Branch: codex/bridge-durable-core.

## Exact-tree Linux validation

2026-10-22: Git bundle of checkpoint transferred via existing ssh homelab.
Cloned into owned disposable /tmp/pab-bridge-test.mOq9PAIl/repo.
git rev-parse HEAD matched checkpoint exactly.
A disposable venv installed the declared .[dev] dependencies.
Full pytest: 251 passed in 1.70 seconds. No production checkout/service modified.

## Windows validation

16 focused ledger tests passed (latest run: 0.72 seconds).
Native full-suite collection is inappropriate: existing Linux modules use fcntl.

Real AppServer + execute + Ledger smoke task:
task ef22e806-af0a-4a97-a8d7-acf755d253b4.
Managed ChatGPT authentication; explicit MXC and bridge-workspace-only overrides.
Status succeeded. One actual turn/start call.
Identical duplicate returned existing truth; no second turn/start.
This was reply-only, not the required real coding acceptance.

Prior persisted-history forensics:
thread 01a1655c-0e5f-7d62-a84b-a7db969e31e7;
turn 01a1655c-13af-7f20-aa91-c9f6e88be47e;
runtime notLoaded, persisted turn interrupted.
No resume, turn/start or approval response during read-only history inspection.

## Remaining acceptance gaps

The client still targets the canonical cwd directly; task-specific disposable
worktree preparation/base verification is not implemented.
Explicit cancellation and complete crash/recovery integration matrix are pending.
RPC event dispatch currently drops unrelated notifications while waiting for a
response; approval during RPC becomes a generic exception instead of the
persisted approval_required path. No approval is accepted, but this needs repair.
Actual turn counts are verified only for reply success + identical duplicate;
ledger unit tests are not substitutes for live crash acceptance.
Outbound controlled TCP acceptance, static/secret scan, offline environment
checks, internal change/test/commit artifact, CI and security review remain pending.
Do not deploy or enable submissions from this checkpoint.
Project State is intentionally unchanged.

## Dispatcher repair follow-up

The notification-dropping defect listed above is superseded by this repair:
one stdout reader correlates response IDs to private wait queues and dispatches
notifications/server requests independently. Approval policy runs in the
dispatcher and persists approval_required even while an RPC waits.

Windows byte-range lock setup no longer reads the locked byte before acquiring
the lock. A real approval test exposed that race; the successful repaired test:
task fb938d9c-5ae0-4256-977f-474206029260;
thread 01a1656f-b33a-79c3-bf6e-d45fac188c93;
turn 01a1656f-b9bd-72a3-ac5d-7184ec9f51c9.
One actual turn/start, terminal bridge state approval_required,
failure_class owner_approval_required, no approval response sent.
Earlier diagnostic tasks each started once and ended uncertain; none was replayed.

Windows focused suite now has 27 passing tests, including response/event ordering,
concurrent RPC correlation, malformed notification, EOF, interruption notification,
and ledger approval persistence during RPC wait.
Disposable-worktree execution, complete live crash/cancellation matrix and real
coding acceptance remain unimplemented/unaccepted. No deployment authorization
is inferred from these focused checks.

## Acceptance stop: malformed response ID terminates dispatcher

2026-10-22, exact implementation HEAD
c6118deca37906f082a55d776c0ea7894b2b145f, deterministic in-memory
protocol probe using the existing test Pipe/client helpers.

While thread/read was pending, supplied:
1. malformed unrelated response with id=[] and a result object;
2. valid turn/completed interruption notification;
3. valid response matching the pending RPC.

Observed:
- pending RPC failed with RuntimeError: dispatcher_failed_TypeError;
- reader terminated;
- valid interruption notification was not delivered;
- two input messages remained unread.

Cause: AppServer._read indexes pending with message["id"] before validating
that the JSON-RPC response ID is a supported scalar. An array-valued ID is
unhashable. The outer exception handler terminates all dispatch instead of
isolating the malformed unrelated message.

This violates malformed-message isolation and the no-message-loss acceptance
invariant. Per the continuation's explicit STOP condition, no live worktree,
crash/cancellation or coding acceptance followed. No PR/push/merge/deployment
or Project State completion occurred.

Required repair: validate inbound message envelopes/ID types before correlation;
isolate malformed messages into bounded sanitized diagnostics; add a regression
covering malformed response followed by a valid interruption event and matching
RPC response. Reassess bounded-queue overflow handling in the same dispatcher
review before resuming the remaining acceptance.

## Malformed-message repair and live regression

Central classify() validates decoded envelopes before correlation/dispatch.
Responses require a positive exact Python int ID (bool/float/containers rejected),
exactly one result/error, and a structured error with integer code/string message.
Server requests accept exact int or nonempty string IDs; notifications omit IDs.
Malformed messages and handler failures generate sanitized diagnostics in a
64-entry ring, separate from valid-event delivery. No raw malformed payloads
are retained. Unknown/late/duplicate responses cannot overwrite a waiter.
EOF/transport failures wake pending RPCs; message-level faults do not.

Windows focused suite: 46 passed in 1.00 seconds.
New fault coverage includes 16 malformed envelope variants, duplicate/unknown
responses, 200-message malformed flood and handler exception containment.
All injected cases retain subsequent valid traffic and keep the reader alive.

Real MXC approval regression:
task 91a4abfe-287f-4ee8-93e2-2d0a01a6d3a6;
thread 01a16577-7bcb-70d2-874c-d91296833eba;
turn 01a16577-8211-7822-8570-b75e90d70d6c.
State approval_required; failure_class owner_approval_required;
one actual turn/start; zero approval responses; no protocol diagnostics.
Remote transport remains disabled. Full durable-core acceptance remains pending.

Exact repair commit e75ac359e4878cfc0c98f68d241bd1a412a04638 was reproduced by Git bundle in an isolated Homelab checkout: 281 Linux tests passed in 1.85 seconds; compileall and git diff --check passed. Owned Linux artifacts were removed. Windows focused total: 46 passed. No full durable-core completion is claimed.

## Disposable linked-worktree acceptance stop

Evidence checkpoint commit: 842736f (preserves the exact e75ac359 Linux results).
A new trusted workspace helper derives only bridge-owned UUID paths/branches,
refuses reuse and removes only clean owned worktrees. It is not integrated or
accepted for execution.

Controlled real worktree task:
890bc10c-2e0a-4479-88f1-122624e76695.
Base: 842736f.
Thread/start with cwd set to the disposable worktree succeeded.
One actual turn/start. The real turn reported:
workspace sentinel denied; canonical sentinel denied; turn completed.
Thus required workspace usability is NOT accepted. This is not evidence that
the real thread escaped into canonical files.

Separate standalone command/exec probes (not exposed by bridge client) reported:
disposable workspace read denied (UnauthorizedAccessException);
canonical sentinel read allowed.
These standalone results must not be conflated with thread-tool enforcement.
They demonstrate that merely changing cwd does not establish equivalent
permission-root semantics across these two app-server paths.

The helper/test did not change launch-process cwd or broaden permission roots.
Required next investigation: supported binding of effective runtime workspace
roots/profile to the trusted task worktree, including process startup context.

Per explicit Windows acceptance STOP condition, no crash/cancellation/coding
acceptance, PR, merge or deployment followed.
Both owned test worktrees/branches and all sentinel files were removed.
Canonical git status before/after matched byte-for-byte.
Remote transport disabled; Project State unchanged.

## Worktree root-binding resolution

The earlier missing-workspace-root interpretation is superseded.
Bounded commandExecution output showed the real blocker: selected bundled
pwsh.exe under .cache/codex-runtimes could not resolve/start under :minimal.
Those failed shell starts were not authoritative sentinel read-denial evidence.

A reversible launch PATH selected installed Windows PowerShell instead, without
adding filesystem permissions, profile roots, sandbox overrides or credentials.
With that environment BOTH tested variants passed through real thread tools:
A startup cwd canonical + thread cwd allocated worktree;
B startup cwd allocated worktree + thread cwd same worktree.
Thus startup cwd was not the determining workspace-root mechanism.
An earlier explicit-root trial with the inaccessible shell was inconclusive;
after runtime binding worked, no explicit profile-root fallback was needed.

Integration:
- AppServer startup cwd is explicitly the canonical launcher context.
- The bridge-only subprocess environment selects Windows PowerShell.
- Thread cwd comes exclusively from the trusted UUID worktree allocator.
- Approved base is selected from local HEAD; mismatching submitted base is rejected.
- Named bridge-workspace-only profile; no legacy sandbox/sandboxPolicy fields.
- turn/start contains only threadId and task text.
- Global interactive configuration is unchanged.
- No canonical/parent profile-defined workspace_roots were added.

Final real-thread workspace acceptance task:
58c9c9ab-0077-470b-84aa-3dc416986f74.
One new thread, one turn/start, completed without approval.
Observed commandExecution flags:
workspace read allowed; workspace write allowed;
canonical read denied; canonical write denied;
other repository denied; home sentinel denied;
relative traversal denied; junction sentinel denied;
network socket error 10013.
Canonical Git status before/after matched.
Owned worktrees/branches, junctions and sentinel files removed.

WORKTREE ACCEPTED for the tested runtime/profile/Windows shell combination.
Focused Windows tests: 53 passed in 1.17 seconds, including structured override
rejection, trusted allocated cwd/profile mapping, omitted turn-level overrides,
unapproved-base rejection and no second allocation on identical duplicate.
Full durable-core crash/cancellation/coding acceptance remains pending.

## Worktree candidate validation and subsequent recovery STOP

Candidate d47110171e5fe239c7506523f58dd23558a91258 reproduced by Git bundle:
288 Linux tests passed in 1.91 seconds; compileall passed.
Windows focused total 53. WORKTREE ACCEPTED remains the slice verdict.

Continued approved recovery acceptance:
A task 645b5b68-308d-4dda-bc22-61b5763426d6:
reserved before allocation -> uncertain, zero turn/start.
B task 54584551-73dd-4cf6-aab5-1d429ea248b0:
thread 01a1658b-ce78-7ee2-8733-90a0f1d61e1d, no turn;
reserved -> uncertain, zero turn/start.
Identical replay produced no allocation/turn; conflicting payload rejected in A/B.

C task fe10eb43-a4cd-4e5a-a42c-8fecbc8018c3:
thread 01a1658b-d9ad-7312-9e4e-a2be08b12636;
turn 01a1658b-e070-7352-b4a3-4d81597c5bc7;
one turn/start, bridge running before kill.
After process kill, runner stalled in the client shutdown path before a fresh
reconciliation server was launched. Owned Python runner tree was verified and
terminated. No unrelated process was terminated.
Fresh read-only history query returned sanitized RuntimeError/unavailable;
Ledger.recover classified uncertain. No replacement turn was started.
Underlying stream/process shutdown cause is not yet proven by a stack capture.

Preserved owned uncertain worktrees under C:\Projects\.pab-bridge-workspaces:
54584551-73dd-4cf6-aab5-1d429ea248b0;
fe10eb43-a4cd-4e5a-a42c-8fecbc8018c3.
Local bounded ledger/receipt evidence remains in .bridge-recovery-private.
These are intentional retention artifacts, not active remote execution.
Remaining matrix, cancellation and coding acceptance were not run after this
Windows live-acceptance failure. No PR/push/merge/deployment/Project State closure.
Next repair: bound shutdown/pipe handling and owned child-process lifetime;
re-test history reconciliation after abrupt termination without replay.

## Bounded shutdown repair and live re-acceptance

Controlled reproduction established the old stall at buffered stdout.close()
(client line 198): parent was dead, reader blocked, closer blocked; both released
only after the inherited-handle descendant exited (4.09 seconds).
No raw model output or credentials were used in this reproduction.

Windows ownership now uses a private Job Object with KILL_ON_JOB_CLOSE.
App-server is created suspended, assigned before its first instruction, and only
its own suspended thread is resumed. Assignment failure kills that owned child.
No process-name discovery or arbitrary PID cleanup interface is exposed.
Linux process tests use a private session/process group.

Transport has one reader and one bounded-queue writer, both explicitly joined.
Sends have a finite queue/ack budget. Shutdown rejects new writes, fails waiters,
waits at most 2 seconds after terminate, closes the owned Job Object (including
descendants), waits at most 2 seconds for root exit and joins each worker for at
most 2 seconds. Buffered streams close only after workers exit.
Process cleanup never starts/resumes a turn.

Live killed-server re-acceptance:
idle task 383a317a-b34f-48f0-86fb-e47aab097ed1: 0 turns, 0.078s shutdown;
active task 7d69b053-afc0-42fb-9248-8283c2672763: 1 turn, 0.015s,
history unavailable -> uncertain;
approval task 1dfe0e88-b5ed-47b8-85e2-496deaae3e67: 1 turn, 0.078s,
approval_required -> persisted interrupted -> interrupted.
No approval sent, no reader/writer survived these shutdowns.

Completion-before-ledger-write:
task a1d6f5f4-6df7-47c5-bf13-824d6a498c81,
thread 01a16593-101e-7d43-ba13-641ab46a59d5,
turn 01a16593-1617-7ef2-87aa-d6c651e1b842:
one turn; running ledger recovered persisted completed -> succeeded;
identical duplicate after restart caused no turn; conflict rejected.

Cancellation:
An initial immediate interrupt was rejected before command readiness; that task
was not retried or replayed and its artifacts are retained.
Fresh task 2f8c2040-b7de-48e5-8e4b-6f58f2a1fe7f,
thread 01a16593-b646-7e72-80ec-703cabd46c99,
turn 01a16593-bcba-7002-b6cb-1f3e7f7b1c9d:
observed commandExecution start, sent exactly one turn/interrupt;
observed and persisted interrupted, ledger recovered interrupted;
one turn/start; duplicate no relaunch; conflict rejected;
shutdown 0.094s with no surviving reader/writer.

Focused Windows suite: 60 passed. Includes owned-descendant inherited-pipe test,
unrelated-process survival, shutdown states/idempotency, waiter release, ownership
reuse rejection and ledger-only cancellation targeting.
Full final validation and real coding acceptance remain pending.

## Internal coding acceptance STOP after lifecycle repair

Lifecycle checkpoint: 3a1cd6edfb0f030aaa9c9ea4e2bdc5479fe286e8.
Additional uncommitted validation guard:
executed tasks require trusted completion validation before succeeded;
completed turn history without a durable required-validation result reconciles
uncertain. Result metadata shape is bounded/allowlisted.
Focused Windows tests including guard/result cases: 67 passed in 1.95 seconds.
This candidate has not had final Linux validation.

Real coding task 39aa076f-7f11-4df7-879e-96e62f615274:
thread 01a16597-fb38-7661-95dc-11dc3d5bed57;
turn 01a16598-0109-7130-9885-a2c1a7a66d62;
base 3a1cd6edfb0f030aaa9c9ea4e2bdc5479fe286e8.
One turn/start, result uncertain, duplicate same stored result,
conflicting payload rejected. No generated commit, no source files changed.
The private runtime was staged only inside this owned worktree.

Read-only persisted item inspection:
three commandExecution items, each exit 1, zero passed-test summaries.
All command cwd fields matched the allocated task worktree.
Get-Content repository-relative reads reported missing path.
A subsequent Set-Location reported access denied.
Thus correct command cwd metadata and absolute sentinel access do not yet prove
normal PowerShell repository-relative coding usability.
Exact provider-location/runtime cause remains unresolved; no root-read grant,
profile broadening or task retry was attempted.

The canonical-comparison receipt is invalidated: the supervising agent added
authorized focused tests in canonical while acceptance was active. Its false
status/diff comparison must NOT be treated as evidence of a task escape.
No generated task edits were found in either approved source/test file.

Per Windows live-acceptance STOP condition, no remaining matrix or further coding
turn followed. Failed/uncertain worktrees and private ledger receipts retained.
No PR/push/merge/local installation/Project State closure.
Remote transport remains disabled.

Next investigation: reconcile shell provider location and MXC directory access
inside the correctly mapped worktree, using controlled relative reads and
Set-Location tests. Do not conflate that with the fixed inherited-pipe shutdown.

## 2026-10-23 shell/provider diagnosis STOP

Preservation checkpoint:
59f6fcd1e20dff90d60bccacf96c298b1d651c0a.
No permission broadening or normal interactive configuration changes.

Comparison:
both prior successful sentinel turn and failed coding turn used installed Windows
PowerShell, named bridge-workspace-only, explicit MXC, on-request approvals,
trusted thread cwd and no turn-level cwd/sandbox overrides.
Successful simple probes used absolute .NET File APIs; failed coding used
PowerShell provider-relative operations. That distinction matters.

Fresh real-thread matrix:
baseline runtime roots, no-login control, exact task profile root.
No-login trials produced no usable flag evidence and are inconclusive.
Baseline/exact-root .NET file operations passed; provider-relative operations
failed. Initial path-comparison labels used doubled slashes and are superseded.

Authoritative corrected exact-root diagnostic:
workspace 1048cf80-8466-4c19-abaf-fbec9fee4a5d;
thread 01a167ec-5641-7b61-b6ec-3be1a85921df.
One turn in this fresh diagnostic thread; no approval.
- process cwd: exact task worktree;
- PowerShell provider cwd: other;
- .NET absolute and relative sentinel reads: allowed;
- absolute provider Get-Content: allowed;
- .NET absolute directory enumeration: allowed;
- exact root GetAttributes: allowed;
- absolute write: allowed;
- provider Get-Item ., relative Get-ChildItem/Get-Content/Set-Content: denied;
- nested provider-relative read / Resolve-Path: denied;
- Set-Location exact worktree / . / controlled child: denied.

The same exact trusted task path was added reversibly to profile workspace_roots
for the final variant; it did not repair provider access. No canonical/parent
root was added, no :root read, no legacy sandbox mixing.
All diagnostic worktrees/branches and controlled files were removed.
These were explicitly authorized fresh diagnostic turns, not retries of a
durable bridge coding task.

Read-only persisted command metadata:
installed Windows PowerShell=True, NoProfile=True, NonInteractive=False;
no Set-Location before supplied probe body.
The earlier whole-command wrapper_location flag included the probe's own
Set-Location tests and is NOT evidence of an internal wrapper prefix.
Profile loading and a pre-probe cd wrapper are not demonstrated causes.

The official documented CODEX_HOME/.sandbox/sandbox.log was absent.
.sandbox-secrets was not accessed. No raw events/auth/log bodies retained.
No independent Python was installed or provisioned for these diagnostic turns.

Conclusion:
process cwd propagation and direct file access are functioning.
Failure is isolated to Windows PowerShell provider location/path resolution
under this restrictive MXC setup; the exact underlying provider/access check
is not established. Do not claim an ancestor grant is necessary or safe.
Normal coding usability remains NOT ACCEPTED despite absolute sentinel success.

Per explicit relative-path STOP condition, no coding retry or final milestone
closure followed. Windows baseline remains 67; no implementation changed in this
slice, so full suites were not repeated. Final Linux lifecycle validation remains
pending. No PR/push/merge/deployment/Project State completion.
Next supported investigation: provider-path compatibility under MXC with a
controlled root and narrowly scoped sandbox/runtime diagnostic evidence,
without granting root/parent/canonical access.

## 2026-10-23 decisive native execution-path control

Classification D: GENERAL MXC WORKSPACE FAILURE on the tested runtime/profile.
This is below the bridge app-server client: the supported codex sandbox command
reproduced the provider failure without app-server or model execution.

Versions:
- OpenAI.Codex Appx package 26.1016.6573.0.
- CLI/app-server 0.163.0-alpha.11.
- Windows 11 25H2 build 26200.9457.
- Installed Windows PowerShell 5.1.26100.9444, System32 WindowsPowerShell v1.0.
- Explicit windows.sandbox=mxc; bridge-workspace-only; task network false.
- App updater separately reported installedVersion 26.1016.51333, build 21161,
  prod channel, restart_required. This is not an up-to-date confirmation.
- Alternate CLI bin directory had no executable. No relevant supported fix/version
  was established from available release information. No update/restart performed.
  Verdict is scoped to the tested runtime, not every future native release.

One fresh trusted worktree control aa9a187e-5e5c-4ec4-94ff-dd1dcaff403e:
Host same Windows PowerShell -NoProfile -NonInteractive, cwd worktree:
provider/process cwd correct, relative listing/read/write, Set-Location . and
controlled child all succeeded.
Official Codex sandbox invocation used --include-managed-config,
--permission-profile bridge-workspace-only, --cd exact worktree, and explicit
MXC plus the existing profile overrides. No legacy sandbox policy fields.

Official sandbox + PowerShell:
process cwd correct; provider cwd elsewhere; relative listing/read/write and
Set-Location denied; absolute .NET read/write allowed.
Canonical/other/home controlled sentinel reads denied.

Separate official sandbox cmd checks, with individual exit codes:
cwd: exit 0, matches exact task root;
dir /b control.txt: exit 1, access denied;
type control.txt: exit 0, controlled content read;
relative echo write: exit 0, controlled file created.
Thus classification C alone would overstate cmd normal-operation support.
Directory enumeration failure also affects cmd; classification D is required.
No prompt-only absolute-path workaround or command/exec substitution is accepted.

WSL read-only inventory: Windows Subsystem for Linux is not installed.
No distro, bubblewrap, Linux Codex or managed-auth feasibility is established.
No WSL install/feature enablement, credentials, mount changes or migration.
A WSL implementation was not started because phase 5/D explicitly requires STOP.

All owned diagnostic worktrees/branches and external sentinels were removed.
Bounded sanitized receipts retained under .native-controls-private.
No auth or sandbox-secrets access. Zero turn/start in this slice (host/official
sandbox controls only). No implementation change; baseline tests not repeated.
Windows historical total 67; Linux historical accepted worktree total 288;
final lifecycle Linux regression remains pending.
No PR/push/merge/installation/Project State completion. Remote transport disabled.

Recommended next step:
resolve/confirm the supported native runtime update state, then repeat only this
small official-sandbox control. If enumeration remains denied, address the native
MXC/profile runtime compatibility with supported diagnostics; do not modify
bridge launch logic or grant parent/root/home access to hide the failure.
If separately proceeding to WSL, first provision/verify WSL2 through supported
mechanisms; prefer bridge-owned Linux-filesystem task checkout and keep trusted
app-server authentication outside all sandboxed command mounts. Feasibility and
credential isolation must be proven before adopting that lane.

## 2026-10-23 supported-restart handoff

Requested slice: resolve restart_required before rerunning ONLY the narrow official
sandbox controls. No full bridge acceptance or WSL installation until the native
post-restart verdict is available.
Pre-restart: Appx 26.1016.6573.0; CLI/app-server 0.163.0-alpha.11;
updater installedVersion 26.1016.51333/build 21161/prod/restart_required.
Windows 26200.9457; Windows PowerShell 5.1.26100.9444.
Only desktop package processes are authorized restart targets. Standalone codex
process ownership was not established and they must not be killed by name.
Persisted private ledgers and retained uncertain worktrees remain unchanged.

After restart:
1. query updater and Desktop/CLI/app-server versions; establish actual change;
2. official MXC compatibility echo with managed :workspace, startup only;
3. recreate one owned worktree and repeat PowerShell/cmd relative operation controls
   and controlled canonical/other/home denials, using unchanged restrictive profile;
4. if fixed, smallest real thread probe; if still broken, stop native investigation
   and perform authorized supported WSL2 feasibility/setup, preserving all isolation.
No post-restart result is claimed by this handoff.

## 2026-10-23 post-restart native verdict and WSL setup

Desktop restart verified by process replacement: old PID 28748 absent; new package
main PID 31668. Helper receipt stopped before completion, so it is not proof of
successful updater activation. Package path remains OpenAI.Codex_26.1016.6573.0.
Updater: installedVersion 26.1016.51333, build 21161, prod, restart_required.
Packaged CLI (also app-server executable) --version: 0.163.0-alpha.11, unchanged.
No real runtime update confirmed; update pending state remains unresolved.

Fresh disposable Git repository postrestart-c67846004b7742108cda611fefdcb8a8:
- Official documented explicit-MXC compatibility echo: MXC_OK, exit 0.
- Unchanged restrictive bridge profile, managed config, network disabled, exact cwd.
- Installed Windows PowerShell -NoProfile -NonInteractive: process cwd correct;
  provider cwd C:\; listing/read/write/Set-Location fail; exit 1.
- cmd: cd correct exit 0; dir access denied exit 1; relative type and write exit 0.
- Harmless canonical/other-repo/home sentinels all denied (exit 23); removed.
- No app-server/turn execution and no bridge implementation changes.
Bounded sanitized receipt: .native-controls-private/postrestart-c67846004b7742108cda611fefdcb8a8.json.
Native verdict: STILL BROKEN on unchanged installed runtime after verified Desktop
restart. Stop native investigation per current user instruction; proceed WSL2.

WSL setup: status confirms not installed. Both wsl --install --distribution Debian
--no-launch and bare wsl --install returned exit 1 with not-installed message.
Runner token is not administrator (require_escalated bypasses workspace sandbox,
but does not itself grant Windows administrator rights). Normal UAC RunAs launch
of supported Debian installer requested; completion not yet established.
No Linux distro/bwrap/Codex/auth/isolation/process-lifecycle feasibility claimed.
No credentials copied/read, no API key, no permission broadening, no bridge-client
workaround, no full acceptance, PR, merge, or Project State completion.
References: https://learn.chatgpt.com/docs/windows/windows-sandbox;
https://learn.chatgpt.com/docs/app/windows;
https://learn.microsoft.com/en-us/windows/wsl/basic-commands.

WSL install delta: UAC-launched installer PID 30316 subsequently exited. WSL now
reports version 3.0.3.0, kernel 6.18.54.1-1, default version 2. No distribution is
registered. WSL2 startup reports virtualization unavailable and requests Virtual
Machine Platform plus firmware virtualization. This does not establish which
prerequisite is missing; an administrator read-only feature/firmware query was
launched through normal UAC to distinguish them. No automatic reboot requested.
Distro/bwrap/Linux Codex/auth/sandbox/bridge process feasibility remains pending.

## 2026-10-23 approved administrator readiness result

Administrator receipt now collected: VirtualMachinePlatform Enabled;
HypervisorPresent true; CPU CIM reports VirtualizationFirmwareEnabled false and
SecondLevelAddressTranslationExtensions false. These mixed observations do not
establish a firmware defect while feature activation/reboot is pending.
Host WSL status remains default version 2, virtualization unavailable, no distro.
Both Component Based Servicing RebootPending and Windows Update RebootRequired
registry indicators are present. Last boot: 2026-09-27 17:51:25 (host local time).
Next required action: user-controlled Windows restart, then recheck WSL2 readiness
and install/start Debian through supported WSL commands. Do not repeat native MXC
controls. If virtualization remains unavailable after reboot, investigate firmware
or nested virtualization prerequisites before Linux acceptance. No automatic
reboot, credential access, bridge changes, full acceptance or milestone closure.
Microsoft guidance: https://learn.microsoft.com/en-us/windows/wsl/troubleshooting
requires restarting after enabling Virtual Machine Platform.

## 2026-10-23 post-reboot WSL2 feasibility

Windows reboot verified (last boot 2026-10-23 12:25:21 host local). WSL no longer
reports virtualization unavailable. Supported Debian install completed exit 0;
wsl --list --verbose registers Debian version 2. Distro starts: Debian 13.5 trixie,
WSL2 kernel 6.18.54.1-microsoft-standard-WSL2. No firmware changes needed.
Standard Debian packages installed: bubblewrap 0.12.0, Git, Python3/venv,
Node.js/npm, curl and CA certificates (including package-manager dependencies).
Created unprivileged pabbridge account, UID 1000, without administrator membership.
Normal stable npm @openai/codex@latest installed CLI/app-server 0.164.0.
No nightly/development/unofficial package. Windows native runtime unchanged.

Unprivileged bwrap --unshare-all minimal-system startup: BWRAP_OK.
Official codex sandbox on Linux task Git repository
/home/pabbridge/tasks/4097983cedf5418ca37ccc59e34308b9 used unchanged bridge
root-deny, minimal-read, workspace-write, tmp-deny and network-disabled semantics.
Exit 0: cwd exact, relative list/read/write and child traversal succeeded.
Trusted sibling sentinel, Windows home, Windows Codex home and canonical Windows
repository paths invisible (FileNotFoundError); socket connection PermissionError.
Only harmless Linux sentinel content was created; no real private file read.
Linux bounded receipt in /home/pabbridge/trusted/control-4097983cedf5418ca37ccc59e34308b9.json.

Windows ProcessOwner Job Object -> WSL Python parent+sleep child smoke test:
close 0.016 seconds; both owned Linux PIDs absent afterward. This is one graceful
ownership smoke test, not full bridge-crash/lifecycle acceptance.
Windows-owned Linux app-server stdio initialize succeeded, userAgent 0.164.0
Debian; close 0.016 seconds. Zero model turns; remote transport remains disabled.

Dedicated Linux codex login status: not logged in. Started official ChatGPT device
authentication, awaiting user browser completion. No Windows auth copied/extracted,
no API key, no credentials printed/stored in task folders. One-time device code
is intentionally omitted here. Authentication/inference, backend-dependent crash
acceptance, real coding task and result mapping remain unverified. Current bridge
implementation is not yet wired to WSL; feasibility must not be reported as full
milestone acceptance. No PR/merge/Project State closure.
Official install/auth references: https://learn.chatgpt.com/docs/cli and
https://learn.chatgpt.com/docs/auth.
