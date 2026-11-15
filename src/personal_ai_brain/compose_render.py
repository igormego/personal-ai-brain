"""Isolated, read-only Compose render for fixture-1. No deployment authority.

The trusted caller must pin/protect the root descriptor and both tool binaries,
and supply their independently approved hashes, Compose version and layer order.
This module does not open a Docker socket or invoke an operator action.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows cannot provide the Linux isolation boundary.
    fcntl = None
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time

import yaml

from .deployment_preflight import deny, open_beneath, parse_manifest, path_parts, preflight, read_file, sha

MAX_TOOL = 128 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
TOP = {"services", "secrets"}
SERVICE = {"image", "volumes", "secrets", "read_only", "cap_drop", "environment"}


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key, value in node.value:
        k = loader.construct_object(key)
        if type(k) is not str or k in result:
            deny()
        result[k] = loader.construct_object(value)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _layer(data):
    """Reject unsupported features before Compose can load any references."""
    try:
        text = data.decode("utf-8")
        if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken, yaml.tokens.TagToken)) for t in yaml.scan(text)):
            deny()
        doc = yaml.load(text, Loader=UniqueLoader)
        if type(doc) is not dict or not set(doc) <= TOP:
            deny()
        services = doc.get("services", {})
        if type(services) is not dict or any(type(v) is not dict or not set(v) <= SERVICE for v in services.values()):
            deny()
        for v in (doc.get("secrets") or {}).values():
            if type(v) is not dict or set(v) != {"file"}:
                deny()
    except (ValueError, TypeError, UnicodeError, yaml.YAMLError, RecursionError, AttributeError):
        deny()


def _sealed(data):
    try:
        if fcntl is None:
            deny()
        fd = os.memfd_create("pab-compose-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    deny()
                view = view[written:]
            os.lseek(fd, 0, os.SEEK_SET)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            return fd
        except BaseException:
            os.close(fd)
            raise
    except (AttributeError, OSError):
        deny()


def _tool(fd, expected, uid):
    """Snapshot approved non-writable binary from a caller-pinned descriptor."""
    sha(expected)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, uid)
                or before.st_mode & 0o022 or not 0 < before.st_size <= MAX_TOOL):
            deny()
        chunks, offset = [], 0
        while offset < before.st_size:
            data = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
            if not data:
                deny()
            chunks.append(data); offset += len(data)
        data = b"".join(chunks)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or hashlib.sha256(data).hexdigest() != expected):
            deny()
        return _sealed(data)
    except (OSError, ValueError):
        deny()


def _identity(root_fd, path, uid):
    fd = open_beneath(root_fd, path_parts(path, absolute=True), uid)
    try:
        s = os.fstat(fd)
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                s.st_uid, s.st_gid, s.st_mode, s.st_nlink)
    except OSError:
        deny()
    finally:
        os.close(fd)


def _run(args, fds, executable, timeout):
    """Bound output and wall clock, kill the process group on any ambiguity."""
    p = None
    success = False
    sel = None
    try:
        p = subprocess.Popen(args, executable=f"/proc/self/fd/{executable}", pass_fds=tuple(fds),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             close_fds=True, start_new_session=True, env={})
        sel = selectors.DefaultSelector()
        for stream in (p.stdout, p.stderr):
            os.set_blocking(stream.fileno(), False); sel.register(stream, selectors.EVENT_READ)
        buffers = {p.stdout: bytearray(), p.stderr: bytearray()}
        deadline = time.monotonic() + timeout
        while sel.get_map():
            left = deadline - time.monotonic()
            if left <= 0:
                deny()
            for key, _ in sel.select(left):
                part = os.read(key.fd, 65536)
                if not part:
                    sel.unregister(key.fileobj)
                else:
                    buffers[key.fileobj].extend(part)
                    if sum(map(len, buffers.values())) > MAX_OUTPUT:
                        deny()
        if p.wait(timeout=max(0.01, deadline - time.monotonic())) != 0 or buffers[p.stderr]:
            deny()
        success = True
        return bytes(buffers[p.stdout])
    except (OSError, subprocess.SubprocessError, ValueError):
        deny()
    finally:
        if p is not None:
            if not success:
                try: os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            try: p.wait(timeout=3)
            except subprocess.SubprocessError: pass
            if p.stdout: p.stdout.close()
            if p.stderr: p.stderr.close()
        if sel is not None: sel.close()


def render_preflight(root_fd, manifest_path, *, owner_uid, expected_layer_order,
                     bwrap_fd, bwrap_sha256, compose_fd, compose_sha256,
                     compose_version, project_name, timeout=10):
    """Render sealed layer/env snapshots in a no-network/no-host-mount sandbox.

    Layer five is the candidate override; original uses the first four. A
    successful result still never authorizes deployment or proves live rollback.
    """
    if (type(owner_uid) is not int or owner_uid < 0 or type(expected_layer_order) is not tuple
            or type(compose_version) is not str or not VERSION.fullmatch(compose_version)
            or type(project_name) is not str or not PROJECT.fullmatch(project_name)
            or type(timeout) not in (int, float) or not 0 < timeout <= 30):
        deny()
    opened = []
    try:
        manifest_identity = _identity(root_fd, manifest_path, owner_uid)
        raw = read_file(root_fd, manifest_path, owner_uid)
        if _identity(root_fd, manifest_path, owner_uid) != manifest_identity:
            deny()
        m = parse_manifest(raw)
        if tuple(x["path"] for x in m["layers"]) != expected_layer_order:
            deny()
        project, envpath = m["project_directory"], m["environment"]["reference"]
        paths = [project + "/" + x["path"] for x in m["layers"]] + [envpath]
        reserved = ("/tools", "/proc", "/dev", "/tmp")
        if len(set(paths)) != len(paths) or any(p == r or p.startswith(r + "/") for p in paths for r in reserved):
            deny()
        tracked = [manifest_path, m["profile"]["reference"], *paths]
        if len(set(tracked)) != len(tracked):
            deny()
        identities = {path: _identity(root_fd, path, owner_uid) for path in tracked}
        if identities[manifest_path] != manifest_identity:
            deny()
        if hashlib.sha256(read_file(root_fd, m["profile"]["reference"], owner_uid)).hexdigest() != m["profile"]["sha256"]:
            deny()
        snapshots = []
        for item, path in zip(m["layers"] + [m["environment"]], paths):
            data = read_file(root_fd, path, owner_uid)
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                deny()
            snapshots.append(data)
        for data in snapshots[:5]: _layer(data)
        # Compose control variables can alter project loading and are not part of
        # this narrow contract. Never print or return protected env content.
        for line in snapshots[-1].decode("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, sep, _ = line.partition("=")
                if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key.startswith(("COMPOSE_", "DOCKER_")):
                    deny()
        bw = _tool(bwrap_fd, bwrap_sha256, owner_uid); opened.append(bw)
        compose = _tool(compose_fd, compose_sha256, owner_uid); opened.append(compose)
        inputs = []
        for data in snapshots:
            fd = _sealed(data); opened.append(fd); inputs.append(fd)
        directories = {"/tools"}
        for path in paths:
            parts = path.split("/")[1:-1]
            for i in range(1, len(parts) + 1): directories.add("/" + "/".join(parts[:i]))
        if any(p in ("/proc", "/dev", "/tmp") or p.startswith(("/proc/", "/dev/", "/tmp/", "/tools/")) for p in directories):
            deny()
        base = ["bwrap", "--unshare-all", "--unshare-user", "--die-with-parent", "--new-session", "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL", "--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for directory in sorted(directories, key=lambda x: (x.count("/"), x)):
            base += ["--dir", directory]
        base += ["--perms", "0555", "--ro-bind-data", str(compose), "/tools/compose"]
        for fd, path in zip(inputs, paths): base += ["--perms", "0444", "--ro-bind-data", str(fd), path]
        base += ["--setenv", "HOME", "/tmp", "--setenv", "DOCKER_CONFIG", "/tmp/empty", "--setenv", "DOCKER_HOST", "unix:///nonexistent/docker.sock", "--chdir", project, "--", "/tools/compose"]
        for fd in [compose, *inputs]: os.lseek(fd, 0, os.SEEK_SET)
        version = _run(base + ["version", "--short"], opened, bw, timeout).decode("utf-8").strip()
        if version != compose_version: deny()
        configs = []
        for count in (4, 5):
            for fd in [compose, *inputs]: os.lseek(fd, 0, os.SEEK_SET)
            args = base + ["-p", project_name, "--project-directory", project, "--env-file", envpath]
            for path in paths[:count]: args += ["-f", path]
            output = _run(args + ["config", "--format", "json"], opened, bw, timeout)
            configs.append(json.loads(output, object_pairs_hook=_unique, parse_constant=lambda _: deny()))
        if read_file(root_fd, manifest_path, owner_uid) != raw:
            deny()
        result = preflight(root_fd, manifest_path, owner_uid=owner_uid,
                           expected_layer_order=expected_layer_order,
                           original=configs[0], candidate=configs[1])
        if any(_identity(root_fd, path, owner_uid) != identity for path, identity in identities.items()):
            deny()
        result["normalization_provenance"] = "sealed_inputs_sandboxed"
        result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        result["compose_sha256"] = compose_sha256
        result["compose_version"] = compose_version
        result["bwrap_sha256"] = bwrap_sha256
        return result
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, yaml.YAMLError):
        deny()
    finally:
        for fd in opened:
            os.close(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: deny()
        result[key] = value
    return result
