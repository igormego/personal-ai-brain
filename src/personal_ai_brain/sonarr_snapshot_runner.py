"""Linux operator runner; never imported/wired by control or exposed as a tool.

Only the isolated child reads the existing Sonarr key. The parent publishes a
validated packet after child success; collection failure preserves the old snapshot.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import re
import resource
import stat
import time
import uuid
import xml.etree.ElementTree as ET

from .assistant_media_resources import MediaResourceUnavailable, sanitize_media_resource
from .media_snapshot import MAX_SNAPSHOT_BYTES, _unique
from .sonarr_overview import SonarrOverviewCollector

HARD_TIMEOUT = 150


def _fail():
    raise MediaResourceUnavailable("media_resource_unavailable")


def _key(path):
    """Existing trusted config only; no key in argv, env, output or copied file."""
    if not isinstance(path, Path) or not path.is_absolute():
        _fail()
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o022
            or parent.st_uid not in (0, os.geteuid())):
        _fail()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022
                or not 0 < info.st_size <= 65536):
            _fail()
        raw = stream.read(65537)
    if len(raw) > 65536 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        _fail()
    root = ET.fromstring(raw)
    keys = root.findall("ApiKey")
    if (root.tag != "Config" or len(keys) != 1
            or root.findtext("Port") != "8989" or root.findtext("UrlBase") not in (None, "", "/")):
        _fail()
    key = keys[0].text
    if type(key) is not str or not re.fullmatch(r"[a-fA-F0-9]{32}", key):
        _fail()
    return key


def _decode(raw):
    if not 0 < len(raw) <= MAX_SNAPSHOT_BYTES:
        _fail()
    packet = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                        parse_constant=lambda _: _fail())
    sanitize_media_resource("sonarr/overview", packet)
    return packet


def _collect_child(config, channel):
    # Even unexpected library diagnostics cannot expose config/key/API bodies.
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            packet = SonarrOverviewCollector(_key(config))("sonarr/overview")
            raw = (json.dumps(packet, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
            _decode(raw)
            channel.send_bytes(raw)
        except BaseException:
            pass  # No exception text or partial packet crosses the process boundary.
        finally:
            channel.close()


def _bounded_collect(config, timeout):
    if type(timeout) not in (int, float) or not 0 < timeout <= HARD_TIMEOUT:
        _fail()
    context = multiprocessing.get_context("fork")  # Linux host runner only.
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_collect_child, args=(config, send))
    started = False
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        send.close()
        if not receive.poll(max(0, deadline - time.monotonic())):
            _fail()
        raw = receive.recv_bytes(MAX_SNAPSHOT_BYTES)
        process.join(max(0, deadline - time.monotonic()))
        if process.is_alive() or process.exitcode != 0 or time.monotonic() > deadline:
            _fail()
        _decode(raw)
        return raw
    finally:
        receive.close()
        send.close()
        if started:
            if process.is_alive():
                process.kill()  # SIGKILL: does not rely on cooperative socket timeout.
            process.join()
            process.close()


def _check_private(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        _fail()


def _existing_snapshot(directory_fd):
    try:
        info = os.stat("snapshot.json", dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    _check_private(info)


def _publish(directory_fd, raw):
    # A directory-fsync failure after replace is an uncertain durability outcome:
    # a complete new packet may already exist. Never retry/restore automatically.
    _decode(raw)
    _existing_snapshot(directory_fd)
    name = ".pending-" + uuid.uuid4().hex
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _existing_snapshot(directory_fd)
        os.replace(name, "snapshot.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def run(config: Path, directory: Path, *, timeout=HARD_TIMEOUT):
    """Publish into a pre-existing 0700 directory; never mkdir/chmod old files.

    The directory descriptor pins the destination. An advisory private lock
    prevents overlapping runners. A crash can leave a private .pending file;
    never automatically delete another run's evidence.
    """
    directory_fd = lock_fd = None
    try:
        if not isinstance(directory, Path) or not directory.is_absolute():
            _fail()
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _check_private(os.fstat(directory_fd), directory=True)
        lock_fd = os.open(".runner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                          0o600, dir_fd=directory_fd)
        _check_private(os.fstat(lock_fd))
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _existing_snapshot(directory_fd)
        raw = _bounded_collect(config, timeout)
        _publish(directory_fd, raw)
    except Exception:
        raise MediaResourceUnavailable("media_resource_unavailable") from None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description="Operator-only private Sonarr snapshot publisher")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        run(args.config, args.directory)
    except MediaResourceUnavailable:
        print("media_resource_unavailable")
        return 1
    print("media_snapshot_published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
