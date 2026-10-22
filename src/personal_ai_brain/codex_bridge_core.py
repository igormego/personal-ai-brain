"""Bridge-owned execution identity. No transport, automatic retry or approvals."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

ACTIVE = {"reserved", "running", "approval_required"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "uncertain"}
REPOSITORY = "Az1mutt/personal-ai-brain"
CWD = r"C:\Projects\personal-ai-brain"
PROFILE = "bridge-workspace-only"

def now():
    return datetime.now(timezone.utc).isoformat()

def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_identifier")
    return value

def validate(payload):
    keys = {"request_id", "task_id", "project_id", "execution_profile",
            "goal", "acceptance_criteria", "base_sha"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("invalid_fields")
    for key in ("request_id", "task_id"):
        identifier(payload[key])
    if payload["project_id"] != "personal-ai-brain":
        raise ValueError("unknown_project")
    if payload["execution_profile"] != "local-homelab-admin":
        raise ValueError("unknown_profile")
    for key in ("goal", "acceptance_criteria"):
        if not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 8000:
            raise ValueError("invalid_text")
    sha = payload["base_sha"]
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("invalid_base")
    return dict(payload)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

@contextmanager
def locked(path):
    with open(path, "a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)

def atomic(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "xb") as stream:
            stream.write(json.dumps(data, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            import ctypes
            # Replace existing target and flush the move to disk.
            move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            move.restype = ctypes.c_int
            if not move(str(temporary.resolve()), str(path.resolve()), 0x1 | 0x8):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

class Ledger:
    """One atomic snapshot serializes invocation and execution reservations."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.json"

    def load(self):
        if not self.path.exists():
            return {"version": 1, "requests": {}, "tasks": {}}
        state = json.loads(self.path.read_text())
        if state["version"] != 1:
            raise ValueError("unsupported_ledger")
        return state

    def submit(self, payload):
        payload = validate(payload)
        task_payload = {k: v for k, v in payload.items() if k != "request_id"}
        request_hash, task_hash = digest(payload), digest(task_payload)
        with locked(self.root / "ledger.lock"):
            state = self.load()
            invocation = state["requests"].get(payload["request_id"])
            if invocation:
                if invocation["hash"] != request_hash:
                    raise ValueError("request_conflict")
                return dict(state["tasks"][invocation["task_id"]])
            task = state["tasks"].get(payload["task_id"])
            if task and task["payload_hash"] != task_hash:
                raise ValueError("task_conflict")
            if task is None:
                task = {**task_payload, "request_id": payload["request_id"],
                        "payload_hash": task_hash, "repository": REPOSITORY,
                        "state": "submitted", "attempts": 0, "thread_id": None,
                        "turn_id": None, "created_at": now(), "updated_at": now(),
                        "failure_class": None}
                state["tasks"][payload["task_id"]] = task
            state["requests"][payload["request_id"]] = {
                "hash": request_hash, "task_id": payload["task_id"]}
            atomic(self.path, state)
            return dict(task)

    def transition(self, task_id, expected, target, **fields):
        identifier(task_id)
        edges = {"submitted": {"reserved", "cancelled"},
                 "reserved": {"running"} | TERMINAL,
                 "running": {"approval_required"} | TERMINAL,
                 "approval_required": {"interrupted", "uncertain", "cancelled"}}
        if target not in edges.get(expected, set()):
            raise ValueError("invalid_transition")
        if set(fields) - {"thread_id", "turn_id", "failure_class"}:
            raise ValueError("invalid_metadata")
        with locked(self.root / "ledger.lock"):
            state = self.load()
            task = state["tasks"][task_id]
            if task["state"] != expected:
                raise ValueError("state_conflict")
            if target == "reserved":
                if task["attempts"] != 0:
                    raise ValueError("replay_forbidden")
                task["attempts"] = 1
            task.update(fields, state=target, updated_at=now())
            atomic(self.path, state)
            return dict(task)

    def bind(self, task_id, thread_id, turn_id):
        identifier(task_id)
        with locked(self.root / "ledger.lock"):
            state = self.load()
            task = state["tasks"][task_id]
            if task["state"] not in {"reserved", "running"}:
                raise ValueError("binding_forbidden")
            if task["thread_id"] not in {None, thread_id}:
                raise ValueError("thread_conflict")
            if task["turn_id"] not in {None, turn_id}:
                raise ValueError("turn_conflict")
            task.update(thread_id=thread_id, turn_id=turn_id, updated_at=now())
            atomic(self.path, state)

    def recover(self, history):
        """history(thread_id, turn_id) is read-only; no start/resume callbacks."""
        tasks = list(self.load()["tasks"].values())
        for task in tasks:
            if task["state"] not in ACTIVE:
                continue
            status = None
            if task["thread_id"] and task["turn_id"]:
                try:
                    status = history(task["thread_id"], task["turn_id"])
                except Exception:
                    status = None
            target = {"completed": "succeeded", "failed": "failed",
                      "interrupted": "interrupted"}.get(status, "uncertain")
            # Approval states cannot become success from unrelated/stale evidence.
            if task["state"] == "approval_required" and target in {"succeeded", "failed"}:
                target = "uncertain"
            self.transition(task["task_id"], task["state"], target,
                            failure_class=None if target == "succeeded" else "recovery_" + target)

def public_result(task):
    """Deliberately omit goal, raw output, paths and arbitrary model summaries."""
    return {key: task[key] for key in (
        "request_id", "task_id", "project_id", "base_sha", "repository",
        "attempts", "created_at", "updated_at", "failure_class")} | {
            "status": task["state"],
            "approval_required": task["state"] == "approval_required"}

def approval(ledger, task_id, event):
    if event.get("method") in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    }:
        ledger.transition(task_id, "running", "approval_required",
                          failure_class="owner_approval_required")
        return True  # Caller must disconnect/interrupt, never respond with acceptance.
    return False



