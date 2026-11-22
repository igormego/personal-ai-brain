"""Credential-isolated, fixed-view ARR snapshot bridge for the existing read broker.

One trusted host-only collector reads fixed ARR GET endpoints and atomically
publishes sanitized inventory snapshots. The unprivileged Control process sees
only fixed read-only mounted files, NEVER ARR API credentials or network access.

Disabled unless the separately approved operator installs an isolated collector
and explicitly enables the independent service + disclosure grants.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import uuid
from xml.etree import ElementTree

from .resource_query import _arr_snapshot
from .arr_read import ARRReadProvider
from .arr_request_status import MEDIA_DIAGNOSTIC_VIEWS, REQUEST_STATUS_VIEWS
from .sonarr_diagnostics import SONARR_DIAGNOSTIC_VIEWS

ARR_FILES = {
    "arr/sonarr": {view: f"sonarr-{view}.json" for view in {"items", "profiles"} | SONARR_DIAGNOSTIC_VIEWS},
    "arr/radarr": {view: f"radarr-{view}.json" for view in MEDIA_DIAGNOSTIC_VIEWS["arr/radarr"]},
    "arr/seerr": {view: f"seerr-{view}.json" for view in MEDIA_DIAGNOSTIC_VIEWS["arr/seerr"]},
}
HOST_OUTPUT = Path("/opt/personal-ai-brain/arr-audit")
CONTROL_MOUNT = Path("/arr-audit")
COLLECTOR_CONFIGS = {
    "sonarr": Path("/etc/personal-ai-brain/arr-read/sonarr.json"),
    "radarr": Path("/etc/personal-ai-brain/arr-read/radarr.json"),
    "seerr": Path("/etc/personal-ai-brain/arr-read/seerr.json"),
}
MAX_SNAPSHOT = 2_000_000
DIR_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class ARRBridgeUnavailable(ValueError):
    """Fixed code only, no host path, secret, title or upstream error."""


def _deny():
    raise ARRBridgeUnavailable("arr_bridge_unavailable")


def _directory(path):
    if not isinstance(path, Path) or not path.is_absolute():
        _deny()
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            new_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _source_json(raw):
    def unique(pairs):
        result = {}
        for key, val in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = val
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _name(resource_id, view):
    if (type(resource_id) is not str or resource_id not in ARR_FILES
            or type(view) is not str or view not in ARR_FILES[resource_id]):
        _deny()
    return ARR_FILES[resource_id][view]


def _read_file(path, *, max_bytes, expected_uid, private, mode=None):
    if mode is None:
        mode = 0o600 if private else 0o640
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, READ_FLAGS, dir_fd=parent)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                    or meta.st_uid != expected_uid
                    or not 0 < meta.st_size <= max_bytes
                    or stat.S_IMODE(meta.st_mode) != mode):
                _deny()
            raw = os.read(fd, max_bytes + 1)
            if not 0 < len(raw) <= max_bytes:
                _deny()
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def read_mounted_arr(resource_id, view, *, directory=CONTROL_MOUNT,
                     expected_owner=0, now=None):
    """Return validated sanitized source only from a fixed read-only mount.

    No API key, upstream URL or caller-provided filename is accepted. The
    optional directory/UID overrides exist for deterministic offline tests
    and are NOT forwarded by Control from untrusted request arguments.
    """
    filename = _name(resource_id, view)
    try:
        fd = _directory(directory)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != expected_owner
                    or stat.S_IMODE(meta.st_mode) != 0o750
                    or meta.st_gid not in (os.getegid(), *os.getgroups())):
                _deny()
        finally:
            os.close(fd)
        source = _source_json(_read_file(directory / filename,
                                       max_bytes=MAX_SNAPSHOT,
                                       expected_uid=expected_owner, private=False))
        _arr_snapshot(source, now or datetime.now(timezone.utc), view=view)
        if source["resource_id"] != resource_id:
            _deny()
        return source
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        _deny()


def publish_arr(provider, resource_id, view, *, directory=HOST_OUTPUT,
                expected_owner=0, reader_gid=None, now=None):
    """Atomically publish exactly one allowlisted sanitized view.

    Only the trusted host collector may supply the injected ARRReadProvider.
    A schema-checked, size-bounded snapshot is published owner:reader-group
    mode 0640 in a root-owned, group-traversable, non-writable 0750 directory.
    No script/URL/credential crosses into the Control worker.
    """
    filename = _name(resource_id, view)
    if not callable(provider) or type(expected_owner) is not int or expected_owner < 0:
        _deny()
    if type(reader_gid) is not int or reader_gid < 0:
        _deny()
    fd = None
    temporary = None
    try:
        source = provider(view)
        if type(source) is not dict or source.get("resource_id") != resource_id:
            _deny()
        _arr_snapshot(source, now or datetime.now(timezone.utc), view=view)
        raw = (json.dumps(source, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
        if not 0 < len(raw) <= MAX_SNAPSHOT:
            _deny()
        fd = _directory(directory)
        meta = os.fstat(fd)
        if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != expected_owner
                or meta.st_gid != reader_gid or stat.S_IMODE(meta.st_mode) != 0o750):
            _deny()
        try:
            previous = os.stat(filename, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            previous = None
        if previous is not None and (
                not stat.S_ISREG(previous.st_mode) or previous.st_nlink != 1
                or previous.st_uid != expected_owner or previous.st_gid != reader_gid
                or stat.S_IMODE(previous.st_mode) != 0o640):
            _deny()
        temporary = ".arr-" + uuid.uuid4().hex + ".tmp"
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                      | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.fchown(out, expected_owner, reader_gid)
            os.fchmod(out, 0o640)
            with os.fdopen(out, "wb") as stream:
                out = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if out is not None:
                os.close(out)
        os.replace(temporary, filename, src_dir_fd=fd, dst_dir_fd=fd)
        temporary = None
        os.fsync(fd)
        return {"status": "published", "resource_id": resource_id, "view": view,
                "collected_at": source["collected_at"]}
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        _deny()
    finally:
        if fd is not None:
            if temporary is not None:
                os.unlink(temporary, dir_fd=fd)
            os.close(fd)


def _trusted_config(service, *, media_diagnostics=False):
    """Fixed service selector only from the operator-owned systemd unit."""
    if type(service) is not str or service not in COLLECTOR_CONFIGS:
        _deny()
    try:
        spec = _source_json(_read_file(COLLECTOR_CONFIGS[service], max_bytes=2048,
                                      expected_uid=0, private=True))
        credential_dir = os.environ.get("CREDENTIALS_DIRECTORY") if media_diagnostics else None
        expected_fields = {"service", "origin", "reader_gid"}
        if not credential_dir:
            expected_fields.add("api_key_file")
        if (type(spec) is not dict or set(spec) !=
                expected_fields
                or spec["service"] != service
                or type(spec["origin"]) is not str
                or type(spec["reader_gid"]) is not int
                or spec["reader_gid"] <= 0):
            _deny()
        if credential_dir:
            key = _systemd_api_key(service, Path(credential_dir))
        else:
            key_path = Path(spec["api_key_file"])
            key = _read_file(key_path, max_bytes=1024, expected_uid=0, private=True).decode("ascii").strip()
        provider = ARRReadProvider(spec["service"], spec["origin"],
                                   key, media_diagnostics=media_diagnostics)
        return spec["service"], spec["reader_gid"], provider
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, KeyError, ElementTree.ParseError):
        _deny()


def _systemd_api_key(service, directory):
    """Read one ephemeral copy of the application's existing settings."""
    raw = _read_file(directory / "arr-source-config", max_bytes=1_048_576,
                     expected_uid=0, private=True, mode=0o400)
    if service == "seerr":
        return _source_json(raw)["main"]["apiKey"]
    if service == "radarr" and b"<!DOCTYPE" not in raw.upper():
        return ElementTree.fromstring(raw.decode("utf-8")).findtext("ApiKey")
    _deny()


