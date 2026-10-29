"""Fixed, read-only Ubuntu inventory snapshot; never gives an LLM Docker access.

A trusted operator schedules the host-side collector, outside the control
container. The authenticated control worker may ONLY read its sanitized, private
snapshot from an operator-mounted, read-only location. No user-supplied paths,
commands, targets or API credentials are accepted.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

SCHEMA = "0.1"
MAX_BYTES = 4096
MAX_AGE = timedelta(minutes=30)
MAX_FUTURE = timedelta(minutes=2)
DEFAULT_HOST_PATH = Path("/var/lib/personal-ai-brain/host-audit/snapshot.json")
CONTROL_NAME = "personal-ai-brain-control-control-1"
DOCKER_BIN = "/usr/bin/docker"

# Go templates are fixed constants; never interpolate a user or model value.
_PS_FORMAT = "{{.Status}}"
_INSPECT_FORMAT = (
    "{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.Privileged}}|"
    "{{.Image}}|"
    "{{range .Mounts}}{{if eq .Destination \"/var/run/docker.sock\"}}socket{{end}}{{end}}|"
    "{{len .HostConfig.PortBindings}}|"
    "{{range .HostConfig.SecurityOpt}}{{if eq . \"no-new-privileges:true\"}}yes{{end}}{{end}}"
)


class AuditUnavailable(ValueError):
    """Fixed failure code only; never expose file, host or Docker errors."""


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def _utc(raw):
    if not isinstance(raw, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw
    ):
        raise ValueError("timestamp")
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _integer(value, upper, *, allow_none=False):
    return (value is None and allow_none) or (
        type(value) is int and 0 <= value <= upper
    )


def validate_snapshot(value, *, now=None):
    """Reject arbitrary keys/strings and stale or implausible host facts."""
    try:
        if type(value) is not dict or set(value) != {
            "schema_version", "collected_at", "cpu_logical", "memory_mib",
            "disk_free_gib", "docker", "control",
        } or value["schema_version"] != SCHEMA:
            raise ValueError("schema")
        at = _utc(value["collected_at"])
        age = (now or datetime.now(timezone.utc)) - at
        if not -MAX_FUTURE <= age <= MAX_AGE:
            raise ValueError("stale")
        if not _integer(value["cpu_logical"], 1024) or value["cpu_logical"] < 1:
            raise ValueError("cpu")
        mem = value["memory_mib"]
        if (type(mem) is not dict or set(mem) != {"total", "available"}
                or not _integer(mem["total"], 2_000_000) or mem["total"] < 1
                or not _integer(mem["available"], mem["total"])):
            raise ValueError("memory")
        disk = value["disk_free_gib"]
        if (type(disk) is not dict or set(disk) != {"root", "data"}
                or not _integer(disk["root"], 10_000_000)
                or not _integer(disk["data"], 10_000_000, allow_none=True)):
            raise ValueError("disk")
        docker = value["docker"]
        if (type(docker) is not dict
                or set(docker) != {"status", "running", "unhealthy"}
                or docker["status"] not in ("available", "unavailable")):
            raise ValueError("docker")
        if docker["status"] == "available":
            if (not _integer(docker["running"], 4096)
                    or not _integer(docker["unhealthy"], docker["running"])):
                raise ValueError("docker")
        elif docker["running"] is not None or docker["unhealthy"] is not None:
            raise ValueError("docker")
        ctl = value["control"]
        fields = {"status", "image_id", "read_only_rootfs", "privileged",
                  "no_new_privileges", "docker_socket", "published_ports"}
        if type(ctl) is not dict or set(ctl) != fields or ctl["status"] not in (
            "available", "unavailable"
        ):
            raise ValueError("control")
        if ctl["status"] == "available":
            if (not isinstance(ctl["image_id"], str)
                    or not re.fullmatch("sha256:[0-9a-f]{64}", ctl["image_id"])
                    or any(type(ctl[k]) is not bool for k in (
                        "read_only_rootfs", "privileged", "no_new_privileges", "docker_socket"
                    ))
                    or not _integer(ctl["published_ports"], 64)):
                raise ValueError("control")
        elif any(ctl[key] is not None for key in fields - {"status"}):
            raise ValueError("control")
        return {
            **value, "freshness": "current",
            "source": "host_collector_snapshot_not_live_probe",
        }
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise AuditUnavailable("snapshot_invalid_or_stale") from None


def read_snapshot(path: Path, *, now=None):
    """Accept only an owned, non-symlink, small regular file. No raw output."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES
                or info.st_size <= 0 or info.st_mode & 0o022
                or info.st_uid not in (0, os.geteuid())):
            raise ValueError("snapshot_permissions")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("snapshot_size")
        parsed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant"))
        )
        return validate_snapshot(parsed, now=now)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise AuditUnavailable("snapshot_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _probe_docker(arguments):
    """Operator-side fixed, bounded read. Docker socket NEVER in PAB worker."""
    try:
        output = subprocess.run(
            [DOCKER_BIN, "--host", "unix:///var/run/docker.sock", *arguments],
            capture_output=True, text=True,
            timeout=5, check=False,
        )
        if output.returncode != 0 or len(output.stdout) > 16384:
            return None
        return output.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None


def _free_gib(path):
    st = os.statvfs(path)
    return int(st.f_bavail * st.f_frsize // (1024 ** 3))


def collect_snapshot(*, now=None, probe=None):
    """No caller-supplied Docker command; only the host operator calls this."""
    probe = _probe_docker if probe is None else probe
    try:
        cpu = os.cpu_count()
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, separator, rest = line.partition(":")
            if separator and key in ("MemTotal", "MemAvailable"):
                match = re.fullmatch(r"\s*(\d+) kB\s*", rest)
                if match:
                    values[key] = int(match.group(1)) // 1024
        if not cpu or not all(k in values for k in ("MemTotal", "MemAvailable")):
            raise ValueError("missing_metrics")
        root_free = _free_gib("/")
        data_free = _free_gib("/data") if os.path.ismount("/data") else None
        ps = probe(["ps", "--format", _PS_FORMAT])
        docker = {"status": "unavailable", "running": None, "unhealthy": None}
        control = {
            "status": "unavailable", "image_id": None, "read_only_rootfs": None,
            "privileged": None, "no_new_privileges": None,
            "docker_socket": None, "published_ports": None,
        }
        if ps is not None:
            states = [] if not ps else ps.splitlines()
            if len(states) <= 4096 and all(
                state.startswith("Up ") and len(state) <= 160 for state in states
            ):
                docker = {
                    "status": "available", "running": len(states),
                    "unhealthy": sum("(unhealthy)" in state for state in states),
                }
                details = probe(["inspect", "--format", _INSPECT_FORMAT, CONTROL_NAME])
                if details is not None:
                    parts = details.split("|")
                    if (len(parts) == 6
                            and parts[0] in ("true", "false")
                            and parts[1] in ("true", "false")
                            and re.fullmatch("sha256:[0-9a-f]{64}", parts[2])
                            and parts[3] in ("", "socket")
                            and parts[4].isdigit() and 0 <= int(parts[4]) <= 64
                            and parts[5] in ("", "yes")):
                        control = {
                            "status": "available", "image_id": parts[2],
                            "read_only_rootfs": parts[0] == "true",
                            "privileged": parts[1] == "true",
                            "docker_socket": parts[3] == "socket",
                            "published_ports": int(parts[4]),
                            "no_new_privileges": parts[5] == "yes",
                        }
        at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        packet = {
            "schema_version": SCHEMA,
            "collected_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cpu_logical": cpu,
            "memory_mib": {"total": values["MemTotal"], "available": values["MemAvailable"]},
            "disk_free_gib": {"root": root_free, "data": data_free},
            "docker": docker, "control": control,
        }
        validate_snapshot(packet, now=at)
        return packet
    except (OSError, ValueError, TypeError, OverflowError):
        raise AuditUnavailable("collection_unavailable") from None


def publish_snapshot(path: Path, packet):
    """Atomic private host file; parent dir must pre-exist and be operator-owned."""
    pending = None
    try:
        directory = path.parent
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or path.is_symlink()):
            raise ValueError("unsafe_directory")
        validate_snapshot(packet)
        raw = (json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise ValueError("large")
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".host-audit-",
                                         delete=False) as temp:
            pending = Path(temp.name)
            os.fchmod(temp.fileno(), 0o600)
            temp.write(raw)
            temp.flush()
            os.fsync(temp.fileno())
        pending.replace(path)
        pending = None
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, RecursionError):
        raise AuditUnavailable("publish_unavailable") from None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Host-only read-only audit collector")
    parser.add_argument("action", choices=("collect",))
    parser.add_argument("--output", type=Path, default=DEFAULT_HOST_PATH)
    args = parser.parse_args()
    try:
        publish_snapshot(args.output, collect_snapshot())
    except AuditUnavailable:
        print("audit_unavailable")
        return 1
    print("audit_collected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
