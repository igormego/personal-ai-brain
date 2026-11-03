# Codex environments

Use one capability profile per security/dependency boundary, not per project.
Repository-specific rules belong in that repository's AGENTS.md.

| Access plane | Durable state | Boundary |
| --- | --- | --- |
| Codex Desktop/local | User .codex config, global AGENTS, app-managed authentication | Local process and sandbox permissions; installed plugins do not prove authentication |
| Task workspace | Documents/Codex dated scratch and outputs | Temporary execution context; generated files are not automatically authoritative |
| Canonical checkout | Local checkout of GitHub history; C:\Projects is the local Windows convention only | Preserve uncommitted work; committed and pushed GitHub history is durable implementation truth |
| Published/cloud environment | Repository commits plus explicitly configured platform environment | Local config, disk, SSH identities and connector sessions are not inherited |
| ChatGPT connector/plugin | Service-managed connection and repository/account grants | Independent of Git CLI authentication and local SSH |

## Profiles and credentials

- **local-homelab-admin:** Desktop/local project work, GitHub via whichever access
  plane is verified, administrative SSH through the canonical homelab alias.
- **cloud-github-dev:** Repository development only. Administrative homelab SSH is
  forbidden; never upload its private key or assume local machine credentials.

The manifest contains capability descriptions and external credential references,
never credential values, destinations or machine addresses. It uses JSON-compatible
YAML 1.2 so PowerShell can parse it without installing a YAML module.
Profile restrictions describe policy; these scripts do not provision or enforce a
cloud sandbox. Platform credential isolation still requires operator verification.

Canonical SSH configuration/identities stay in the user's .ssh directory.
Keep other credentials in their approved external/platform stores; leave Codex
app-managed auth under app control. Never copy private keys or tokens to task
folders, repositories, manifests, reports or cloud development environments.
Documents/Codex is scratch/output, not a credential store or canonical checkout.
Scratch may still contain valuable work: location alone never authorizes deletion.

## Verified Cloud environment

The existing **pab-homelab-dev** environment implements the **cloud-github-dev**
profile on **Codex Cloud**. Acceptance on **2026-10-12** is
**VERIFIED_WITH_WARNING**, based on the read-only Cloud validation below. This is
an environment acceptance record, distinct from the verification script's
`PASS_WITH_WARNINGS` outcome; it is not a live Homelab acceptance claim.

| Capability | Verified result |
| --- | --- |
| Repository scope | `Az1mutt/personal-ai-brain` and `Az1mutt/homelab-infrastructure`, both available in the isolated Cloud workspace |
| Repository policy | Both repository `AGENTS.md` files load correctly |
| Git | Local Git operations work |
| Python tooling | Python, pytest and PyYAML available |
| Node tooling | Node available |
| PowerShell | Installed under `/workspace/.cloud-setup/powershell`; `pwsh` is not on the default PATH |
| SSH observations | No user SSH directory, SSH agent socket or explicit `homelab` configuration found |
| Credential boundary | No Homelab administrative credential intentionally present; no local Windows paths or credentials assumed |

**Warnings:** Absolute SSH capability absence could not be certified because a
configuration-only probe encountered proxy behavior. No live Homelab connection
was attempted. PowerShell also requires explicit PATH initialization. Observed
absence is not a proof of enforced isolation; the administrative SSH prohibition
remains policy regardless of probe behavior.

### PowerShell startup configuration

The saved Cloud `start_skill` already supplies the following shell initialization.
It makes `pwsh` discoverable in that shell and gives PowerShell writable state
locations; it does not change the default PATH of unrelated task shells:

```sh
export PATH="/workspace/personal-ai-brain/.venv/bin:/workspace/.cloud-setup/powershell:$PATH"
export XDG_CACHE_HOME=/workspace/.cloud-setup/cache
export XDG_CONFIG_HOME=/workspace/.cloud-setup/config
export XDG_DATA_HOME=/workspace/.cloud-setup/data
```

Recommended environment change: retain this initialization in the versioned
`pab-homelab-dev` Cloud startup configuration and execute it in each task shell
before invoking `pwsh`. If the platform supports a persistent startup-PATH setting,
prepend `/workspace/.cloud-setup/powershell` while preserving the platform-provided
PATH, then publish and verify `command -v pwsh` in a fresh task. The exposed draft
configuration does not offer a dedicated additive default-PATH setting; an install
script's `export` alone does not persist into future task shells. Do not substitute
an invented PATH value, edit system shell profiles, or copy binaries into runtime
paths as a workaround. No credentials or additional network access are needed.
Default-PATH acceptance remains a warning until independently verified.