def publish_arr_views(service, provider, *, reader_gid, sonarr_diagnostics=False):
    """Keep the working inventory fresh even when a diagnostic endpoint fails."""
    if sonarr_diagnostics and service != "sonarr":
        _deny()
    resource = "arr/" + service
    snapshots = {}
    for view in ("items", "profiles"):
        source = provider(view)
        _arr_snapshot(source, datetime.now(timezone.utc), view=view)
        snapshots[view] = source
    for view in snapshots:
        publish_arr(snapshots.__getitem__, resource, view, reader_gid=reader_gid)
    unavailable = []
    if sonarr_diagnostics:
        for view in sorted(SONARR_DIAGNOSTIC_VIEWS):
            try:
                publish_arr(provider, resource, view, reader_gid=reader_gid)
            except ARRBridgeUnavailable:
                unavailable.append(view)
    return unavailable


def publish_request_status_views(service, provider, *, reader_gid):
    """Publish metadata-only views without collecting the title-level inventory."""
    resource = "arr/" + service
    if resource not in REQUEST_STATUS_VIEWS:
        _deny()
    if service == "seerr":
        captured = provider("requests")
        snapshots = {"requests": captured, "request": {**captured, "view": "request"}}
    else:
        snapshots = {view: provider(view) for view in sorted(REQUEST_STATUS_VIEWS[resource])}
    for view, source in snapshots.items():
        _arr_snapshot(source, datetime.now(timezone.utc), view=view)
    for view in snapshots:
        publish_arr(snapshots.__getitem__, resource, view, reader_gid=reader_gid)


