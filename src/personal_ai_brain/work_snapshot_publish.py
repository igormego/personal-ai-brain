"""Privileged, fixed-output, single-shot publication of one sanitized host snapshot.

Run ONLY from an operator-owned root systemd oneshot. The private collector
writes its own 0700/0600 source; the Work Runner must never gain access to the
collector directory. This bridge validates the exact schema and copies only
that snapshot into a root-controlled, group-readable *fixed* destination.

This module never accepts model requests, network URLs, commands or dynamic
destination paths. It does not restart services, change credentials or create
the destination directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .host_audit import AuditUnavailable, MAX_BYTES, _unique, validate_snapshot
from .work_resource_grants import HOST_SNAPSHOT

CONFIG = Path("/etc/personal-ai-brain/builder/snapshot-publisher.json")
O_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class PublicationUnavailable(ValueError):
    """Fixed errors only: never echo private paths, owners or source values."""


def _deny():
    raise PublicationUnavailable("snapshot_publication_unavailable")


def _directory(path: Path):
    """Traverse without following symlinks, including every parent component."""
    if not isinstance(path, Path) or not path.is_absolute():
        _deny()
    fd = os.open("/", O_FLAGS)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, O_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _input(source: Path, *, collector_uid: int):
    fd = _directory(source.parent)
    try:
        folder = os.fstat(fd)
        if (not stat.S_ISDIR(folder.st_mode)
                or folder.st_uid != collector_uid
                or folder.st_mode & 0o077):
            _deny()
        handle = os.open(source.name, FILE_FLAGS, dir_fd=fd)
        try:
            info = os.fstat(handle)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != collector_uid or info.st_size <= 0
                    or info.st_size > MAX_BYTES or info.st_mode & 0o077):
                _deny()
            raw = os.read(handle, MAX_BYTES + 1)
            if not 0 < len(raw) <= MAX_BYTES:
                _deny()
        finally:
            os.close(handle)
    finally:
        os.close(fd)
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    # Never trust source text because it comes from the named collector.
    # Exact schema and freshness are independently checked before publication.
    validate_snapshot(parsed)
    return (json.dumps(parsed, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def publish_copy(source: Path, *, collector_uid: int, reader_gid: int,
                 destination: Path = HOST_SNAPSHOT, expected_owner: int = 0):
    """Atomically publish a verified refresh, preserving a read-only reader.

    The target directory is owned by the publisher, group-readable/traversable
    by the Work Runner, but NEVER group-writable. The resulting file is owned
    by the publisher and group-readable (0640); the runner cannot alter it.
    Only the root-owned operator config selects the collector source and group.
    """
    try:
        if (type(collector_uid) is not int or collector_uid <= 0
                or type(reader_gid) is not int or reader_gid <= 0
                or type(expected_owner) is not int or expected_owner < 0
                or not isinstance(source, Path) or not source.is_absolute()
                or source.name != "snapshot.json"
                or not isinstance(destination, Path) or destination != HOST_SNAPSHOT
                   and expected_owner == 0):
            _deny()
        raw = _input(source, collector_uid=collector_uid)
        dest_fd = _directory(destination.parent)
        tmp = None
        try:
            folder = os.fstat(dest_fd)
            if (not stat.S_ISDIR(folder.st_mode)
                    or folder.st_uid != expected_owner
                    or folder.st_gid != reader_gid
                    or folder.st_mode & 0o027  # no group-write/other permissions
                    or not folder.st_mode & stat.S_IXGRP):
                _deny()
            try:
                existing = os.stat(destination.name, dir_fd=dest_fd,
                                   follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and (
                    not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
                    or existing.st_uid != expected_owner
                    or existing.st_gid != reader_gid
                    or existing.st_mode & 0o027):
                _deny()
            tmp = ".snapshot-" + uuid.uuid4().hex + ".tmp"
            out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                          | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dest_fd)
            try:
                if (os.fstat(out).st_uid != expected_owner
                        or os.fstat(out).st_gid != reader_gid):
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
            os.replace(tmp, destination.name, src_dir_fd=dest_fd,
                       dst_dir_fd=dest_fd)
            tmp = None
            os.fsync(dest_fd)
        finally:
            if tmp is not None:
                os.unlink(tmp, dir_fd=dest_fd)
            os.close(dest_fd)
        return {"status": "published", "sha256": hashlib.sha256(raw).hexdigest()}
    except (AuditUnavailable, OSError, ValueError, KeyError, TypeError,
            UnicodeError, RecursionError):
        raise PublicationUnavailable("snapshot_publication_unavailable") from None


def _config():
    """Root-only private declaration, never from GitHub/model or environment."""
    fd = _directory(CONFIG.parent)
    try:
        f = os.open(CONFIG.name, FILE_FLAGS, dir_fd=fd)
        try:
            meta = os.fstat(f)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                    or meta.st_uid != 0 or meta.st_mode & 0o077
                    or not 0 < meta.st_size <= 1024):
                _deny()
            raw = os.read(f, 1025)
        finally:
            os.close(f)
    finally:
        os.close(fd)
    spec = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(spec) is not dict
            or set(spec) != {"collector_source_path", "collector_uid", "reader_gid"}
            or type(spec["collector_source_path"]) is not str
            or type(spec["collector_uid"]) is not int
            or type(spec["reader_gid"]) is not int):
        _deny()
    return spec


def main():
    if os.geteuid() != 0:
        print("snapshot_publication_unavailable")
        return 1
    try:
        spec = _config()
        publish_copy(Path(spec["collector_source_path"]),
                     collector_uid=spec["collector_uid"],
                     reader_gid=spec["reader_gid"])
    except (PublicationUnavailable, OSError, ValueError, TypeError):
        print("snapshot_publication_unavailable")
        return 1
    print("snapshot_published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