## Surface selection and Codex task lifecycle

1. Codex Cloud is the default for repository-only implementation, tests and docs
   in PAB/Homelab when live infrastructure is not required.
2. Codex Local is required for work explicitly needing the live Homelab, using
   the canonical `ssh homelab` capability and the repository's authorization rules.
3. Never copy the local Homelab administrative SSH credential into Cloud.
4. A new concrete feature, fix or milestone normally starts a new Codex task.
5. Continue an existing task only for the same bounded piece of work when its
   uncommitted task workspace is intentionally needed.
6. Codex task workspaces and task-local `work` branches are disposable execution
   state, not the source of truth. Preserve needed uncommitted work before disposal.
7. Durable implementation truth is committed and pushed GitHub history.
8. Durable project status and the next action belong in Project State.
9. Live runtime truth requires explicit verification in the appropriate runtime
   environment; a merged PR or passing Cloud tests does not establish deployment.
10. Reuse `pab-homelab-dev`; create additional Cloud environments only when
    repository scope, dependencies, network/service access or security boundaries
    materially differ.

Cloud tasks already have isolated workspaces. Use their existing checkouts; do
not create an additional Git worktree unless explicitly requested. The Windows
paths in the local setup section below apply only to Codex Local.

## Canonical Local execution lane

**local-homelab-admin** is the canonical logical profile for Codex Desktop
**Local / Worktree on Igor's Windows PC**. One logical profile covers both
canonical repositories:

- `C:\Projects\personal-ai-brain`
- `C:\Projects\homelab-infrastructure`

Use this lane when a task needs or is likely to need live Homelab inspection,
Docker/service/log/filesystem inspection on the Homelab, deployment or runtime
acceptance. For a bounded implementation followed by deployment/acceptance,
prefer Local from the start. Use **pab-homelab-dev** Cloud when work is strictly
repository-only and requires no live Homelab access.

Repository `AGENTS.md` remains the project-specific authority; the existing
`~/.codex/AGENTS.md` supplies global Local instructions. Do not duplicate the global
file into either repository. The Windows checkout paths are Local conventions,
not Cloud dependencies.

### Existing SSH route and credential ownership

Live Homelab administration requires explicit task authorization and the existing
canonical **`ssh homelab`** route. Inherit machine SSH resolution from
`~/.ssh/config`; the existing administrative identity remains in the user's
machine-local `.ssh` store. Its purpose is Homelab administration, and the manifest
stores only that external reference. Never copy it into a task workspace,
repository or Cloud environment, and never track its actual path, Homelab IP or
username in project configuration.

### Windows Local acceptance — 2026-10-13

**VERIFIED_WITH_WARNINGS** for the Local profile. The exact authorized probe
`ssh -o BatchMode=yes -o ConnectTimeout=10 homelab true` exited **0** under
Igor's Windows account. Alias resolution selects one existing machine-local
identity with `IdentitiesOnly=yes`, `ControlMaster=false` and GSSAPI disabled.
Together with BatchMode success, this attributes access to the configured identity
(including possible SSH-agent signing for that identity); no raw authentication
log or private key was read. The identity path was reported locally, not added to
this profile configuration.

The known historical task-folder identity candidate and its public file still
exist. Their identity equivalence and old consumers remain unresolved; neither is
selected by the canonical alias. This is a preserved pre-existing exception to
the no-task-local-credentials policy, not a claim that all old copies are absent.
No credentials were copied, created, rotated or deleted. Cloud isolation evidence
remains the dated Cloud record above; it was not re-audited from Windows.

