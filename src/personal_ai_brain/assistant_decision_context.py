"""Bounded read-only project hints for an Assistant decision provider.

Only a trusted Core reader/adapter may build this from its CoreSnapshot.
Names and freshness are *data*, never instructions or authorization. No raw
project-state prose, repository locations, credentials or paths leave here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
import json
import re

from .assistant_project_os import StatusRejected, _workstream_id, select_project_status, validate_selector
from .core_agent import CoreSnapshot, StateRecord


MAX_PROJECTS = 64
MAX_CONTEXT_BYTES = 8192
_NAME = re.compile(r"[\w .()/&+-]{1,80}\Z", re.UNICODE)


class ContextRejected(ValueError):
    """Safe fixed source failure. Never echo map entries or source exceptions."""


@dataclass(frozen=True, slots=True)
class ProjectHint:
    project_id: str
    workstream_id: str | None
    name: str
    freshness: str


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Trusted, bounded selector catalog; immutable and safe to show to a model."""

    generated_on: str
    projects: tuple[ProjectHint, ...]

    def permits(self, arguments: dict[str, str]) -> bool:
        return any(
            entry.project_id == arguments.get("project_id")
            and entry.workstream_id == arguments.get("workstream_id")
            for entry in self.projects
        )


def _label(raw: object, fallback: str) -> str:
    # No instructions, newlines, markup, arbitrary source prose or huge fields.
    # Model-facing names remain untrusted DATA, even after lexical screening.
    if not isinstance(raw, str) or not _NAME.fullmatch(raw):
        return fallback
    return raw.strip() or fallback


def build_decision_context(
    snapshot: CoreSnapshot, *, today: date | None = None,
) -> DecisionContext:
    """Build from a complete, trusted Core snapshot; deny partial or ambiguous catalogs.

    This is a consistency/grounding layer, NOT source authentication. The caller
    owns source identity, Core read permissions and adapter authenticity.
    """
    reference = date.today() if today is None else today
    if type(reference) is not date or not isinstance(snapshot, CoreSnapshot):
        raise ContextRejected("context_unavailable")
    try:
        generated = date.fromisoformat(snapshot.generated_on)
        if generated.isoformat() != snapshot.generated_on or generated > reference:
            raise ValueError("invalid snapshot date")
        records = snapshot.records
        findings = snapshot.findings
        if (not isinstance(records, list) or not 1 <= len(records) <= MAX_PROJECTS
                or not isinstance(findings, list)):
            raise ValueError("snapshot size")
        # An incomplete source view can silently omit project IDs. Never turn
        # a partial list into a seemingly authoritative catalog.
        if any(f.severity == "error" or f.code in {
            "project_map_unreadable", "project_map_invalid", "state_unreadable",
            "state_path_missing", "repository_missing", "workstream_state_path_missing",
            "source_read_failure",
        } for f in findings):
            raise ValueError("incomplete source")
        selectors: list[tuple[StateRecord, dict[str, str]]] = []
        seen: set[tuple[str, str | None]] = set()
        roots: set[str] = set()
        for rec in records:
            if not isinstance(rec, StateRecord):
                raise ValueError("invalid record")
            if rec.scope not in {"project", "workstream"}:
                raise ValueError("invalid scope")
            workstream_id = _workstream_id(rec)
            selector = {"project_id": rec.project_id}
            if rec.scope == "workstream":
                if not isinstance(rec.path, str) or not rec.path.endswith((".yaml", ".yml")):
                    raise ValueError("invalid workstream path")
                selector["workstream_id"] = workstream_id
            validate_selector(selector)
            key = (rec.project_id, workstream_id)
            if key in seen:
                raise ValueError("duplicate selector")
            seen.add(key)
            if rec.scope == "project":
                roots.add(rec.project_id)
            selectors.append((rec, selector))
        if any(rec.scope == "workstream" and rec.project_id not in roots for rec, _ in selectors):
            raise ValueError("orphan workstream")

        hints = []
        for rec, selector in selectors:
            status = select_project_status(snapshot, selector, today=reference)
            freshness = status["verification"]["freshness"]
            # Even a recently verified record is not current if the *snapshot*
            # was captured before today.
            if generated != reference and freshness == "current":
                freshness = "stale"
            fallback = selector.get("workstream_id") or selector["project_id"]
            hints.append(ProjectHint(
                selector["project_id"], selector.get("workstream_id"),
                _label(rec.project_name, fallback), freshness,
            ))

        # Duplicate human-facing labels are ambiguous. Withhold them in favor
        # of explicit logical selectors; the model should clarify vague asks.
        counts = Counter(h.name.casefold() for h in hints)
        hints = [
            replace(h, name=f"{h.project_id}/{h.workstream_id}" if h.workstream_id else h.project_id)
            if counts[h.name.casefold()] > 1 else h
            for h in hints
        ]
        hints.sort(key=lambda h: (h.project_id, h.workstream_id or ""))
        context = DecisionContext(generated.isoformat(), tuple(hints))
        size = len(json.dumps({
            "generated_on": context.generated_on,
            "projects": [
                {"project_id": h.project_id, "workstream_id": h.workstream_id,
                 "name": h.name, "freshness": h.freshness}
                for h in context.projects
            ],
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > MAX_CONTEXT_BYTES:
            raise ValueError("context budget")
        return context
    except (ValueError, TypeError, AttributeError, StatusRejected, RecursionError):
        raise ContextRejected("context_unavailable") from None
