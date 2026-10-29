"""A real conversational, read-only, stateless Brain tool loop (v0.5).

The model can write original answers and decide when to request project data.
Only a trusted Gateway callback can supply the actual project_os.status result.
This module neither authenticates a surface nor executes Registry handlers,
contacts GitHub, persists a conversation, or delivers an outbound message.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from .assistant_contract import AssistantRequest
from .assistant_decision_context import DecisionContext
from .assistant_grounded_reply import ReplyRejected, _facts
from .assistant_project_os import StatusRejected, validate_selector
from .assistant_result import AssistantResult, MAX_TEXT_BYTES
from .control import Registry

MAX_MODEL_STEPS = 3
MAX_TOOL_CALLS = 2
MAX_HISTORY_ITEMS = 8
MAX_HISTORY_BYTES = 8192
MAX_ASSISTANT_BYTES = 3072
MAX_TOOL_OUTPUT_BYTES = 4096


class ConversationRejected(ValueError):
    """Fixed failure codes only: never echo user, provider or source content."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModelStep:
    """Exactly one completed model reply: either free text or tool call(s)."""

    text: str | None
    tool_calls: tuple[ModelToolCall, ...]
    replay_items: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class SourceReference:
    project_id: str
    workstream_id: str | None
    freshness: str
    last_verified: str
    status: str


@dataclass(frozen=True, slots=True)
class ConversationReply:
    request_id: str
    request_hash: str
    surface: str
    reply_target: str
    text: str
    history: tuple[ChatMessage, ...]
    sources: tuple[SourceReference, ...]
    model_steps: int
    tool_calls: int


# The producer MUST verify its source/author and the transport idempotency
# ledger. This is NOT permission for the Brain to call arbitrary handlers.
ReadProjectStatus = Callable[[AssistantRequest, dict[str, str]], AssistantResult]
ModelProvider = Callable[[tuple[dict, ...], DecisionContext], ModelStep]


def _history(history: tuple[ChatMessage, ...]) -> list[dict]:
    if (not isinstance(history, tuple) or len(history) > MAX_HISTORY_ITEMS
            or len(history) % 2 != 0):
        raise ConversationRejected("history_invalid")
    result = []
    total = 0
    for i, item in enumerate(history):
        if (not isinstance(item, ChatMessage)
                or item.role != ("user" if i % 2 == 0 else "assistant")
                or not isinstance(item.text, str) or not item.text.strip()):
            raise ConversationRejected("history_invalid")
        try:
            length = len(item.text.encode("utf-8"))
        except UnicodeError:
            raise ConversationRejected("history_invalid") from None
        if length > MAX_ASSISTANT_BYTES:
            raise ConversationRejected("history_invalid")
        total += length
        if total > MAX_HISTORY_BYTES:
            raise ConversationRejected("history_invalid")
        result.append({"role": item.role, "content": item.text})
    return result


def _selector(
    call: ModelToolCall, registry: Registry, context: DecisionContext,
) -> dict[str, str]:
    if (not isinstance(call, ModelToolCall)
            or call.name != "project_os_status"
            or not isinstance(call.arguments, dict)
            or set(call.arguments) != {"project_id", "workstream_id"}):
        raise ConversationRejected("tool_denied")
    raw = call.arguments
    if raw["workstream_id"] is not None and not isinstance(raw["workstream_id"], str):
        raise ConversationRejected("tool_denied")
    arguments = {"project_id": raw["project_id"]}
    if raw["workstream_id"] is not None:
        arguments["workstream_id"] = raw["workstream_id"]
    try:
        selected = validate_selector(arguments)
        capability, normalized = registry.resolve("project_os.status", selected)
        if (
            capability.id != "project_os.status"
            or capability.risk_level != "read_only"
            or capability.side_effect_class != "none"
            or not isinstance(normalized, dict)
            or normalized != selected
            or not context.permits(normalized)
        ):
            raise ConversationRejected("tool_denied")
    except ConversationRejected:
        raise
    except Exception:
        raise ConversationRejected("tool_denied") from None
    return selected