| Local check | Result |
| --- | --- |
| Installed Desktop | `OpenAI.Codex` package `26.1007.2314.0`; running bundled Codex runtime, sandbox runner `0.162.0-alpha.17.2` |
| Local execution | PASS: this task runs in the canonical Windows checkout |
| Worktree support | YES: installed app exposes managed worktree operations and setup loader; fresh-worktree execution NOT_RUN |
| First-class Local environment configuration | YES: installed `app.asar` defines the version/name/setup/actions schema and discovers `.codex/environments/*.toml` |
| Project-root configuration | `.codex/environments/environment.toml` added in Personal AI Brain using that installed schema; empty setup and explicit Windows-only Verify Homelab action |
| Configuration recognition | Installed loader/schema inspected and file validated; live settings-pane/action rendering NOT_RUN |
| Personal AI Brain checkout | Available, initially clean on main; switched to existing `docs/local-homelab-profile`; repository AGENTS readable |
| Homelab infrastructure checkout | Available, clean on main; repository AGENTS readable; left unchanged, with no `.codex` configuration |
| Global Local policy | Existing `~/.codex/AGENTS.md` read and retained as global authority |
| Machine configuration | Existing `~/.codex/config.toml`; Windows elevated sandbox configured; Personal AI Brain trusted |
| Effective task boundary | Workspace-write, restricted network, auto-review escalation; sandbox uses a separate Windows account |
| Organization / Agent Security | UNKNOWN: organization policy source/settings not exposed by the available inspection surface; observed task restrictions do not prove their organizational provenance |

