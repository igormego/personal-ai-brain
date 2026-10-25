# Credential inventory

Metadata authority for platform execution. Values, token fragments and hashes do
not belong here. Paths denote references, not a request to copy credentials.
Baseline verified 2026-10-10; inference metadata updated 2026-10-11; Core/control metadata verified 2026-10-23 Europe/Bratislava; unknowns must be resolved by the owner before
the corresponding new capability is enabled. File permission evidence is in
[the execution map](builder-execution-plane.md), not duplicated as token material.

| Purpose | Path/reference | Scope | Expiry | Owner |
|---|---|---|---|---|
| Administrative Windows → Homelab SSH | `C:\Users\megoi\.ssh\id_ed25519_homelab_codex`; host `/home/az1mutt/.ssh/authorized_keys` | Login as administrative az1mutt; no agent forwarding; never builder auth | No certificate expiry observed; revocation by key removal | Igor / az1mutt |
| Core source reads — `Homelab-AI-Brain-core-read` | `/home/az1mutt/.config/personal-ai-brain/github-token` → `/run/secrets/github_token` | Only `igormego/personal-project-brain`: Contents read, Metadata read, no other permissions (owner-confirmed UI); private Contents 200 and Issues 403 verified | 2027-10-23 00:00 Europe/Bratislava (GitHub response metadata) | Igor / az1mutt |
| Control Issues transport | `/home/az1mutt/.config/personal-ai-brain/github-control-token` → `/run/secrets/github_control_token` | `igormego/personal-project-brain`: intended Metadata read and Issues read/write; live Issues read and Contents denial verified; complete granted permission list not independently enumerated | 2027-09-24 00:00 Europe/Bratislava (GitHub response metadata) | Igor / az1mutt |
| Current interactive repository connector | Connected GitHub app, externally managed; no Homelab credential file established | Connector reports push/admin access to platform repo; broader grant not audited | Externally managed / unknown | Igor / connected app account |
| Parked Media client reference | `/home/az1mutt/.config/personal-ai-brain/trakt-client.json` | Media-owned; contents/scopes not inspected | Not inspected | Igor / Homelab-Media |
| Parked Media device-flow reference | `/home/az1mutt/.config/personal-ai-brain/trakt-device.json` | Media-owned historical device-flow artifact; validity not assumed | Not inspected | Igor / Homelab-Media |
| Parked Media token reference | `/home/az1mutt/.config/personal-ai-brain/trakt-token.json` | Media-owned; contents/scopes not inspected | Not inspected | Igor / Homelab-Media |
| Builder GitHub publication — `personal-ai-brain-builder` | `/home/az1mutt/.config/personal-ai-brain/builder/github-token` | Only `Az1mutt/personal-ai-brain`; Contents read/write, Pull requests read/write, automatic Metadata read; selected UI scope per owner setup, branch push verified | 2026-12-09 (owner supplied) | Igor / Az1mutt |
| Responses inference — owner setup pending, credential absent | `/etc/personal-ai-brain/inference/credentials/openai-api-key` (private directory verified; real file absent) | Dedicated Personal AI Brain OpenAI project; Responses inference with gpt-6.1-sol only, USD 15 enforced monthly project cap; no other platform credentials | Owner must record rotation/revocation date before enabling; no expiry assumed | Igor / local pab-inference UID 995; OpenAI service account pending |

Inventory scope is observed platform execution references, not a host-wide secret
discovery exercise. Media internals and unrelated trading credentials are excluded.
Add future entries here rather than creating separate builder token inventories.

## Core rotation verification — 2026-10-23

The owner installed the replacement through hidden interactive input and atomic
replacement at the existing Core path. File ownership remains az1mutt:az1mutt,
mode 0600, parent 0700. Core and control containers were recreated and their
read-only mounts matched the new file. All four fresh Core capabilities completed
with source findings: 12 states, 3 errors, 6 warnings and 2 proposals. These are
source validation findings, not credential failures.

The old `Homelab-AI-Brain` is no longer consumed by these services and is safe to
retire. The owner chose natural expiration at 2026-10-24 00:00 Europe/Bratislava;
revocation was not performed. That scheduled expiry is now past; the old token was not retrieved or directly probed. The Issues
and builder credentials were not changed. No secret value or fingerprint is
recorded here.

Post-expiry refresh on 2026-10-25: private Core Contents returned 200, Core Issues
returned 403, control Contents returned 403, both containers and transport were
healthy, and fresh core.health completed with the same source findings.
