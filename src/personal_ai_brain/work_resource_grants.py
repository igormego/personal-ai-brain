"""Host-only Resource Broker input for the existing isolated Work Runner.

An operator installs one private, read-only, fixed snapshot mount accessible
to the trusted builder process. A model can request only a registered logical
resource ID, never an input byte string, filesystem path, URL or credential.
This module is source-only until the separate operator grant is approved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .host_audit import AuditUnavailable, read_snapshot
from .work_contract import HOST_RESOURCE
from .workspace_artifacts import InputGrant

# A fixed operator-managed mount/ACL, not a model input or environment variable.
# The production Work Runner profile must independently opt in.
HOST_SNAPSHOT = Path("/opt/personal-ai-brain/worker-resources/host-snapshot.json")
MAX_RESOURCE_INPUT = 4096


class ResourceGrantUnavailable(ValueError):
    """Fixed code only; never reveal operator paths or snapshot contents."""


def load_host_resource(resource_id: object, *, path: Path = HOST_SNAPSHOT):
    """Return trusted task-local immutable bytes and a non-sensitive receipt.

    The existing host audit validator verifies file ownership, no-follow,
    bounded size, complete schema and collection freshness. The snapshot
    includes *sanitized host facts* only, not logs/tokens/credentials.
    No automatic refresh or host probe is allowed here.
    """
    if type(resource_id) is not str or resource_id != HOST_RESOURCE:
        raise ResourceGrantUnavailable("resource_unavailable")
    try:
        value = read_snapshot(path)
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":")).encode("utf-8")
        if not 1 <= len(raw) <= MAX_RESOURCE_INPUT:
            raise ValueError("resource_size")
        return (
            (InputGrant(HOST_RESOURCE, "host-snapshot.json", raw),),
            {
                "resource_id": HOST_RESOURCE,
                "source": value["source"],
                "collected_at": value["collected_at"],
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
    except (AuditUnavailable, OSError, ValueError, TypeError, KeyError):
        raise ResourceGrantUnavailable("resource_unavailable") from None
