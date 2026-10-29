# Brain v0.5 — Conversational Responses tool loop

_Status: merged code must be verified in GitHub CI; **not** a live Gateway deployment. See Issue #60 and Gateway Issue #25._

## What changed from v0.3/v0.4

This is a **normal conversational LLM loop**, not a single-function classifier or deterministically templated answer.

1. Authenticated owner adapter creates an `AssistantRequest` and a trusted `DecisionContext` from Core. It may pass bounded previously authenticated user/assistant conversation turns (no implicit long-term ChatGPT memory).
2. The LLM receives the request, prior short-term context and a **small allowed tool catalog**. It may answer directly (free-form Slovak) or ask for `project_os_status(project_id, workstream_id|null)`.
3. Brain checks every proposal against the **current read-only Registry**, the strictly validated selector and the trusted Core catalog **before any backend read**.
4. Gateway-owned callback produces a **verified, correlated `AssistantResult`** through its existing authenticated transport/control protocol, not an arbitrary raw handler call. Brain validates result correlation, sanitizes/structures source facts, and replays `function_call` with matching `function_call_output` to the model.
5. The model writes **its own natural answer**: explanation, comparison, caveats and opinions. The final output includes a deterministic source/freshness footer to distinguish Project OS records from live server health.
6. Gateway is responsible for reply delivery and all durable request/delivery replay protections. Brain does not send WhatsApp/ChatGPT messages.

```
ChatGPT / future WhatsApp
  → authenticated Gateway request + bounded short-term history
  → Conversational LLM via Responses API
     ↘ (optional) project_os_status project/workstream function call
       → trusted Registry + selector check
       → Gateway authorized Core/Project OS read-only result
     ↗ authenticated, correlated project facts + provenance
  → Conversational LLM analysis/recommendation in Slovak
  → source/freshness indicator
  → Gateway authenticated reply delivery (NOT YET WIRED)
```

For example, a user may ask *“Čo ďalej s Homelabom a ako to súvisí s Recipe Intelligence?”* The model can request both project facts, compare the returned **recorded** status and make its own clearly qualified recommendation. It may not invent up-to-date status without a tool read, change the Project State, execute Codex, issue shell commands, or claim a proposed change was performed.

## Cost-aware conversational defaults (v0.5.1)

- **One model makes the routing decision and writes the answer.** Do not chain a second intent-classification/focus-model request before the conversation. Direct conversational replies need only one Responses request; a grounded read usually needs one tool-request turn and one final-answer turn.
- Default replies are **natural, brief Slovak (normally 1–3 sentences)**, answering first without automatic headings, restated input, or filler. Longer explanations remain possible when explicitly requested.
- `max_output_tokens` is 320 per provider request, with a separate bounded answer-size parser. Project selector catalog contains only ID/workstream/name; verification freshness comes from the actual authenticated tool result, not a potentially stale catalog hint.
- When the model has already used two tool calls or reached the final provider request, `tool_choice=none` prevents another unnecessary/denied tool proposal. Same trusted tool and correlation policy as v0.5.
- Source references are appended as a **compact** deterministic footer, deduplicating repeated reads of one project. They indicate recorded Project OS verification, **not a real-time server check**.
- The provider already totals `input_tokens`/`output_tokens` per turn for the operator; compare measured costs and reliability before selecting a different model or implementing speculative routing caches. No autonomous API spending or new model router is enabled.

## Brain v0.6 optional source-only read routing

The existing conversational loop now has an **off-by-default** second read-only
tool: `homelab_audit`. It appears in the model's tool list only when the
trusted caller opts in. Executing it also requires an injected Gateway callback
and an existing read-only `homelab.audit` registry entry.

The callback delivers a correlated evidence object after checking the private
transport. Brain then validates the snapshot through the existing
`host_audit.validate_snapshot` and reports its collection timestamp, never
presenting the data as a live server check. Only the preapproved empty argument
object is accepted; unmatched correlation, stale evidence and unsupported tools
fail closed. Project OS status routing remains unchanged.

This milestone adds no deployment, plugin exposure, scheduler, backend
credentials or host-control permissions. Gateway #25 and the operator's
final Issue #67 closeout remain separate prerequisites.

