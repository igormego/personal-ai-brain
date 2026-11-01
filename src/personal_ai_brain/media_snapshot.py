"""Credential-free, fixed-path Sonarr snapshot reader for the trusted control worker.

The collector and write-capable Sonarr credential MUST remain on the host.
This reader only consumes one pre-sanitized, operator-mounted read-only file.
No network calls, arbitrary filesystem selectors or writes are possible.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from .assistant_media_resources import (
    MediaResourceUnavailable, sanitize_media_resource, validate_media_selector,
)

MAX_SNAPSHOT_BYTES = 4096
MOUNTED_SNAPSHOT_PATH = Path("/media-audit/snapshot.json")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def read_media_snapshot(path: Path, resource_id: str, *, now=None) -> dict:
    """Fail closed on stale, tampered or unexpectedly accessible file content.

    A deployment operator separately verifies the private directory, file
    ownership parity (host / container UID), and read-only container mount.
    """
    descriptor = None
    try:
        validate_media_selector({"resource_id": resource_id})
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("invalid_snapshot_path")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        )
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > MAX_SNAPSHOT_BYTES
                or (info.st_mode & 0o077) != 0
                or info.st_uid not in (0, os.geteuid())):
            raise ValueError("snapshot_access_denied")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot_too_large")
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
        # The authoritative strict schema and 30-minute freshness gate are
        # enforced on EVERY read, never cached as a forever-current result.
        sanitize_media_resource(resource_id, data, now=now)
        return data  # Registry independently normalizes into a filtered result.
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise MediaResourceUnavailable("media_resource_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