Local environments are project setup/action files, not Cloud-style published
credential-bearing environments. The logical **local-homelab-admin** profile still
spans both canonical repositories and relies on machine-local SSH and policy.
The installed loader and schema corroborate the supported mechanism described in
[OpenAI Local environments documentation](https://learn.chatgpt.com/docs/environments/local-environment).
The manual action is optional convenience; its exact command passed independently.
No automatic SSH startup was added. A separate Worktree/UI acceptance is not
implied by this Local acceptance. No Personal AI Brain runtime or service changed.

The default sandbox account does not inherit Igor's SSH route. The live probe used
the authorized user-context escalation; a sandbox-only alias failure must not be
misreported as failure of the machine's canonical SSH configuration. Future live
tasks still require explicit authorization and applicable approval boundaries.

## Repeatable local setup

1. Inspect C:\Projects destination collisions. Stop a component if meaningful
   content exists; never overwrite a checkout to install this layout.
2. Establish canonical Git repositories using existing authoritative history.
   Preserve refs before updating from remotes. Keep original checkouts until
   preservation is independently verified.
3. Preserve dirty files byte-for-byte, including untracked files. Recreate linked
   worktrees through Git; do not copy .git pointer files into new locations.
4. Review config/codex/AGENTS.global.example.md and install it as the user's
   .codex/AGENTS.md only if absent, or reconcile existing instructions explicitly.
5. Run the commands below in PowerShell 7+. Audit and verify do not install
   software, modify configuration, fetch Git history, or read credential values.

```powershell
./scripts/codex/audit-environment.ps1
./scripts/codex/audit-environment.ps1 -ProbeHomelab
./scripts/codex/verify-environment.ps1 local-homelab-admin
./scripts/codex/verify-environment.ps1 cloud-github-dev
```

Audit probes SSH only with -ProbeHomelab. Local profile verification probes by
default; -SkipHomelabProbe yields INCOMPLETE because connectivity is required.
The SSH probe uses BatchMode, requires an existing trusted host key, suppresses
forwarding, connection sharing, proxy/local commands and host-key writes.
It does not change saved SSH behavior. Include/Match configurations are reported
UNKNOWN rather than evaluated. The probe deliberately tests a direct alias path.

GitHub CLI status is suppressed and reduced to a status; absent CLI authentication
does not disprove connector access. No credential helper is invoked by Git status.
Executable metadata is used for Codex version because CLI startup may write state.
No version or surface is guessed when metadata is unavailable.

PASS means that specific check succeeded; WARN needs review; MISSING means an
expected item was absent; UNKNOWN means unverified/inaccessible. A required check
that does not pass makes verification INCOMPLETE (exit 1). Optional warnings produce
PASS_WITH_WARNINGS (exit 0). -AsObject returns structured evidence without exiting.
Filename candidates include scripts and documentation; they are not proof of
credentials or duplicates. Inaccessible paths and skipped links remain warnings.
Neither script decides deletion safety.

## Preservation and review

Use Change / Review Lane for this policy and executable tooling: branch, tests,
secret scan, diff check and PR. Keep distinct media work and ledger README changes
until reconciled. Preserve runner worktree relationships and feature commit refs.
The old SSH candidate and old task roots require separate review; this setup does
not authorize their deletion, credential rotation or authentication ACL changes.

Run offline script tests with:

```powershell
./scripts/codex/test-environment.ps1 -FixtureRoot <new-empty-scratch-path>
```

Tests create only synthetic fixtures, mock external commands, and retain fixtures
at the specified path for review. They never run a live SSH or GitHub probe.


## Laptop-off / travel development lane (2026-11-03)

**Purpose:** work on repository tasks from ChatGPT web/mobile or Codex Cloud
without the Windows notebook, Windows Remote Codex v0.2 worker, or local SSH.
This is a routing convention over existing tools, not another agent, task queue
or grant of runtime permissions. The November 6–11 travel scenario is the
initial consumer; the procedure is reusable.

| Need | Existing route while Windows is OFF | Evidence / limitation |
| --- | --- | --- |
| Discuss, inspect, plan or make a small bounded repository change | Ordinary ChatGPT + its already connected write-enabled GitHub tool surface; read current repository `AGENTS.md`, edit only an approved branch, open PR, check GitHub CI | This path is **independent of the Windows bridge**; GitHub CI, not the chat, executes repository tests. Never assert tests were run locally. |
| Substantial repository coding, runnable tests, iterative implementation | **Codex Cloud** with the existing published `pab-homelab-dev` / `cloud-github-dev` environment, if still available to the account; select the appropriate repository and keep the same task for follow-ups | Prior cloud environment check was **VERIFIED_WITH_WARNING on 2026-10-12**, not a new November acceptance. Only the Codex Cloud surface can launch its tasks; this ordinary ChatGPT plugin cannot claim to launch a cloud task. |
| Project OS status or authorized read-only Homelab inspection | Existing authenticated ChatGPT plugin + private GitHub control ledger on the always-on Homelab, subject to real backend uptime and freshness | Do not confuse the read-only Homelab control with unrestricted administration. |
| Live SSH, host changes, deployment, secrets, local-only files | **Not enabled by this travel lane.** Defer or use a separately approved existing authenticated operator path | Neither GitHub merge nor cloud CI implies live Homelab acceptance; no copied admin SSH keys, broad sudo or exposed Docker socket. |

**Before relying on Codex Cloud from mobile:** on web/desktop ensure the
existing environment is still **published**, visible to the intended account,
and includes the currently accessible repositories. On the phone, select
**Codex → published environment**; start one bounded repository task and
confirm that it can inspect files and run its expected tests without the
notebook. If that acceptance fails, use the working ChatGPT→GitHub branch/PR
route for smaller tasks; do not create a second unreviewed agent/runtime.

**ChatGPT-native GitHub task flow:** identify the correct repository and
read its live `AGENTS.md`; check source and current state; create a narrow
branch; implement a scoped change with GitHub file operations; open a reviewable
PR; read the actual diff and required CI checks; only merge when project
instructions, tests and task authorization permit it. The user should receive
the **real PR/task URL and completed vs blocked truth**, not hand-copy
prompts, commits, log excerpts or task IDs. For a task too large to edit and
validate reliably through the GitHub tool surface, choose Codex Cloud rather
than generating dozens of API mutations or pretending a remote Codex session
was launched.

**Codex Cloud task flow:** use a currently published environment on the
web/mobile, continue the same task for the same feature, review its diff and
test results, and commit/publish important work to GitHub. A new task is a
new isolated workspace; saved Cloud VM state is normally recoverable for up
to seven days after a task resume and is not durable project state. GitHub
commits/PRs remain authoritative; update the specialist Project State on a
verified meaningful milestone. Laptop-off work must not depend on locally
retained, unpublished Windows WSL patches (including accepted coding Issue
#106) until an authorized publisher makes those changes durable in GitHub.

**Explicit selection rule:** when Igor says the notebook is off, unavailable,
or he is travelling, **do not issue `pab-codex/v0.2` requests expecting a
Windows worker to run**. Prefer GitHub-native work here, or a real Codex Cloud
task initiated on its own surface. Existing queued tasks keep their original
durable identity and are never silently re-submitted to a different executor.

Source: [Codex Cloud help](https://help.openai.com/en/articles/20001545-using-codex-cloud)
and [cloud environment guide](https://learn.chatgpt.com/docs/environments/cloud-environments),
checked 2026-11-03. UI options and plan entitlements may change; do not
infer the current user's actual Cloud environment availability from the
historical October test alone.