def publish_media_diagnostic_views(service, provider, *, reader_gid):
    resource = "arr/" + service
    if resource not in MEDIA_DIAGNOSTIC_VIEWS:
        _deny()
    unavailable = []
    if service == "seerr":
        try:
            publish_request_status_views(service, provider, reader_gid=reader_gid)
        except (ARRBridgeUnavailable, ValueError, OSError):
            unavailable.extend(("requests", "request"))
        views = ("health",)
    else:
        views = sorted(MEDIA_DIAGNOSTIC_VIEWS[resource])
    for view in views:
        try:
            publish_arr(provider, resource, view, reader_gid=reader_gid)
        except ARRBridgeUnavailable:
            unavailable.append(view)
    return unavailable


def main(argv=None):
    """Operator-owned one-shot per service, scheduled outside the Control app."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True, choices=("sonarr", "radarr", "seerr"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sonarr-diagnostics", action="store_true")
    mode.add_argument("--request-status", action="store_true")
    mode.add_argument("--media-diagnostics", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("arr_bridge_unavailable")
        return 1
    try:
        service, gid, provider = _trusted_config(args.service, media_diagnostics=args.media_diagnostics)
        if args.media_diagnostics:
            unavailable = publish_media_diagnostic_views(service, provider, reader_gid=gid)
        elif args.request_status:
            publish_request_status_views(service, provider, reader_gid=gid)
            unavailable = []
        elif service == "seerr":
            _deny()
        else:
            unavailable = publish_arr_views(service, provider, reader_gid=gid, sonarr_diagnostics=args.sonarr_diagnostics)
    except (ARRBridgeUnavailable, OSError, ValueError, TypeError):
        print("arr_bridge_unavailable")
        return 1
    if unavailable:
        prefix = "arr_diagnostics_unavailable" if args.media_diagnostics else "sonarr_diagnostics_unavailable"
        print(prefix + " " + ",".join(unavailable))
        if args.media_diagnostics and len(unavailable) == len(MEDIA_DIAGNOSTIC_VIEWS["arr/" + service]):
            return 1
    print("arr_snapshots_published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
