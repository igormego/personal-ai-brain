"""Local Unix socket boundary for the existing approved effect broker.

SOURCE-ONLY: no executable host service, grants, keys or socket mount are
installed. An operator constructs the server with a pinned peer UID and a
trusted ApprovedEffectBroker. Only that server handles approval verification
and adapter calls; Control holds no signing or verification key.

No arbitrary subprocess, URL, file path or Docker/SSH action is exposed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import struct

from .approved_effect import (
    EffectDenied, EffectUncertain, validate_arguments,
)

MAX_REQUEST = 8192
MAX_RESPONSE = 4096
TIMEOUT_SECONDS = 5
SCHEMA = "0.1"


def _deny():
    raise EffectDenied("effect_denied")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _deny()
        result[key] = value
    return result


def _encode(value, maximum):
    raw = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if len(raw) > maximum:
        _deny()
    return raw


def _read_line(peer, maximum):
    """Read only one bounded newline-delimited JSON message; no streaming."""
    data = bytearray()
    while len(data) <= maximum:
        fragment = peer.recv(min(1024, maximum + 1 - len(data)))
        if not fragment:
            break
        data.extend(fragment)
        if b"\n" in fragment:
            break
    if not data or len(data) > maximum or not data.endswith(b"\n"):
        _deny()
    try:
        return json.loads(data[:-1], object_pairs_hook=_unique,
                          parse_constant=lambda _: _deny())
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _deny()


def _request(arguments, request_id):
    validate_arguments(arguments)
    # Trusted caller already uses canonical issue UUID; server checks it against
    # the approval receipt and the exact transport request.
    if type(request_id) is not str or request_id != arguments["approval"]["request_id"]:
        _deny()
    return {
        "schema_version": SCHEMA, "request_id": request_id,
        "operation": "effect.execute", "arguments": arguments,
    }


class HostEffectClient:
    """Injected into Control registry ONLY after an independent operator gate.

    The socket path is an operator setting, NEVER supplied by model/Issue data.
    The client has no approval key, Docker access or host command execution.
    """
    def __init__(self, path: Path):
        if not isinstance(path, Path) or not path.is_absolute():
            _deny()
        self._path = path

    def execute(self, arguments: dict, *, request_id: str) -> dict:
        payload = _encode(_request(arguments, request_id), MAX_REQUEST)
        connected = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(TIMEOUT_SECONDS)
                conn.connect(str(self._path))
                connected = True
                conn.sendall(payload)
                result = _read_line(conn, MAX_RESPONSE)
        except EffectDenied:
            if connected:
                raise EffectUncertain("effect_outcome_uncertain") from None
            raise
        except (OSError, TimeoutError):
            if connected:
                raise EffectUncertain("effect_outcome_uncertain") from None
            _deny()
        if (type(result) is not dict or set(result) != {"schema_version", "status", "result"}
                or result["schema_version"] != SCHEMA):
            raise EffectUncertain("effect_outcome_uncertain")
        if result["status"] == "denied" and result["result"] is None:
            _deny()
        if result["status"] == "uncertain" and result["result"] is None:
            raise EffectUncertain("effect_outcome_uncertain")
        if result["status"] != "completed" or type(result["result"]) is not dict:
            raise EffectUncertain("effect_outcome_uncertain")
        receipt = result["result"]
        # Refuse success claims not bound to this exact request and approved plan.
        if (set(receipt) != {
            "schema_version", "resource_id", "operation", "target_id",
            "policy_version", "plan_hash", "expected", "observed",
            "effect_performed", "verified_by",
        } or receipt["schema_version"] != SCHEMA
            or receipt["resource_id"] != arguments["plan"]["resource_id"]
            or receipt["operation"] != arguments["plan"]["operation"]
            or receipt["target_id"] != arguments["plan"]["target_id"]
            or receipt["plan_hash"] != arguments["plan"]["plan_hash"]
            or receipt["policy_version"] != arguments["plan"]["policy_version"]
            or receipt["expected"] != arguments["plan"]["expected"]
            or receipt["observed"] != arguments["plan"]["desired"]
            or receipt["effect_performed"] is not True
            or receipt["verified_by"] != "trusted_adapter_readback"):
            raise EffectUncertain("effect_outcome_uncertain")
        return receipt


class HostEffectSocketServer:
    """Single-process host-side socket, with Linux kernel peer-UID binding.

    A trusted operator installs and supervises the server, pins allowed_uid,
    owns the socket directory and constructs the already reviewed broker with
    no untrusted config or model parameters. The listener itself has no root
    features, no shell, no Docker socket and no network listener.
    """
    def __init__(self, path: Path, *, allowed_uid: int, broker, socket_mode: int = 0o600):
        if (not isinstance(path, Path) or not path.is_absolute()
                or type(allowed_uid) is not int or allowed_uid < 0
                or type(socket_mode) is not int or socket_mode not in (0o600, 0o660)
                or not callable(getattr(broker, "execute", None))):
            _deny()
        self._path = path
        self._uid = allowed_uid
        self._broker = broker
        self._mode = socket_mode
        self._listener = None
        self._inode = None

    def __enter__(self):
        # No insecure parent directory, pre-existing socket or symlink reuse.
        parent = self._path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o022
                or (self._mode == 0o660 and
                    (not parent.st_mode & stat.S_ISGID
                     or not parent.st_mode & stat.S_IXGRP
                     or parent.st_gid not in (os.getegid(), *os.getgroups())))
                or self._path.exists()
                or self._path.is_symlink()):
            _deny()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._path))
            os.chmod(self._path, self._mode)
            if self._mode == 0o660 and self._path.lstat().st_gid != parent.st_gid:
                _deny()
            listener.listen(4)
            self._inode = self._path.lstat().st_ino
            self._listener = listener
            return self
        except BaseException:
            listener.close()
            # Never remove a path that existed before us.
            if self._inode is not None:
                self._cleanup()
            raise

    def _cleanup(self):
        try:
            info = self._path.lstat()
            if (info.st_ino == self._inode and stat.S_ISSOCK(info.st_mode)
                    and info.st_uid == os.geteuid()):
                self._path.unlink()
        except FileNotFoundError:
            pass

    def __exit__(self, *_):
        if self._listener is not None:
            self._listener.close()
        self._cleanup()
        self._listener = None

    def serve_one(self):
        """Handle one client; operator service loop/supervisor is separate."""
        if self._listener is None:
            _deny()
        conn, _ = self._listener.accept()
        with conn:
            conn.settimeout(TIMEOUT_SECONDS)
            try:
                credentials = conn.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _pid, uid, _gid = struct.unpack("3i", credentials)
                if uid != self._uid:
                    _deny()
                request = _read_line(conn, MAX_REQUEST)
                if (type(request) is not dict or set(request) != {
                    "schema_version", "request_id", "operation", "arguments",
                } or request["schema_version"] != SCHEMA
                    or request["operation"] != "effect.execute"):
                    _deny()
                arguments = request["arguments"]
                _request(arguments, request["request_id"])
                result = self._broker.execute(
                    arguments, request_id=request["request_id"])
                response = {"schema_version": SCHEMA,
                            "status": "completed", "result": result}
            except EffectDenied:
                response = {"schema_version": SCHEMA,
                            "status": "denied", "result": None}
            except (EffectUncertain, OSError, TimeoutError):
                response = {"schema_version": SCHEMA,
                            "status": "uncertain", "result": None}
            except Exception:
                # A trusted adapter could have already written. Fail closed;
                # no arbitrary traceback, secrets or retry advice to Control.
                response = {"schema_version": SCHEMA,
                            "status": "uncertain", "result": None}
            try:
                conn.sendall(_encode(response, MAX_RESPONSE))
            except OSError:
                pass  # The caller treats an unknown reply as uncertain.
