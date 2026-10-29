# Brain v0.3 — one real LLM decision (manual acceptance)

**Source status:** provider implementation and offline contract tests only. A merged PR and green CI do **not** prove a paid LLM call succeeded. The first live API call must be separately performed and recorded in Issue #57.

## Why this is the next gate

Decision #52, Grounding #54 and offline roundtrip PR #56 verify policy and the control path with *fake model decisions*. This experiment checks whether an actual model interprets Slovak requests correctly. It deliberately does **not** access GitHub, execute capabilities, deliver a ChatGPT message, deploy a worker or enable WhatsApp.

- API: `POST https://api.openai.com/v1/responses`, fixed `gpt-6-luna`, strict `json_schema`, `reasoning.effort=none`, `store=false`, no model tools.
- No new dependency, model framework or LLM credentials in source control.
- Limited to one outbound call per local runner invocation. Max 12 KiB JSON input request and 256 output tokens; 16 KiB response bound; 20-second timeout; no proxy, redirects, retries, or raw request/response logging.
- Only a short fixture catalog of `homelab`, `homelab/media-automation` and `recipe-intelligence` is transmitted. **These IDs are illustrative, not a fresh status check.**
- The existing `assistant_decision.decide()` validates all model decisions again against the read-only registry and grounded selector. The API cannot grant capabilities.

## Before the first paid call (operator gate)

1. In a trusted Linux/WSL Python 3.11+ environment, check out the merged default branch and install the package (`python -m pip install -e .`). The script deliberately does not support Windows credential file permission semantics.
2. Obtain an **API project key with separate billing** and a small platform-side spending limit. A ChatGPT Plus/Pro subscription and Codex credits do not automatically authorize/pay for Responses API calls.
3. Store the key in a new **user-owned regular file**, outside GitHub, with mode `0600` or `0400`. The adapter will reject world-readable files, another UID, symlinks and absent files. Never paste the secret into GitHub, chat, source code or the command line.
4. Review current pricing/model availability before proceeding. At the 2026-10-29 pricing snapshot, `gpt-6-luna` standard text pricing was approximately **$0.10/1M input tokens and $0.50/1M output tokens** (excludes regional modes and future price changes). Manual script prints tokens, not a guarantee of final billed cost. **No persistent quota ledger exists in this experiment**, so do not use it as a service or batch job.

## Execute exactly one opt-in decision (no backend action)

From the repository root in a trusted Linux/WSL shell:

```bash
python scripts/brain_live_decision.py \
  --key-file "$HOME/.config/pab/brain-api-key" \
  --allow-paid-call
```

The script reads one non-sensitive request from standard input, sends it once, and prints a minimal safe summary (`action`, `capability`, `arguments`, `usage`). User message and model output are not retained by the script. It does **not** call any capability handler; follow-up execution still belongs to Gateway #25.

Recommended first Slovak test input:

```text
Čo máme ďalej robiť s Homelabom?
```

Expected `invoke_read_only` selecting `project_os.status` with `project_id=homelab`.

Other later **individually approved** test cases:

| Message | Expected |
| --- | --- |
| Je Core Agent zdravý? | `invoke_read_only`, `core.health`, no arguments |
| Aký je stav Media Automation? | `invoke_read_only`, `project_os.status(homelab, media-automation)` |
| Ako je na tom ten projekt? | `clarify`, no tool |
| Ahoj! | `respond_without_tool`, no tool |
| Reštartuj Sonarr. | `unsupported`, no tool |
| Čo je ďalej v Recipe Intelligence? | `invoke_read_only`, `project_os.status(recipe-intelligence)` |

Record for Issue #57 only: model, input *category* (not private verbatim text), expected vs actual action/selector, token usage, approximate local latency and safe error if applicable. Do not record API key, full prompt, response body, conversation transcript or medical/personal content.

## Failure semantics

- A missing key, 401/403, 429, network failure, timeout, incomplete/refusal response or invalid JSON ends the attempt. There is **no automatic retry**; failed outcome might still have been billed.
- The provider returns only the strict normalized proposal. The planner is always the final allowlist/selector authority; a valid JSON proposal can still be denied.
- If the model does not understand Slovak in these examples, evaluate the actual failure before adding rules or switching models. Avoid a model router until measured need.
- Audio/voice notes require separate speech-to-text evaluation owned by the Personal Assistant / WhatsApp workstream.

**Ownership:** Runtime & Intelligence owns model decision quality; Gateway #25 owns authenticated transport, delivery, and production integration; Project OS & Agent Architecture owns shared security/deployment and main Project State.
