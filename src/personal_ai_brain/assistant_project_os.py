"""Bounded read-only Project OS status selection from an existing Core snapshot.

The request selects a *logical project ID*, never a repo, ref, path or URL.
All source reads and permissions remain with the accepted Core Agent reader.
"""
from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from .assistant_contract import AssistantRequest
from .assistant_result import AssistantResult, MAX_TEXT_BYTES, build_result
from .core_agent import CoreSnapshot, StateRecord


_PROJECT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_STALE_STATES = {"stale_needs_refresh", "needs_initialization", "synced_but_old"}
_TOKEN = re.compile(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+")
_CREDENTIAL = re.compile(r"(?i)(?:bearer\s+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,\"}]+")
_MAX_FIELD_CHARS = 1100


class StatusRejected(ValueError):
    """Fixed codes only. Never echo project-state contents in errors."""


def validate_selector(arguments: object) -> dict[str, str]:
    """Only logical IDs. Arbitrary repository/path/command fields are rejected."""
    if not isinstance(arguments, Mapping) or "project_id" not in arguments:
        raise StatusRejected("invalid_project_selector")
    if set(arguments) - {"project_id", "workstream_id"}:
        raise StatusRejected("invalid_project_selector")
    for field in arguments:
        value = arguments[field]
        if not isinstance(value, str) or len(value) > 64 or not _PROJECT_ID.fullmatch(value):
            raise StatusRejected("invalid_project_selector")
    return dict(arguments)


def _workstream_id(record: StateRecord) -> str | None:
    if record.scope != "workstream":
        return None
    # ID is already present in the trusted Core map path. The caller never gives a path.
    return PurePosixPath(record.path).stem


def _bounded(value: str) -> str:
    # Preserve source wording but do not forward control characters or credentials.
    safe = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in value)
    safe = _TOKEN.sub("[REDACTED]", safe)
    safe = _CREDENTIAL.sub("[REDACTED]", safe)
    return safe[:_MAX_FIELD_CHARS]


def select_project_status(
    snapshot: CoreSnapshot, arguments: object, *, today: date | None = None,
) -> dict[str, Any]:
    """Select exactly one record and conservatively classify verification freshness.

    'current' describes recently verified *recorded state*, never live host health.
    The Core Agent retains source/map validation and ownership.
    """
    selector = validate_selector(arguments)
    if not isinstance(snapshot, CoreSnapshot):
        raise StatusRejected("source_unavailable")
    project_id = selector["project_id"]
    workstream_id = selector.get("workstream_id")
    matched = [
        rec for rec in snapshot.records if rec.project_id == project_id
        and ((rec.scope == "project" and workstream_id is None)
             or (rec.scope == "workstream" and _workstream_id(rec) == workstream_id))
    ]
    if not matched:
        raise StatusRejected("source_unavailable")
    if len(matched) != 1:
        raise StatusRejected("source_conflict")
    record = matched[0]

    # Do not project findings from *other* projects onto the selected record.
    findings = [f for f in snapshot.findings if f.subject == record.project_name]
    codes = sorted({f.code for f in findings})[:12]
    warnings = any(f.severity in {"error", "warning"} for f in findings)
    errors = any(f.severity == "error" for f in findings)
    reference = today or date.today()
    if not isinstance(reference, date):
        raise StatusRejected("source_unavailable")
    try:
        checked = date.fromisoformat(record.last_verified)
        days = (reference - checked).days
        if days < 0:
            raise ValueError("future")
    except (TypeError, ValueError):
        checked, days = None, None

    if errors or days is None or record.sync_status in {"unknown", "failed", "pending"}:
        freshness = "unknown"
    elif record.status in _STALE_STATES or record.sync_status != "synced" or warnings or days > 7:
        freshness = "stale"
    else:
        freshness = "current"

    return {
        "project_id": record.project_id,
        "workstream_id": workstream_id,
        "project_name": _bounded(record.project_name),
        "scope": record.scope,
        "status": _bounded(record.status),
        "phase": _bounded(record.phase),
        "attention": _bounded(record.attention),
        "current_gate": _bounded(record.current_gate),
        "exact_next_action": _bounded(record.exact_next_action),
        "verification": {
            "last_verified": record.last_verified or None,
            "age_days": days,
            "sync_status": record.sync_status,
            "freshness": freshness,
            "finding_codes": codes,
        },
    }


def answer_project_status(
    request: AssistantRequest, snapshot: CoreSnapshot, arguments: object,
    *, today: date | None = None,
) -> AssistantResult:
    """Produce the existing correlated AssistantResult; no execution or delivery.

    The trusted adapter selects the logical ID. The result cannot send a message;
    a separate authenticated/durable reply transport owns that responsibility.
    """
    try:
        status = select_project_status(snapshot, arguments, today=today)
    except StatusRejected as exc:
        code = str(exc)
        return build_result(
            request, status="unavailable",
            freshness="conflicting" if code == "source_conflict" else "unknown",
            error_code="source_conflict" if code == "source_conflict" else "source_unavailable",
        )
    v = status["verification"]
    lines = [
        f"Project: {status['project_name']} ({status['project_id']})",
        f"Status: {status['status']}; phase: {status['phase']}",
        f"Last verified: {v['last_verified'] or 'unknown'}; freshness: {v['freshness']}; sync: {v['sync_status']}",
        f"Gate: {status['current_gate'] or 'Not recorded'}",
        f"Next action: {status['exact_next_action'] or 'Not recorded'}",
    ]
    if v["finding_codes"]:
        lines.append("Source findings: " + ", ".join(v["finding_codes"]))
    answer = "\n".join(lines)
    if len(answer.encode("utf-8")) > MAX_TEXT_BYTES:
        # Fail closed rather than producing a misleading partial next action.
        return build_result(request, status="unavailable", freshness="unknown",
                            error_code="source_unavailable")
    return build_result(request, status="completed", freshness=v["freshness"],
                        public_text=answer)
