"""Fixed, read-only physical storage topology for the existing Control broker.

The trusted host collector runs separately from the unprivileged Control worker.
No model-supplied device, path, mountpoint, command or filesystem action is used.
Identifiers/labels/serials and raw lsblk output never enter the published snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

RESOURCE_ID = "storage/topology"
DEFAULT_HOST_PATH = Path("/var/lib/personal-ai-brain/host-audit/storage.json")
CONTROL_SNAPSHOT_PATH = Path("/host-audit/storage.json")
LSBLK = "/usr/bin/lsblk"
GIB = 1024 ** 3
MAX_BYTES = 8192
MAX_AGE = timedelta(minutes=30)
FSTYPES = {"ext4", "ext3", "xfs", "btrfs", "ntfs", "vfat", "exfat",
           "swap", "zfs", "lvm_member", "crypto", "other", "unknown"}
BUSES = {"sata", "sas", "usb", "nvme", "scsi", "virtio", "other", "unknown"}
LAYER_TYPES = {"part", "lvm", "crypt", "raid", "md"}
ROLES = {"root", "data"}


class StorageUnavailable(ValueError):
    """Fixed error code, never echo source text or host paths."""


def _deny():
    raise StorageUnavailable("storage_unavailable")


def _pairs_unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            _deny()
        data[key] = value
    return data


def _size(value):
    if type(value) is not int or not 0 <= value <= 10**15:
        _deny()
    return value


def _gib(value):
    return _size(value) // GIB


def _fs(value):
    if value is None:
        return "unknown"
    if type(value) is not str:
        _deny()
    name = value.lower()
    if name == "lvm2_member":
        name = "lvm_member"
    elif name.startswith("crypto_"):
        name = "crypto"
    return name if name in FSTYPES else "other"


def _bus(value):
    if value is None:
        return "unknown"
    if type(value) is not str or len(value) > 24:
        _deny()
    name = value.lower()
    return name if name in BUSES else "other"


def _roles(node, depth=0):
    if depth > 8 or type(node) is not dict:
        _deny()
    mounts = node.get("mountpoints")
    if not isinstance(mounts, list) or len(mounts) > 16 or any(
            p is not None and (type(p) is not str or len(p) > 4096) for p in mounts):
        _deny()
    roles = set()
    if "/" in mounts:
        roles.add("root")
    if "/data" in mounts:
        roles.add("data")
    children = node.get("children", [])
    if type(children) is not list or len(children) > 64:
        _deny()
    for child in children:
        roles.update(_roles(child, depth + 1))
    return roles


def _layers(node, depth=0):
    if depth > 8 or type(node) is not dict:
        _deny()
    kind = node.get("type")
    if type(kind) is not str:
        _deny()
    result = []
    if kind in LAYER_TYPES:
        result.append({"kind": kind, "size_gib": _gib(node.get("size")),
                       "filesystem": _fs(node.get("fstype")),
                       "roles": sorted(_roles(node))})
    children = node.get("children", [])
    if type(children) is not list or len(children) > 64:
        _deny()
    for child in children:
        result.extend(_layers(child, depth + 1))
    if len(result) > 64:
        _deny()
    return result


def _free_gib(path):
    info = os.statvfs(path)
    return max(0, info.f_bavail * info.f_frsize // GIB)


def collect_snapshot(*, now=None, runner=None, free_gib=None, data_mounted=None):
    """Inspect all physical disks; report layout without raw device identifiers.

    Disk indices are ephemeral display ordinals, NEVER safe execution targets.
    An unmounted filesystem's used/free space and contents are unknown.
    """
    def default_runner(argv):
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           check=False, timeout=8)
        if p.returncode or len(p.stdout) > 32768:
            _deny()
        return p.stdout

    try:
        raw = (runner or default_runner)(
            [LSBLK, "-J", "-b", "-o", "NAME,TYPE,SIZE,TRAN,FSTYPE,MOUNTPOINTS"]
        )
        if type(raw) is not bytes or len(raw) > 32768:
            _deny()
        source = json.loads(raw, object_pairs_hook=_pairs_unique)
        if type(source) is not dict or set(source) != {"blockdevices"}:
            _deny()
        physical = source["blockdevices"]
        if type(physical) is not list or len(physical) > 64:
            _deny()
        physical = [item for item in physical
                    if type(item) is dict and item.get("type") == "disk"]
        if not 1 <= len(physical) <= 16:
            _deny()
        devices = []
        for index, disk in enumerate(physical, start=1):
            disk_size = _size(disk.get("size"))
            if disk_size <= 0:
                _deny()
            children = disk.get("children", [])
            if type(children) is not list or len(children) > 64:
                _deny()
            parts = [child for child in children if type(child) is dict
                     and child.get("type") == "part"]
            allocated = sum(_size(p.get("size")) for p in parts)
            if allocated > disk_size:
                _deny()
            roles = _roles(disk)
            devices.append({
                "disk_index": index,
                "size_gib": disk_size // GIB,
                "transport": _bus(disk.get("tran")),
                "roles": sorted(roles),
                "unpartitioned_gib_estimate": max(0, disk_size - allocated) // GIB
                    if parts else None,
                "volumes": _layers(disk),
            })
        clock = now or datetime.now(timezone.utc)
        if not isinstance(clock, datetime) or clock.tzinfo is None:
            _deny()
        clock = clock.astimezone(timezone.utc)
        mounted = os.path.ismount("/data") if data_mounted is None else data_mounted
        if type(mounted) is not bool:
            _deny()
        free = free_gib or _free_gib
        packet = {
            "schema_version": "0.1", "resource_id": RESOURCE_ID,
            "collected_at": clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_status": "ok", "devices": devices,
            "root_free_gib": free("/"),
            "data_free_gib": free("/data") if mounted else None,
            "contents_inspected": False,
        }
        validate_snapshot(packet, now=clock)
        return packet
    except (OSError, ValueError, TypeError, KeyError, RecursionError,
            OverflowError, subprocess.TimeoutExpired, UnicodeError):
        _deny()


def validate_snapshot(value, *, now=None):
    try:
        if (type(value) is not dict or set(value) != {
            "schema_version", "resource_id", "collected_at", "source_status",
            "devices", "root_free_gib", "data_free_gib", "contents_inspected",
        } or value["schema_version"] != "0.1"
            or value["resource_id"] != RESOURCE_ID
            or value["source_status"] != "ok"
            or value["contents_inspected"] is not False):
            _deny()
        raw_time = value["collected_at"]
        if type(raw_time) is not str or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw_time):
            _deny()
        at = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        age = (now or datetime.now(timezone.utc)) - at
        if not -timedelta(minutes=2) <= age <= MAX_AGE:
            _deny()
        if (type(value["root_free_gib"]) is not int or
                not 0 <= value["root_free_gib"] <= 10**9 or
                (value["data_free_gib"] is not None and
                 (type(value["data_free_gib"]) is not int or
                  not 0 <= value["data_free_gib"] <= 10**9))):
            _deny()
        disks = value["devices"]
        if type(disks) is not list or not 1 <= len(disks) <= 16:
            _deny()
        for i, disk in enumerate(disks, start=1):
            if type(disk) is not dict or set(disk) != {
                "disk_index", "size_gib", "transport", "roles",
                "unpartitioned_gib_estimate", "volumes",
            } or type(disk["disk_index"]) is not int or disk["disk_index"] != i:
                _deny()
            if (type(disk["size_gib"]) is not int or not 0 <= disk["size_gib"] <= 10**8
                    or disk["transport"] not in BUSES or type(disk["roles"]) is not list
                    or disk["roles"] != sorted(set(disk["roles"]))
                    or set(disk["roles"]) - ROLES):
                _deny()
            estimate = disk["unpartitioned_gib_estimate"]
            if estimate is not None and (type(estimate) is not int or
                                          not 0 <= estimate <= disk["size_gib"] + 1):
                _deny()
            volumes = disk["volumes"]
            if type(volumes) is not list or len(volumes) > 64:
                _deny()
            for layer in volumes:
                if (type(layer) is not dict or set(layer) != {
                        "kind", "size_gib", "filesystem", "roles"}
                    or layer["kind"] not in LAYER_TYPES
                    or type(layer["size_gib"]) is not int
                    or not 0 <= layer["size_gib"] <= 10**8
                    or layer["filesystem"] not in FSTYPES
                    or type(layer["roles"]) is not list
                    or layer["roles"] != sorted(set(layer["roles"]))
                    or set(layer["roles"]) - ROLES):
                    _deny()
            if set(disk["roles"]) != set().union(*(set(layer["roles"]) for layer in volumes)):
                _deny()
        if len(json.dumps(value, separators=(",", ":")).encode()) > MAX_BYTES:
            _deny()
        return {**value, "freshness": "current",
                "source": "operator_storage_snapshot_not_live_probe"}
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        _deny()


def read_snapshot(path: Path, *, now=None):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC |
                             getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES
                or info.st_mode & 0o022 or info.st_uid not in (0, os.geteuid())):
            _deny()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            _deny()
        packet = json.loads(raw, object_pairs_hook=_pairs_unique)
        return validate_snapshot(packet, now=now)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        _deny()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def publish_snapshot(path: Path, packet):
    pending = None
    try:
        validate_snapshot(packet)
        parent = path.parent
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or path.is_symlink()):
            _deny()
        raw = (json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > MAX_BYTES:
            _deny()
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".storage-audit-",
                                         delete=False) as stream:
            pending = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        pending = None
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, ValueError, TypeError, RecursionError):
        _deny()
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def main():
    """Trusted host entrypoint with fixed output, not an LLM-callable command."""
    if os.geteuid() != 0:
        _deny()
    publish_snapshot(DEFAULT_HOST_PATH, collect_snapshot())
    print("storage_collected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
