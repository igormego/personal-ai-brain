"""Credential-free, fixed-path Media snapshot reader for trusted control.

Host-only collectors and application API keys never enter the control
container. Each allowlisted resource has one independently mounted, read-only,
private file. Callers can never supply URLs, paths or file names.
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
MOUNTED_SNAPSHOT_PATH = Path("/media-audit/snapshot.json")  # Existing Sonarr contract.
MOUNTED_MOVIES_SNAPSHOT_PATH = Path("/media-audit/movies-watchlist-overview.json")
MOUNTED_WATCHLIST_SNAPSHOT_PATH = Path("/watchlist-audit/snapshot.json")


def read_mounted_watchlist_items():
    """Fixed operator-published snapshot; no provider is wired by the worker."""
    descriptor = None
    directory = None
    try:
        directory = os.open(MOUNTED_WATCHLIST_SNAPSHOT_PATH.parent,
                            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(directory)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError("snapshot_directory_unsafe")
        descriptor = os.open(MOUNTED_WATCHLIST_SNAPSHOT_PATH.name,
                             os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > 2_000_000
                or info.st_mode & 0o077 or info.st_uid not in (0, os.geteuid())):
            raise ValueError("snapshot_access_denied")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("snapshot_too_large")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise MediaResourceUnavailable("media_resource_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


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


def read_mounted_media_snapshot(resource_id: str, *, movies_enabled: bool = False,
                                now=None) -> dict:
    """Strict resource->file mapping; movie grant is separate and OFF by default.

    This function is called only after the control worker has enabled the
    existing master PAB_MEDIA_RESOURCE_ENABLED switch. It must NOT accept a
    request-selected path, resource alias, URL or environment-provided filename.
    """
    try:
        validate_media_selector({"resource_id": resource_id})
        if type(movies_enabled) is not bool:
            raise ValueError("invalid_grant")
        if resource_id == "sonarr/overview":
            path = MOUNTED_SNAPSHOT_PATH
        elif resource_id == "movies/watchlist-overview" and movies_enabled:
            path = MOUNTED_MOVIES_SNAPSHOT_PATH
        else:
            raise ValueError("resource_not_granted")
        return read_media_snapshot(path, resource_id, now=now)
    except (OSError, ValueError, TypeError):
        raise MediaResourceUnavailable("media_resource_unavailable") from None