def _tool_payload(
    request: AssistantRequest, selector: dict[str, str], result: AssistantResult,
) -> tuple[str, SourceReference]:
    if not isinstance(result, AssistantResult):
        raise ConversationRejected("result_untrusted")
    if (
        result.schema_version != request.schema_version
        or result.request_id != request.request_id
        or result.request_hash != request.payload_hash
        or result.user_id != request.user_id
        or result.surface != request.surface
        or result.reply_target != request.reply_target
        or result.capability != "project_os.status"
    ):
        raise ConversationRejected("result_correlation")
    if result.status == "completed":
        try:
            facts = _facts(result, selector)
        except ReplyRejected:
            raise ConversationRejected("result_untrusted") from None
        payload = {
            "status": "completed", "project_id": facts.project_id,
            "workstream_id": selector.get("workstream_id"),
            "project_name": facts.project_name, "phase": facts.phase,
            "project_status": facts.status,
            "current_gate": facts.gate,
            "exact_next_action": facts.next_action,
            "verification": {
                "last_verified": facts.last_verified, "freshness": facts.freshness,
                "sync_status": facts.sync_status,
            },
            "source_findings": facts.findings,
        }
        source = SourceReference(facts.project_id, selector.get("workstream_id"),
                                 facts.freshness, facts.last_verified, "completed")
    elif (result.status in {"unavailable", "failed", "rejected"}
          and result.error_code in {
              "source_unavailable", "source_conflict", "execution_failed",
              "request_rejected",
          } and result.freshness in {"unknown", "conflicting"}):
        payload = {
            "status": result.status,
            "project_id": selector["project_id"],
            "workstream_id": selector.get("workstream_id"),
            "freshness": result.freshness,
            "error_code": result.error_code,
        }
        source = SourceReference(selector["project_id"], selector.get("workstream_id"),
                                 result.freshness, "unknown", result.status)
    else:
        raise ConversationRejected("result_untrusted")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_TOOL_OUTPUT_BYTES:
        raise ConversationRejected("result_too_large")
    return text, source


def _footnote(sources: tuple[SourceReference, ...]) -> str:
    if not sources:
        return ""
    # Small, deterministic provenance; don't ask the LLM to restate every fact.
    seen: set[tuple[str, str | None, str, str, str]] = set()
    items = []
    for source in sources:
        key = (source.project_id, source.workstream_id,
               source.status, source.freshness, source.last_verified)
        if key in seen:
            continue
        seen.add(key)
        name = source.project_id + (
            "/" + source.workstream_id if source.workstream_id else ""
        )
        items.append(f"{name} [{source.status}, {source.freshness}, {source.last_verified}]")
    note = "\n\nZdroj: Project OS (záznam, nie live): " + "; ".join(items)
    if any(s.freshness != "current" for s in sources):
        note += "\nPozor: aspoň jeden podklad nie je aktuálne potvrdený."
    return note



