"""Private stdio client. Exposes no generic command or remote transport interface."""
from collections import deque
import os
import json
import queue
import subprocess
import threading
import time

from .codex_bridge_core import CWD, PROFILE, approval

OVERRIDES = [
    'windows.sandbox="mxc"',
    'default_permissions="bridge-workspace-only"',
    'permissions.bridge-workspace-only.extends=":workspace"',
    'permissions.bridge-workspace-only.filesystem={":root"="deny",":minimal"="read",":tmpdir"="deny",":slash_tmp"="deny"}',
    'permissions.bridge-workspace-only.network.enabled=false',
]

def classify(message):
    """Codex omits jsonrpc; our correlation IDs are positive integers."""
    if not isinstance(message, dict):
        return "malformed"
    if "jsonrpc" in message and message["jsonrpc"] != "2.0":
        return "malformed"
    if "method" in message:
        if ("result" in message or "error" in message or
                not isinstance(message["method"], str) or not message["method"] or
                ("params" in message and not isinstance(message["params"], dict))):
            return "malformed"
        if "id" not in message:
            return "notification"
        identifier = message["id"]
        if type(identifier) is int or (type(identifier) is str and identifier):
            return "server_request"
        return "malformed"
    if type(message.get("id")) is not int or message["id"] <= 0:
        return "malformed"
    if ("result" in message) == ("error" in message):
        return "malformed"
    if "error" in message:
        error = message["error"]
        if (not isinstance(error, dict) or type(error.get("code")) is not int or
                not isinstance(error.get("message"), str)):
            return "malformed"
    return "response"


def bridge_environment():
    """Choose the OS shell already covered by :minimal; no extra home reads."""
    env = os.environ.copy()
    if os.name == "nt":
        entries = env.get("PATH", "").split(os.pathsep)
        entries = [entry for entry in entries
                   if "codex-primary-runtime" not in entry.lower()
                   and "powershell" not in entry.lower()]
        shell = os.path.join(env.get("SystemRoot", r"C:\\Windows"),
                             "System32", "WindowsPowerShell", "v1.0")
        env["PATH"] = os.pathsep.join([shell, *entries])
    return env