## Exact limits and owner gates

- The v0.5 default permits only `project_os.status`. Opt-in v0.6 source also accepts `homelab.audit` through an explicitly registered read-only capability and trusted callback, with exactly `{}` arguments. No path, repository, URL, shell, media, calendar, Supabase or write tool is exposed.
- **2 model-proposed tool calls and 3 Responses requests maximum per user turn.** Duplicate tool selectors inside the same turn reuse one authenticated read (no duplicate execution); all model proposals are checked before any of that batch executes.
- `store=false`. Prior conversation is an optional caller-controlled, bounded last-8-message tuple; there is **no persistent conversation database**, no invisible external ChatGPT transcript synchronization, and no model-side long-term memory.
- Exact fixed Responses API endpoint and model from the existing provider (`gpt-6-luna`), `reasoning.effort=none`, `max_output_tokens=320`, strict JSON schema for `project_os_status` function arguments, max input body 32 KiB, max remote response 16 KiB, 20 s per request.
- The existing network adapter disables proxy and redirects, has **no retry**, and uses the existing owner-only Linux/WSL credential file (mode `0400`/`0600`) outside GitHub.
- Model reply is **not** a proof of factual accuracy. Backend validates source/correlation and appends provenance, but free-form answers may still contain mistakes. A stale/unknown/unavailable source must not become a claimed live fact. Never claim full reliability from fake-provider CI.
- This is not authorization to deploy or enable user-facing data access. Gateway #25 and platform owners retain permission, authentication, source freshness, audit and output delivery responsibility.

## Zero-cost CI acceptance

`pytest -q tests/test_assistant_conversation.py tests/test_brain_offline_roundtrip.py`

Scenarios include direct natural response, one/two distinct project tool reads, a short-context follow-up, duplicate selector cache, stale/unavailable sources, malformed model output, unknown project/unsafe tool denial, forged result correlation and bounded request/token budgets. All tool transport and snapshots are simulated for CI; no real API bill.

## Optional real paid API demo — synthetic sources ONLY

The owner may opt in to a manual one/two-turn demo **from trusted Linux/WSL** after checking the API billing limits. The demo deliberately fabricates only clearly marked fixture data; it does **not** access actual GitHub/Project OS/worker/WhatsApp and stores no messages.

```bash
cd ~/pab-live-test/personal-ai-brain
git pull --ff-only
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/brain_conversation_demo.py \
  --key-file ~/.config/pab/brain-api-key \
  --allow-paid-call
```

Suggested non-sensitive turns:

1. `Čo máme ďalej robiť s Homelabom?`
2. `A čo by si odporúčal ako ďalší krok? Je to overené?`

Expected broad behavior: model requests a safe Project OS read, sees `fixture-only` phase and `unknown` freshness, explains that real status is unverified, then naturally responds to the follow-up. **Do not interpret anything the demo says about Homelab as an actual verified project fact.**

Demo runs up to **two user turns × three paid API requests** (upper bound six). There is no persistent application-level monetary quota ledger in this one-off script. API project spending settings and prepaid credit are not a replacement for per-request caps, and spend enforcement may not be instantaneous. Use the script only after deliberate opt-in. It does not print or save the API key or the full original conversation in logs; the terminal naturally shows your own input.

## Next externally meaningful acceptance — Gateway owner

The next real milestone must be a **trusted end-to-end request** over Gateway #25:

- Native authenticated ChatGPT surface, approved reply target, real source-backed Core context.
- Actual `project_os.status` request/result over the accepted durable transport, with author/issue/request verification; read-only workstreams remain owned by Project OS.
- Return authenticated result to this conversational loop; LLM synthesizes a useful Slovak answer and the Gateway **actually delivers it** to the originating surface.
- Verify freshness, duplicate/replay/conflict, network failure, restart uncertainty and no write behavior; no new separate ledger or generic Codex bridge permissions.

**Do not** update main `.project/state.yaml` from this specialist; operational/project owner maintains it. WhatsApp and persistent cross-surface context are separate subsequent acceptance gates.
