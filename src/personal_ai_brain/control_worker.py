"""Single-host Issues poller with durable at-most-once execution reservations."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time
import uuid

from personal_ai_brain.control import CapabilityFailed, Redactor, Rejected, build_registry, parse_request
from personal_ai_brain.github_issues import GitHubIssues, REPOSITORY, TITLE, TransportError
from personal_ai_brain.runtime import RunReader, atomic_json, timestamp

STATE = Path(os.environ.get("PAB_CONTROL_STATE", "/control"))
BEAT = Path("/tmp/pab-control-heartbeat.json")


def durable_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_json(path):
    # Corrupt records fail closed; never interpret corruption as permission to rerun.
    return json.loads(path.read_text()) if path.exists() else None


class ExecutionTimeout(BaseException):
    pass


@contextmanager
def deadline(seconds=120):
    def expired(*_):
        raise ExecutionTimeout()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def bounded_result(data, redactor):
    clean = redactor.clean(data)
    encoded = json.dumps(clean, ensure_ascii=True)
    if len(encoded) <= 16000:
        return clean
    # Full sanitized result remains in the private audit record; transport stays bounded.
    return {"truncated": True, "preview_json": encoded[:8000], "full_result": "local_private_audit"}


class Worker:
    def __init__(self, state, transport, registry, redactor, pulse=lambda: None, stopping=lambda: False):
        self.state, self.transport, self.registry, self.redactor = state, transport, registry, redactor
        self.pulse = pulse
        self.stopping = stopping
        for directory in (state, state / "requests", state / "issues"):
            directory.mkdir(parents=True, exist_ok=True)

    def save(self, path, record):
        durable_json(path, self.redactor.clean(record))

    def process(self, issue):
        number = issue["number"]
        if type(number) is not int or number < 1:
            raise TransportError("transport_unavailable")
        issue_path = self.state / "issues" / f"{number}.json"
        delivery = load_json(issue_path)
        if delivery is None:
            # Refresh after listing: ignore cancelled requests and PRs.
            issue = self.transport.get_issue(number)
            if issue.get("state") != "open" or issue.get("title") != TITLE or "pull_request" in issue:
                return
            correlation = f"invalid-issue-{number}"
            request_path = None
            record = {"received_at": timestamp(), "transport_issue": f"{REPOSITORY}#{number}",
                      "request_id": correlation, "capability": None, "arguments": {},
                      "validation": "rejected", "execution_start": None, "execution_end": None}
            try:
                request = parse_request(issue.get("body"))
                correlation = request["request_id"]
                # Bind the durable UUID to the entire original validated request.
                # A replay with changed fields must not borrow prior task truth.
                payload_hash = hashlib.sha256(json.dumps(
                    request, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")).hexdigest()
                record.update(request_id=correlation, capability=request["capability"],
                              requested_by=request["requested_by"], requested_at=request["requested_at"])
                capability, arguments = self.registry.resolve(request["capability"], request["arguments"])
                # Title-level ARR inventory is persistently disclosed in private
                # Issues + the durable ledger. requested_by is user-controlled
                # metadata, never proof of owner identity. Bind to GitHub's
                # authenticated issue author on every newly admitted request.
                if (capability.id == "media.resource_read"
                        and arguments.get("resource_id") in ("arr/radarr", "arr/sonarr")):
                    author = issue.get("user")
                    if (type(author) is not dict
                            or type(author.get("id")) is not int
                            or author["id"] != 55916200
                            or author.get("login") != "igormego"):
                        raise Rejected("owner_auth_required")
                record.update(arguments=arguments, validation="accepted")
                request_path = self.state / "requests" / (correlation + ".json")
                existing = load_json(request_path)
                if existing:
                    if existing.get("payload_hash") != payload_hash:
                        # Old pre-upgrade reservations cannot be verified; reject
                        # rather than treating an unbound UUID as a safe replay.
                        # Never overwrite or replay the original execution truth.
                        record.update(status="request_rejected", error="request_conflict")
                    else:
                        if existing["status"] == "processing":
                            existing.update(status="capability_execution_failed",
                                            error="interrupted_execution_not_retried",
                                            execution_end=timestamp())
                            self.save(request_path, existing)
                        record.update(status="duplicate", original_status=existing["status"],
                                      original_issue=existing["transport_issue"], result=existing.get("result"))
                else:
                    record.update(payload_hash=payload_hash, status="processing", execution_start=timestamp())
                    self.save(request_path, record)  # fsync BEFORE any handler invocation.
                    try:
                        with deadline():
                            if capability.id in {'work.submit', 'work.status', 'work.cancel'}:
                                result = capability.handler(arguments, request_id=correlation)
                            else:
                                result = capability.handler(arguments)
                        record.update(status="capability_completed", result=self.redactor.clean(result))
                    except CapabilityFailed:
                        record.update(status="capability_execution_failed", error="source_read_failure")
                    except ExecutionTimeout:
                        record.update(status="capability_execution_failed", error="execution_timeout")
                    except Exception:
                        record.update(status="capability_execution_failed", error="handler_failed")
                    record["execution_end"] = timestamp()
                    self.save(request_path, record)
            except Rejected as error:
                record.update(status="request_rejected", error=str(error))
            delivery = {"record": record, "posted": False, "closed": False}
            self.save(issue_path, delivery)
        record = delivery["record"]
        if not delivery["posted"]:
            envelope = {"schema_version": "0.1", "request_id": record["request_id"],
                        "capability": record["capability"], "status": record["status"],
                        "execution_start": record.get("execution_start"), "execution_end": record.get("execution_end"),
                        "error": record.get("error"), "original_status": record.get("original_status"),
                        "result": bounded_result(record.get("result"), self.redactor)}
            self.transport.post_result(number, self.redactor.clean(envelope))
            delivery["posted"] = True
            self.save(issue_path, delivery)
        # Closing is idempotent, including an already completed issue reopened by a caller.
        self.transport.close_issue(number)
        delivery["closed"] = True
        self.save(issue_path, delivery)
        return record["status"]

    def tick(self):
        with (self.state / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"transport": "worker_busy"}
            status = load_json(self.state / "status.json") or {}
            page = status.get("next_page", 1)
            issues = self.transport.list_page(page)
            last = status.get("last_request_status")
            for issue in issues:
                if self.stopping():
                    break
                self.pulse()
                if issue.get("title") == TITLE and "pull_request" not in issue:
                    last = self.process(issue) or last
            result = {"transport": "healthy", "checked_at": timestamp(),
                      "next_page": page + 1 if len(issues) == 100 else 1, "last_request_status": last}
            self.save(self.state / "status.json", result)
            return result


def health(state=STATE, heartbeat=BEAT):
    try:
        beat = load_json(heartbeat)
        if not beat or not 0 <= time.time() - beat["time"] <= 180:
            raise ValueError()
        status = load_json(state / "status.json") or {"transport": "not_checked"}
        return {"runtime": "healthy", **status}, 0 if status["transport"] == "healthy" else 3
    except (OSError, ValueError, TypeError, KeyError):
        return {"runtime": "unavailable"}, 1


def serve(state=STATE, heartbeat=BEAT):
    state.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    core_file = Path(os.environ.get("GITHUB_TOKEN_FILE", "/run/secrets/github_token"))
    control_file = Path(os.environ.get("CONTROL_TOKEN_FILE", "/run/secrets/github_control_token"))
    # Fail startup closed on missing credentials; never log arbitrary exception text.
    try:
        core_token, control_token = core_file.read_text().strip(), control_file.read_text().strip()
        if not core_token or not control_token:
            raise ValueError()
    except (OSError, ValueError):
        print("credential_unavailable", flush=True)
        return 3
    redactor = Redactor((core_token, control_token))
    from .work_handoff import Client
    # Activation requires an explicit operator-provided read-only mount.
    # Ignore every other environment value; do not accept a caller path.
    audit_file = (Path("/host-audit/snapshot.json")
                  if os.environ.get("PAB_HOST_AUDIT_ENABLED") == "1" else None)
    # Explicitly opted-in, fixed, read-only mount.
    # The existing master read grant is OFF by default. Legacy Sonarr is the
    # only resource at master enablement; Movies requires its own explicit grant
    # AND a separate fixed read-only mount. No model-selected paths or endpoints.
    media_provider = None
    if os.environ.get("PAB_MEDIA_RESOURCE_ENABLED") == "1":
        from .media_snapshot import read_mounted_media_snapshot

        movies_enabled = os.environ.get("PAB_MEDIA_MOVIES_ENABLED") == "1"

        def media_provider(resource_id):
            return read_mounted_media_snapshot(
                resource_id, movies_enabled=movies_enabled
            )

        # ARR item/title disclosure is an independent, OFF-by-default
        # configuration gate; existing Sonarr overview remains unchanged.
        services = frozenset(
            "arr/" + service for service in ("sonarr", "radarr")
            if os.environ.get("PAB_ARR_" + service.upper() + "_ENABLED") == "1"
        )
        if services:
            if os.environ.get("PAB_ARR_DISCLOSURE_APPROVED") != "1":
                raise ValueError("arr_title_disclosure_not_approved")
            from .arr_snapshot_bridge import read_mounted_arr
            from .resource_query import ResourceQueryBroker

            # Separate exact, operator-installed private Docker secret. Never
            # re-use the GitHub token as a cursor-signing key.
            with Path("/run/secrets/pab_arr_cursor_key").open("rb") as key_file:
                cursor_key = key_file.read(65)
            if not 32 <= len(cursor_key) <= 64:
                raise ValueError("invalid_arr_cursor_secret")
            providers = {
                rid: (lambda view, rid=rid: read_mounted_arr(rid, view))
                for rid in services
            }
            media_provider.query_broker = ResourceQueryBroker(
                providers, services, cursor_key, disclosure_approved=True
            )

    registry = build_registry(
        Path("/core-state"), lambda: RunReader(core_token),
        redactor, Client(), host_audit_snapshot=audit_file,
        media_resource_provider=media_provider,
    )
    def pulse():
        atomic_json(heartbeat, {"time": time.time()})
    worker = Worker(state, GitHubIssues(control_token), registry, redactor, pulse, stop.is_set)
    interval = max(15, min(300, int(os.environ.get("PAB_POLL_SECONDS", "30"))))
    failures = 0
    print("Control worker ready; fixed read and bounded work capabilities; private Issues transport", flush=True)
    try:
        while not stop.is_set():
            pulse()
            delay = interval
            try:
                worker.tick()
                failures = 0
            except TransportError as error:
                failures += 1
                delay = max(error.retry_after, min(900, 60 * 2 ** min(failures - 1, 4)))
                worker.save(state / "status.json", {"transport": error.status, "checked_at": timestamp(), "retry_in_seconds": delay})
            except Exception:
                worker.save(state / "status.json", {"transport": "worker_failure", "checked_at": timestamp()})
                delay = 60
            # Keep local liveness fresh during backoff; never poll faster to look healthy.
            until = time.monotonic() + delay
            while not stop.is_set() and time.monotonic() < until:
                stop.wait(min(5, max(0, until - time.monotonic())))
                pulse()
    finally:
        heartbeat.unlink(missing_ok=True)
    return 0


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "health"))
    parser.add_argument("--liveness", action="store_true")
    args = parser.parse_args()
    if args.action == "serve":
        return serve()
    data, code = health()
    if args.liveness:
        return 0 if data["runtime"] == "healthy" else 1
    print(json.dumps(data))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