class AppServer:
    def __init__(self, executable):
        args = [str(executable), "app-server", "--listen", "stdio://"]
        for setting in OVERRIDES:
            args.extend(["-c", setting])
        from .codex_bridge_process import ProcessOwner
        self.lifecycle = "STARTING"
        self.stopping = threading.Event()
        self.close_lock = threading.Lock()
        self.owner = ProcessOwner()
        self.process = self.owner.spawn(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        cwd=CWD, env=bridge_environment())
        self.messages = queue.Queue(maxsize=256)
        self.serial = 0
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.server_request_handler = None
        self.disconnected = False
        self.writes = queue.Queue(maxsize=32)
        self.reader = threading.Thread(target=self._read, name="pab-codex-reader")
        self.writer = threading.Thread(target=self._write, name="pab-codex-writer")
        self.reader.start()
        self.writer.start()
        self.lifecycle = "RUNNING"
        try:
            self.rpc("initialize", {"clientInfo": {"name": "pab_bridge_core", "version": "0.1"},
                               "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def diagnostic(self, category):
        if not hasattr(self, "diagnostics"):
            self.diagnostics = deque(maxlen=64)
        self.diagnostics.append({"protocol_error": category, "reader_continued": True})

    def _read(self):
        failure = "app_server_disconnected"
        try:
            while True:
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise ValueError("event_too_large")
                try:
                    message = json.loads(line)
                    category = classify(message)
                except (ValueError, TypeError, RecursionError):
                    category = "malformed"
                if category == "malformed":
                    self.diagnostic("malformed_message")
                    continue
                if category in {"notification", "server_request"}:
                    if category == "server_request" and self.server_request_handler:
                        try:
                            self.server_request_handler(message)
                        except Exception:
                            self.diagnostic("server_request_handler_failed")
                    # Backpressure on valid events; malformed floods never enter this queue.
                    while not getattr(self, "stopping", threading.Event()).is_set():
                        try:
                            self.messages.put(message, timeout=.1)
                            break
                        except queue.Full:
                            continue
                else:
                    with self.pending_lock:
                        waiter = self.pending.get(message["id"])
                        if waiter:
                            try:
                                waiter.put_nowait(message)
                            except queue.Full:
                                self.diagnostic("duplicate_response")
                        else:
                            self.diagnostic("unmatched_response")
        except Exception:
            failure = "transport_reader_failed"
        finally:
            self.disconnected = True
            with self.pending_lock:
                waiters = list(self.pending.values())
            for waiter in waiters:
                try:
                    waiter.put_nowait({"client_failure": failure})
                except queue.Full:
                    pass
            try:
                self.messages.put_nowait({"client_failure": failure})
            except queue.Full:
                pass

    def _write(self):
        while not self.stopping.is_set():
            try:
                message, done = self.writes.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
                done.put_nowait(None)
            except Exception:
                done.put_nowait("transport_write_failed")
                self._fail_waiters("transport_write_failed")
                return

    def _fail_waiters(self, failure):
        with self.pending_lock:
            self.disconnected = True
            for waiter in self.pending.values():
                try:
                    waiter.put_nowait({"client_failure": failure})
                except queue.Full:
                    pass

    def send(self, message):
        if self.disconnected or self.stopping.is_set():
            raise RuntimeError("client_stopping")
        done = queue.Queue(maxsize=1)
        try:
            self.writes.put((message, done), timeout=1)
            error = done.get(timeout=3)
        except queue.Empty:
            self._fail_waiters("transport_write_timeout")
            raise RuntimeError("transport_write_timeout") from None
        if error:
            raise RuntimeError(error)

    def event(self, timeout=30):
        value = self.messages.get(timeout=timeout)
        if value.get("client_failure"):
            raise RuntimeError(value["client_failure"])
        return value

    def rpc(self, method, params):
        if method not in {"initialize", "thread/start", "turn/start", "turn/interrupt",
                          "thread/read", "thread/turns/list", "thread/items/list"}:
            raise ValueError("method_forbidden")
        with self.pending_lock:
            if self.disconnected:
                raise RuntimeError("app_server_disconnected")
            self.serial += 1
            request = self.serial
            waiter = queue.Queue(maxsize=1)
            self.pending[request] = waiter
        try:
            self.send({"id": request, "method": method, "params": params})
            message = waiter.get(timeout=30)
            if "client_failure" in message:
                raise RuntimeError(message["client_failure"])
            if "error" in message:
                raise RuntimeError("app_server_rpc_failed")
            return message["result"]
        finally:
            with self.pending_lock:
                self.pending.pop(request, None)

    def history(self, thread_id, turn_id):
        cursor = None
        for _ in range(100):
            params = {"threadId": thread_id, "limit": 50, "itemsView": "notLoaded"}
            if cursor:
                params["cursor"] = cursor
            response = self.rpc("thread/turns/list", params)
            for turn in response["data"]:
                if turn["id"] == turn_id:
                    return turn["status"]
            cursor = response.get("nextCursor")
            if not cursor:
                return None
        return None

    def close(self):
        # Never close a buffered pipe while another thread may own its I/O lock.
        if not self.close_lock.acquire(timeout=.1):
            return
        try:
            if self.lifecycle == "STOPPED":
                return
            self.lifecycle = "STOPPING"
            self.stopping.set()
            self._fail_waiters("client_shutdown")
            process = self.process
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            # Job closure also kills descendants holding inherited pipe handles,
            # even if the root app-server has already exited.
            self.owner.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.lifecycle = "FAILED"
            for thread in (self.reader, self.writer):
                if thread is not threading.current_thread():
                    thread.join(timeout=2)
            if self.reader.is_alive() or self.writer.is_alive():
                self.lifecycle = "FAILED"
            else:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
                self.lifecycle = "STOPPED"
        finally:
            self.close_lock.release()


def execute(ledger, payload, client, timeout=120):
    """Reserve exactly once. Caller-supplied factory/bind are trusted local code."""
    task = ledger.submit(payload)
    if task["state"] != "submitted":
        return task
    task_id = task["task_id"]
    ledger.transition(task_id, "submitted", "reserved")
    def server_request(event):
        if event.get("method") in {
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
        }:
            current = ledger.submit(payload)
            params = event.get("params", {})
            if current["state"] == "running" and params.get("threadId") == current["thread_id"]:
                if current["turn_id"] is None and params.get("turnId"):
                    ledger.bind(task_id, current["thread_id"], params["turnId"])
                approval(ledger, task_id, event)
    client.server_request_handler = server_request
    try:
        from .codex_bridge_workspace import prepare, git
        approved_base = git("rev-parse", "HEAD")
        if task["base_sha"] != approved_base:
            raise ValueError("base_not_approved")
        workspace = prepare(task_id, approved_base)
        result = client.rpc("thread/start", {
            "cwd": workspace["cwd"], "permissions": PROFILE, "approvalPolicy": "on-request"})
        thread_id = result["thread"]["id"]
        ledger.bind(task_id, thread_id, None)
        # Persist running BEFORE the potentially ambiguous turn/start RPC.
        ledger.transition(task_id, "reserved", "running")
        result = client.rpc("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text":
                task["goal"] + "\nAcceptance criteria:\n" + task["acceptance_criteria"]}]})
        turn_id = result["turn"]["id"]
        ledger.bind(task_id, thread_id, turn_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = client.event(min(30, max(.01, deadline - time.monotonic())))
            if ledger.submit(payload)["state"] == "approval_required":
                client.close()
                return ledger.submit(payload)
            if event.get("method") == "turn/completed":
                turn = event.get("params", {}).get("turn", {})
                if turn.get("id") != turn_id:
                    continue
                target = {"completed": "succeeded", "failed": "failed",
                          "interrupted": "interrupted"}.get(turn["status"], "uncertain")
                return ledger.transition(task_id, "running", target)
        client.rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
    except Exception:
        pass
    current = ledger.submit(payload)
    if current["state"] in {"reserved", "running"}:
        return ledger.transition(task_id, current["state"], "uncertain",
                                 failure_class="execution_truth_uncertain")
    return current





def cancel(ledger, task_id, client):
    """Owner-invoked cancellation; identifiers come only from durable task truth."""
    from .codex_bridge_core import identifier
    identifier(task_id)
    task = ledger.load()["tasks"][task_id]
    if task["state"] not in {"running", "approval_required"}:
        return task
    if not task["thread_id"] or not task["turn_id"]:
        return task
    client.rpc("turn/interrupt", {"threadId": task["thread_id"], "turnId": task["turn_id"]})
    status = client.history(task["thread_id"], task["turn_id"])
    if status == "interrupted":
        return ledger.transition(task_id, task["state"], "interrupted",
                                 failure_class="owner_cancelled")
    return ledger.load()["tasks"][task_id]
