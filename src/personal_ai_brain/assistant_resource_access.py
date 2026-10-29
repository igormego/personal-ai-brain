"""A bounded read-only resource directory, not another per-question tool list.

Only existing trusted Core selectors and an explicitly opted-in host snapshot
can appear. Identifiers are opaque resource keys, not filesystem paths, URLs,
endpoints or commands. The backend still separately enforces registry grants,
transport identity, freshness and result correlation before any read.
"""

from __future__ import annotations

from .assistant_decision_context import DecisionContext


class ResourceDenied(ValueError):
    """Fixed error code, never interpolate untrusted resource text."""


def resource_catalog(
    context: DecisionContext, *, include_host_snapshot: bool = False,
) -> tuple[dict[str, str], ...]:
    """Model-visible resource discovery derived from a trusted Core catalog."""
    if (not isinstance(context, DecisionContext)
            or type(include_host_snapshot) is not bool
            or not 1 <= len(context.projects) <= 64):
        raise ResourceDenied("resources_unavailable")
    records = []
    keys = set()
    for entry in context.projects:
        if entry.workstream_id is None:
            key = f"project-os/{entry.project_id}"
        else:
            key = f"project-os/{entry.project_id}/workstream/{entry.workstream_id}"
        if (key in keys or len(key) > 200
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-/" for c in key)):
            raise ResourceDenied("resources_unavailable")
        keys.add(key)
        records.append({
            "resource_id": key, "source": "recorded_project_state",
            "name": entry.name,
        })
    if include_host_snapshot:
        records.append({
            "resource_id": "homelab/host-snapshot",
            "source": "operator_collected_snapshot_not_live",
            "name": "Homelab host resources and container health",
        })
    return tuple(records)


def resolve_resource(
    resource_id: object, context: DecisionContext, *,
    include_host_snapshot: bool = False,
) -> tuple[str, dict[str, str]]:
    """Resolve one exact advertised read key, without dynamic URLs or paths."""
    if type(resource_id) is not str or not 1 <= len(resource_id) <= 200:
        raise ResourceDenied("resource_denied")
    allowed = {
        entry["resource_id"]
        for entry in resource_catalog(context, include_host_snapshot=include_host_snapshot)
    }
    if resource_id not in allowed:
        raise ResourceDenied("resource_denied")
    if resource_id == "homelab/host-snapshot":
        return "homelab.audit", {}
    parts = resource_id.split("/")
    if len(parts) == 2:
        return "project_os.status", {"project_id": parts[1]}
    if len(parts) == 4 and parts[2] == "workstream":
        return "project_os.status", {
            "project_id": parts[1], "workstream_id": parts[3],
        }
    raise ResourceDenied("resource_denied")
