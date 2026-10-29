"""Bounded, provenance-aware Project OS reply composition (Brain v0.4).

This runs only AFTER the trusted Gateway has authenticated the requester, used
the read-only capability, verified the owner-produced result and its delivery
correlation. This module is NOT a transport, a Core reader or an executor.

An optional LLM may choose *which verified fact to emphasize*, never compose
new claims. Every user-visible project fact is copied from the decoded,
bounded AssistantResult; stale/unknown source state is never called current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Callable

from .assistant_contract import AssistantRequest
from .assistant_decision import DecisionPlan
from .assistant_project_os import StatusRejected, validate_selector
from .assistant_result import AssistantResult, MAX_TEXT_BYTES


MAX_ANSWER_BYTES = MAX_TEXT_BYTES
FOCUS = frozenset({"next_action", "gate", "overview"})
_VERIFICATION = re.compile(
    r"Last verified: (unknown|\d{4}-\d{2}-\d{2}); "
    r"freshness: (current|stale|unknown|conflicting); sync: ([^\n]{1,100})\Z"
)
_FRESHNESS_NOTICE = {
    "current": "Zaznamenaný stav je podľa Project OS aktuálne overený; nejde o kontrolu bežiaceho systému.",
    "stale": "Pozor: zaznamenaný stav môže byť zastaraný. Pred zásahom ho treba overiť.",
    "unknown": "Pozor: aktuálnosť zaznamenaného stavu nie je potvrdená.",
    "conflicting": "Pozor: dostupné zdroje si odporujú. Aktuálny stav nie je potvrdený.",
}
_ERRORS = {
    "source_unavailable": "Stav projektu sa nepodarilo overiť. Nebudem si ho domýšľať.",
    "source_conflict": "Zdroje projektového stavu si odporujú. Aktuálny stav nie je potvrdený.",
    "request_rejected": "Táto požiadavka neprešla overením.",
    "execution_failed": "Načítanie projektového stavu sa nedokončilo.",
}


class ReplyRejected(ValueError):
    """Stable rejection codes; never interpolate source or model content."""


@dataclass(frozen=True, slots=True)
class ProjectFacts:
    project_id: str
    project_name: str
    status: str
    phase: str
    last_verified: str
    freshness: str
    sync_status: str
    gate: str
    next_action: str
    findings: str | None


@dataclass(frozen=True, slots=True)
class GroundedReply:
    request_id: str
    request_hash: str
    surface: str
    reply_target: str
    capability: str
    freshness: str
    text: str
    focus: str | None
    used_model: bool


# A model can prioritize trusted facts but cannot supply prose or tool actions.
FocusProvider = Callable[[str, ProjectFacts], str]


def _check_correlation(
    request: AssistantRequest, decision: DecisionPlan, result: AssistantResult,
) -> dict[str, str]:
    if (not isinstance(request, AssistantRequest)
            or not isinstance(decision, DecisionPlan)
            or not isinstance(result, AssistantResult)):
        raise ReplyRejected("trusted_contract_required")
    if (decision.request_id != request.request_id
            or decision.request_hash != request.payload_hash
            or result.request_id != request.request_id
            or result.request_hash != request.payload_hash
            or result.user_id != request.user_id
            or result.surface != request.surface
            or result.reply_target != request.reply_target
            or result.schema_version != request.schema_version):
        raise ReplyRejected("reply_correlation")
    if (decision.action != "invoke_read_only"
            or decision.capability != "project_os.status"
            or result.capability != "project_os.status"):
        raise ReplyRejected("reply_capability_denied")
    try:
        return validate_selector(decision.arguments)
    except StatusRejected:
        raise ReplyRejected("reply_capability_denied") from None


def _take(line: str, prefix: str) -> str:
    if not line.startswith(prefix) or not line[len(prefix):].strip():
        raise ReplyRejected("reply_source_invalid")
    return line[len(prefix):]


def _facts(result: AssistantResult, selector: dict[str, str]) -> ProjectFacts:
    if (result.status != "completed" or result.error_code is not None
            or result.freshness not in _FRESHNESS_NOTICE
            or not isinstance(result.text, str)):
        raise ReplyRejected("reply_source_invalid")
    try:
        if len(result.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ReplyRejected("reply_source_invalid")
    except UnicodeError:
        raise ReplyRejected("reply_source_invalid") from None
    lines = result.text.splitlines()
    if len(lines) not in (5, 6):
        raise ReplyRejected("reply_source_invalid")
    trailer = f" ({selector['project_id']})"
    name = _take(lines[0], "Project: ")
    if not name.endswith(trailer) or len(name) <= len(trailer):
        raise ReplyRejected("reply_source_invalid")
    name = name[:-len(trailer)]
    status_line = _take(lines[1], "Status: ")
    if "; phase: " not in status_line:
        raise ReplyRejected("reply_source_invalid")
    status, phase = status_line.split("; phase: ", 1)
    if not status or not phase:
        raise ReplyRejected("reply_source_invalid")
    verification = _VERIFICATION.fullmatch(lines[2])
    if verification is None or verification.group(2) != result.freshness:
        raise ReplyRejected("reply_source_invalid")
    verified, freshness, sync = verification.groups()
    if verified != "unknown":
        try:
            if date.fromisoformat(verified).isoformat() != verified:
                raise ValueError
        except ValueError:
            raise ReplyRejected("reply_source_invalid") from None
    gate = _take(lines[3], "Gate: ")
    action = _take(lines[4], "Next action: ")
    findings = _take(lines[5], "Source findings: ") if len(lines) == 6 else None
    if any(ord(c) < 32 or ord(c) == 127 for c in (name + status + phase + gate + action + sync + (findings or ""))):
        raise ReplyRejected("reply_source_invalid")
    return ProjectFacts(selector["project_id"], name, status, phase,
                        verified, freshness, sync, gate, action, findings)


def _render(facts: ProjectFacts, focus: str) -> str:
    gate = facts.gate if facts.gate != "Not recorded" else "nie je uvedená"
    action = facts.next_action if facts.next_action != "Not recorded" else "nie je uvedený"
    parts = [
        f"Podľa zaznamenaného stavu Project OS — {facts.project_name} ({facts.project_id}):",
    ]
    if focus == "next_action":
        parts.extend((f"Najbližší zapísaný krok: {action}",
                      f"Aktuálna brána: {gate}",
                      f"Stav: {facts.status}; fáza: {facts.phase}"))
    elif focus == "gate":
        parts.extend((f"Aktuálna brána: {gate}",
                      f"Najbližší zapísaný krok: {action}",
                      f"Stav: {facts.status}; fáza: {facts.phase}"))
    else:
        parts.extend((f"Stav: {facts.status}; fáza: {facts.phase}",
                      f"Aktuálna brána: {gate}",
                      f"Najbližší zapísaný krok: {action}"))
    parts.append(
        f"Posledné overenie: {facts.last_verified}; synchronizácia: {facts.sync_status}. "
        + _FRESHNESS_NOTICE[facts.freshness]
    )
    if facts.findings:
        parts.append("Zistenia zdroja: " + facts.findings)
    text = "\n".join(parts)
    if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ReplyRejected("reply_size")
    return text


def compose_project_reply(
    request: AssistantRequest,
    decision: DecisionPlan,
    result: AssistantResult,
    *,
    focus_provider: FocusProvider | None = None,
) -> GroundedReply:
    """Transform ONE authenticated Project OS result into a Slovak response.

    Does not read Project OS, trigger another capability or deliver a response.
    Even if a model is used, it can only choose a focus enum; project facts and
    provenance are rendered deterministically from the trusted result.
    """
    selector = _check_correlation(request, decision, result)
    if result.status != "completed":
        if (result.status not in {"unavailable", "failed", "rejected"}
                or result.error_code not in _ERRORS
                or result.freshness not in {"unknown", "conflicting"}):
            raise ReplyRejected("reply_source_invalid")
        text = _ERRORS[result.error_code]
        return GroundedReply(request.request_id, request.payload_hash, request.surface,
                             request.reply_target, "project_os.status", result.freshness,
                             text, None, False)

    facts = _facts(result, selector)
    focus = "overview"
    used_model = False
    if focus_provider is not None:
        if not callable(focus_provider):
            raise ReplyRejected("focus_provider_invalid")
        try:
            proposed = focus_provider(request.message, facts)
            if isinstance(proposed, str) and proposed in FOCUS:
                focus, used_model = proposed, True
        except Exception:
            # A failed or malformed optional model cannot hide trusted status.
            # No retries, tool invocation or model-authored text are allowed.
            pass
    text = _render(facts, focus)
    return GroundedReply(request.request_id, request.payload_hash, request.surface,
                         request.reply_target, "project_os.status", facts.freshness,
                         text, focus, used_model)
