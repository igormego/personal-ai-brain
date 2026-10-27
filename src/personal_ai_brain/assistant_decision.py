"""Bounded, offline-testable Assistant decision planner (Issue #52).

The model proposes a decision; the existing control Registry remains the
authority for capability identity, argument validation and risk. This module
does not authenticate callers, invoke handlers, persist data or deliver replies.
Only a trusted surface adapter may supply an authenticated AssistantRequest.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Callable

from .assistant_contract import AssistantRequest
from .assistant_decision_context import DecisionContext
from .control import Registry, Rejected, unique_object


# Deliberately smaller than the runtime registry, which also contains
# operational diagnostics and optional bounded work capabilities.
ALLOWED_CAPABILITIES = ("project_os.status", "core.health")
MAX_DECISION_BYTES = 2048

_FIXED_RESPONSES = {
    "clarify": "Ktorý projekt máš na mysli?",
    "respond_without_tool": "Ahoj! Ako ti môžem pomôcť?",
    "unsupported": "Túto operáciu teraz neviem bezpečne vykonať.",
}


class DecisionRejected(ValueError):
    """Fixed failure codes; never include user text, model output or exceptions."""


@dataclass(frozen=True, slots=True)
class CapabilityHint:
    id: str
    description: str
    argument_schema: dict


@dataclass(frozen=True, slots=True)
class DecisionPlan:
    request_id: str
    request_hash: str
    action: str
    capability: str | None
    arguments: dict | None
    public_text: str | None


# A provider accepts only the current message and a bounded, trusted catalog.
# It must return one JSON object as a string; no direct tool invocation is
# permitted. A real provider owns its own timeout, token cap and budget.
DecisionProvider = Callable[[str, tuple[CapabilityHint, ...], DecisionContext | None], str]


def _catalog(registry: Registry) -> tuple[CapabilityHint, ...]:
    hints = []
    for capability_id in ALLOWED_CAPABILITIES:
        capability = registry.capabilities.get(capability_id)
        if capability is None:
            continue
        if capability.risk_level != "read_only" or capability.side_effect_class != "none":
            continue
        # The provider may mutate the hint, but cannot alter registry policy.
        hints.append(CapabilityHint(
            capability.id, capability.description, deepcopy(capability.argument_schema)
        ))
    return tuple(hints)


def _decode(raw: object) -> dict:
    if not isinstance(raw, str):
        raise DecisionRejected("model_decision_invalid")
    try:
        if len(raw.encode("utf-8")) > MAX_DECISION_BYTES:
            raise DecisionRejected("model_decision_invalid")
        value = json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except DecisionRejected:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, Rejected):
        raise DecisionRejected("model_decision_invalid") from None
    if not isinstance(value, dict):
        raise DecisionRejected("model_decision_invalid")
    return value


def decide(
    request: AssistantRequest,
    registry: Registry,
    provider: DecisionProvider,
    *,
    context: DecisionContext | None = None,
) -> DecisionPlan:
    """Validate a model proposal; produce an inert, correlated decision plan.

    The caller must authenticate the request separately. Execution belongs to
    the existing trusted Gateway/control transport, which must re-authorize the
    plan and retain its own idempotency/replay/correlation ledger.
    """
    if not isinstance(request, AssistantRequest) or not isinstance(registry, Registry):
        raise DecisionRejected("trusted_input_required")
    if context is not None and not isinstance(context, DecisionContext):
        raise DecisionRejected("trusted_context_required")
    if not callable(provider):
        raise DecisionRejected("provider_unavailable")

    catalog = _catalog(registry)
    try:
        raw = provider(request.message, catalog, context)
    except Exception:
        raise DecisionRejected("provider_unavailable") from None
    value = _decode(raw)
    action = value.get("action")

    if isinstance(action, str) and action in _FIXED_RESPONSES:
        if set(value) != {"action"}:
            raise DecisionRejected("model_decision_invalid")
        return DecisionPlan(
            request.request_id, request.payload_hash, action,
            None, None, _FIXED_RESPONSES[action],
        )

    if action != "invoke_read_only" or set(value) != {"action", "capability", "arguments"}:
        raise DecisionRejected("model_decision_invalid")
    name = value["capability"]
    if not isinstance(name, str) or name not in {hint.id for hint in catalog}:
        raise DecisionRejected("decision_denied")

    try:
        capability, arguments = registry.resolve(name, value["arguments"])
        if (capability.id != name or capability.risk_level != "read_only"
                or capability.side_effect_class != "none" or not isinstance(arguments, dict)):
            raise DecisionRejected("decision_denied")
    except DecisionRejected:
        raise
    except Exception:
        raise DecisionRejected("decision_denied") from None

    if name == "project_os.status":
        if context is None:
            raise DecisionRejected("context_unavailable")
        if not context.permits(arguments):
            raise DecisionRejected("decision_denied")

    return DecisionPlan(
        request.request_id, request.payload_hash, "invoke_read_only",
        capability.id, deepcopy(arguments), None,
    )