def run_conversation(
    request: AssistantRequest,
    registry: Registry,
    context: DecisionContext,
    provider: ModelProvider,
    read_project_status: ReadProjectStatus,
    *,
    history: tuple[ChatMessage, ...] = (),
) -> ConversationReply:
    """One bounded conversational turn: LLM → permitted reads → LLM reply.

    Trusted Gateway caller authenticates the request, constructs a complete
    source-backed DecisionContext, supplies authenticated results, and owns any
    durable delivery/replay ledger. No implicit model calls or background work.
    """
    if (not isinstance(request, AssistantRequest) or not isinstance(registry, Registry)
            or not isinstance(context, DecisionContext)
            or not callable(provider) or not callable(read_project_status)):
        raise ConversationRejected("trusted_input_required")

    items = _history(history)
    items.append({"role": "user", "content": request.message})
    references: list[SourceReference] = []
    executed: dict[tuple[str, str | None], tuple[str, SourceReference]] = {}
    total_calls = 0
    all_call_ids: set[str] = set()

    for iteration in range(MAX_MODEL_STEPS):
        try:
            step = provider(tuple(items), context)
        except Exception:
            raise ConversationRejected("model_unavailable") from None
        if not isinstance(step, ModelStep):
            raise ConversationRejected("model_response_invalid")
        if step.text is not None:
            if step.tool_calls or step.replay_items:
                raise ConversationRejected("model_response_invalid")
            if not isinstance(step.text, str) or not step.text.strip():
                raise ConversationRejected("model_response_invalid")
            try:
                if len(step.text.encode("utf-8")) > MAX_ASSISTANT_BYTES:
                    raise ConversationRejected("model_response_invalid")
            except UnicodeError:
                raise ConversationRejected("model_response_invalid") from None
            footnote = _footnote(tuple(references))
            final = step.text.strip()
            if len((final + footnote).encode("utf-8")) > MAX_TEXT_BYTES + 600:
                raise ConversationRejected("reply_too_large")
            # Keep bounded short-term history without retaining full prompts.
            # A valid new user request may be larger than a historical item.
            bounded_user = request.message.encode("utf-8")[:MAX_ASSISTANT_BYTES].decode(
                "utf-8", errors="ignore"
            )
            updated = history + (ChatMessage("user", bounded_user),
                                 ChatMessage("assistant", final))
            updated = updated[-MAX_HISTORY_ITEMS:]
            while sum(len(m.text.encode("utf-8")) for m in updated) > MAX_HISTORY_BYTES:
                updated = updated[2:]
            return ConversationReply(request.request_id, request.payload_hash,
                                     request.surface, request.reply_target,
                                     final + footnote, updated, tuple(references),
                                     iteration + 1, total_calls)

        if (not isinstance(step.tool_calls, tuple) or not step.tool_calls
                or not isinstance(step.replay_items, tuple)
                or len(step.replay_items) != len(step.tool_calls)):
            raise ConversationRejected("model_response_invalid")
        if (iteration == MAX_MODEL_STEPS - 1
                or total_calls + len(step.tool_calls) > MAX_TOOL_CALLS):
            raise ConversationRejected("tool_budget_exceeded")

        # Prevalidate every proposed call BEFORE any tool executes.
        planned = []
        seen_ids = set()
        for call in step.tool_calls:
            if (not isinstance(call, ModelToolCall)
                    or not isinstance(call.call_id, str)
                    or not 1 <= len(call.call_id) <= 120
                    or not call.call_id.startswith("call_")
                    or not all(c.isascii() and (c.isalnum() or c in "_-") for c in call.call_id)
                    or call.call_id in seen_ids
                    or call.call_id in all_call_ids):
                raise ConversationRejected("model_response_invalid")
            seen_ids.add(call.call_id)
            planned.append((call, _selector(call, registry, context)))
        for item, (call, _) in zip(step.replay_items, planned):
            if (not isinstance(item, dict)
                    or set(item) != {"type", "call_id", "name", "arguments"}
                    or item["type"] != "function_call"
                    or item["call_id"] != call.call_id
                    or item["name"] != call.name):
                raise ConversationRejected("model_response_invalid")
            try:
                if json.loads(item["arguments"]) != call.arguments:
                    raise ValueError("mismatch")
            except (TypeError, ValueError, RecursionError):
                raise ConversationRejected("model_response_invalid") from None
        all_call_ids.update(seen_ids)

        items.extend(step.replay_items)
        for call, selector in planned:
            key = (selector["project_id"], selector.get("workstream_id"))
            if key not in executed:
                try:
                    result = read_project_status(request, selector)
                except Exception:
                    # Never guess source truth or leak infrastructure exception.
                    raise ConversationRejected("tool_unavailable") from None
                executed[key] = _tool_payload(request, selector, result)
            output, source = executed[key]
            references.append(source)
            items.append({"type": "function_call_output", "call_id": call.call_id,
                          "output": output})
        total_calls += len(planned)

    raise ConversationRejected("model_turn_budget_exceeded")
