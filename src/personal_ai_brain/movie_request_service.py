"""Systemd socket-activated adapter for the existing fixed movie controller."""

import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import time

from .approved_effect import EffectDenied, EffectUncertain
from .assistant_media_plan import FixedNodeMoviePlanner, _pairs_unique
from .host_effect_socket import MAX_REQUEST, MAX_RESPONSE, _encode, _read_line
from .movie_request_socket import PLAN, REQUEST, checked_receipt, movie_request_id, movie_target

ROOT = Path("/opt/personal-ai-brain/movie-request")
APPROVAL = Path("/etc/personal-ai-brain/movie-request/approval.json")
LEDGER = Path("/var/lib/personal-ai-brain/movie-control-ledger/requests")
NODE = Path("/usr/bin/node")
APPROVAL_LIFETIME = 900
WRITE_DEADLINE = 63


def _deny():
    raise EffectDenied("effect_denied")


def _read(path, *, owner_only=False):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 0 < info.st_size <= 4096
                or (owner_only and (info.st_uid != 0 or info.st_mode & 0o037))):
            _deny()
        return json.loads(stream.read(4097), object_pairs_hook=_pairs_unique)


def verify_approval(approval, request_id, tmdb_id, now):
    if (type(approval) is not dict
            or set(approval) != {"request_id", "tmdb_id", "issued_at", "expires_at"}
            or approval["request_id"] != request_id
            or type(approval["tmdb_id"]) is not int or approval["tmdb_id"] != tmdb_id):
        _deny()
    issued, expiry = approval["issued_at"], approval["expires_at"]
    if (type(issued) is not int or type(expiry) is not int
            or not issued <= now < expiry - WRITE_DEADLINE
            or not 0 < expiry - issued <= APPROVAL_LIFETIME):
        _deny()


def reserve_movie(directory, request_id, tmdb_id):
    # Index under the existing Control requests ledger. Keep the marker even
    # after a crash, ambiguous POST or a confirmed receipt. Never reclaim it.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with_fd = os.open(directory, flags)
    try:
        info = os.fstat(with_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            _deny()
        journal_fd = os.open(request_id + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=with_fd)
        with os.fdopen(journal_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                _deny()
            raw = stream.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            _deny()
        record = json.loads(raw, object_pairs_hook=_pairs_unique)
        if (record["status"] != "processing" or record["capability"] != REQUEST
                or record["request_id"] != request_id or record["arguments"] != {"tmdb_id": tmdb_id}):
            _deny()
        try:
            fd = os.open(f".movie-{tmdb_id}.json",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=with_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_encode({"request_id": request_id, "tmdb_id": tmdb_id}, MAX_RESPONSE))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(with_fd)
        except OSError:
            raise EffectUncertain("effect_outcome_uncertain") from None
    finally:
        os.close(with_fd)


def submit(tmdb_id, env):
    try:
        output = subprocess.run(
            [str(NODE), str(ROOT / "controller" / "request-cli.mjs"), str(tmdb_id)],
            input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, timeout=WRITE_DEADLINE, check=True,
        ).stdout
        if len(output) > MAX_RESPONSE:
            raise EffectUncertain("effect_outcome_uncertain")
        return checked_receipt(json.loads(output, object_pairs_hook=_pairs_unique), tmdb_id)
    except (OSError, ValueError, subprocess.SubprocessError, RecursionError):
        raise EffectUncertain("effect_outcome_uncertain") from None


def execute(request, *, planner, get_approval, reserve, submit_movie, clock=time.time):
    if type(request) is not dict or set(request) != {"operation", "arguments", "request_id"}:
        _deny()
    arguments, request_id = request["arguments"], request["request_id"]
    if request["operation"] == PLAN and request_id is None:
        return planner(arguments)
    if request["operation"] != REQUEST:
        _deny()
    tmdb_id = movie_target(arguments)["tmdb_id"]
    movie_request_id(request_id)
    verify_approval(get_approval(), request_id, tmdb_id, clock())
    reserve(request_id, tmdb_id)
    # The existing controller rechecks availability, identity and current
    # configured standard-quality policy immediately before its single POST.
    return submit_movie(tmdb_id)


def serve(peer, allowed_uid, **operations):
    request_id = None
    try:
        peer.settimeout(5)
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", credentials)
        if uid != allowed_uid:
            _deny()
        request = _read_line(peer, MAX_REQUEST)
        if type(request) is dict and request.get("operation") == REQUEST:
            request_id = movie_request_id(request.get("request_id"))
        result = execute(request, **operations)
        response = {"status": "completed", "result": result, "request_id": request_id}
    except EffectDenied:
        response = {"status": "denied", "result": None, "request_id": request_id}
    except Exception:
        response = {"status": "uncertain", "result": None, "request_id": request_id}
    try:
        peer.sendall(_encode(response, MAX_RESPONSE))
    except OSError:
        pass   # A caller with an unknown write outcome must never retry.


def main():
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    env = _read(credentials / "movie-sources.json")
    planner = FixedNodeMoviePlanner(
        node_executable=NODE, plan_script=ROOT / "controller" / "plan-cli.mjs",
        trusted_env=env, timeout_seconds=60,
    )

    def get_approval():
        if os.environ.get("PAB_MOVIE_REQUEST_ALLOWED") != "1":
            _deny()
        try:
            return _read(APPROVAL, owner_only=True)
        except (OSError, ValueError):
            _deny()

    with socket.socket(fileno=os.dup(0)) as peer:
        serve(peer, os.geteuid(), planner=planner, get_approval=get_approval,
              reserve=lambda request_id, tmdb_id: reserve_movie(LEDGER, request_id, tmdb_id),
              submit_movie=lambda tmdb_id: submit(tmdb_id, env))


if __name__ == "__main__":
    main()
