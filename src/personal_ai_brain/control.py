"""Transport-independent request policy and explicitly bound read-only capabilities."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
from typing import Callable
from uuid import UUID

from personal_ai_brain import runtime
from personal_ai_brain.machine_snapshot import build_machine_snapshot
from personal_ai_brain.assistant_project_os import StatusRejected, select_project_status, validate_selector


class Rejected(ValueError):
    """Messages are fixed policy codes, never input or exception text."""


class CapabilityFailed(RuntimeError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Rejected("duplicate_json_key")
        result[key] = value
    return result


def parse_request(body, now=None):
    if not isinstance(body, str) or len(body.encode()) > 8192:
        raise Rejected("request_size")
    try:
        data = json.loads(body, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        fields = {"schema_version", "request_id", "capability", "arguments", "requested_by", "requested_at"}
        if not isinstance(data, dict) or set(data) != fields or data["schema_version"] != "0.1":
            raise Rejected("request_schema")
        if not isinstance(data["request_id"], str) or str(UUID(data["request_id"])) != data["request_id"]:
            raise Rejected("request_id")
        for key in ("capability", "requested_by"):
            if not isinstance(data[key], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", data[key]):
                raise Rejected("request_schema")
        if not isinstance(data["arguments"], dict):
            raise Rejected("arguments")
        date = data["requested_at"]
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", date):
            raise Rejected("requested_at")
        when = datetime.fromisoformat(date.replace("Z", "+00:00"))
        age = (now or datetime.now(timezone.utc)) - when
        if not -timedelta(minutes=5) <= age <= timedelta(days=7):
            raise Rejected("request_expired_or_future")
        return data
    except Rejected:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise Rejected("malformed_request") from None


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(value for value in secrets if value)

    def text(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[REDACTED]", text)
        text = re.sub(r"(?i)(?:bearer\s+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,\"}]+", "[REDACTED]", text)
        return text

    def clean(self, value):
        if isinstance(value, dict):
            return {self.text(str(k)): self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return self.text(value) if isinstance(value, str) else value


def no_arguments(arguments):
    if arguments != {}:
        raise Rejected("invalid_arguments")
    return {}


def log_arguments(arguments):
    if set(arguments) - {"lines"}:
        raise Rejected("invalid_arguments")
    lines = arguments.get("lines", 30)
    if type(lines) is not int or not 1 <= lines <= 100:
        raise Rejected("invalid_arguments")
    return {"lines": lines}


@dataclass(frozen=True)
class Capability:
    id: str
    description: str
    risk_level: str
    argument_schema: dict
    validate: Callable
    handler: Callable
    side_effect_class: str = "none"


class Registry:
    def __init__(self, capabilities=()):
        self.capabilities = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability):
        if capability.id in self.capabilities:
            raise Rejected("duplicate_capability")
        bounded_work = (capability.id in {'work.submit', 'work.cancel'}
                        and capability.risk_level == 'bounded_work'
                        and capability.side_effect_class == 'local_work_handoff')
        if not bounded_work and (capability.risk_level != "read_only" or capability.side_effect_class != "none"):
            raise Rejected("capability_policy_denied")
        self.capabilities[capability.id] = capability

    def resolve(self, name, arguments):
        capability = self.capabilities.get(name)
        if capability is None:
            raise Rejected("unknown_capability")
        return capability, capability.validate(arguments)


def build_registry(core_state: Path, reader_factory, redactor, work_client=None):
    def runtime_health(_):
        health, code = runtime.health(core_state, core_state / "heartbeat.json")
        last = health.get("last_run", {})
        return {"runtime": health["runtime"], "last_manual_run_status": last.get("status"),
                "last_manual_run_at": last.get("finished_at"), "health_exit": code}

    def core_result(kind):
        def handler(_):
            reader = reader_factory()
            agent, snapshot, proposals = runtime.collect(reader)
            status, _ = runtime.classify(snapshot, reader.read_failures)
            if status == "source_read_failure":
                raise CapabilityFailed("source_read_failure")
            machine = build_machine_snapshot(snapshot, proposals)
            result = {"source_status": status, "health": machine["health"]}
            if kind == "snapshot":
                result["snapshot"] = machine
            elif kind == "report":
                result["report"] = agent.render_markdown(snapshot)
            elif kind == "rollup_proposals":
                result["proposals"] = [p.to_dict() for p in proposals]
            return result
        return handler

    def project_status(arguments):
        # The existing Core reader owns GitHub source access; the selector has
        # no repository/ref/path input and can never invoke coding or writes.
        reader = reader_factory()
        _agent, snapshot, _proposals = runtime.collect(reader)
        try:
            return select_project_status(snapshot, arguments)
        except StatusRejected:
            raise CapabilityFailed("source_read_failure") from None

    def project_status_selector(arguments):
        try:
            return validate_selector(arguments)
        except StatusRejected:
            raise Rejected("invalid_project_selector") from None

    def logs_tail(arguments):
        path = core_state / "service.log"  # Fixed file, never a request argument.
        with path.open("rb") as source:
            size = source.seek(0, 2)
            source.seek(max(0, size - 8192))
            text = source.read(8192).decode("utf-8", errors="replace")
        if size > 8192:
            text = text.partition("\n")[2]
        text = redactor.text(text)
        text = "\n".join(text.splitlines()[-arguments["lines"]:])
        return {"log": "core.service", "text": text.encode()[-4096:].decode("utf-8", errors="ignore"),
                "bounded": True, "max_bytes": 4096}

    empty_schema = {"type": "object", "additionalProperties": False, "properties": {}}
    caps = [Capability("runtime.health", "Core process liveness and last manual run", "read_only", empty_schema, no_arguments, runtime_health)]
    for kind in ("health", "snapshot", "report", "rollup_proposals"):
        caps.append(Capability("core." + kind, "Fresh read-only Core " + kind, "read_only", empty_schema, no_arguments, core_result(kind)))
    caps.append(Capability("project_os.status", "Read-only one-project or workstream status",
                           "read_only",
                           {"type": "object", "additionalProperties": False,
                            "required": ["project_id"],
                            "properties": {"project_id": {"type": "string"},
                                           "workstream_id": {"type": "string"}}},
                           project_status_selector, project_status))
    caps.append(Capability("runtime.logs_tail", "Bounded redacted Core lifecycle log", "read_only",
                           {"type": "object", "additionalProperties": False, "properties": {"lines": {"type": "integer", "minimum": 1, "maximum": 100}}},
                           log_arguments, logs_tail))
    if work_client is not None:
        from .work_handoff import arguments as work_arguments
        from .work_contract import Rejected as WorkRejected
        for operation in ('work.submit', 'work.status', 'work.cancel'):
            def validate(value, operation=operation):
                try:
                    return work_arguments(operation, value)
                except (WorkRejected, ValueError, TypeError, RecursionError):
                    raise Rejected('invalid_work_arguments') from None
            def handle(value, request_id, operation=operation):
                return work_client.invoke(operation, value, request_id)
            field = 'contract' if operation == 'work.submit' else 'task_id'
            caps.append(Capability(operation, 'Bounded local Work Runner operation',
                'read_only' if operation == 'work.status' else 'bounded_work',
                {'type': 'object', 'additionalProperties': False, 'required': [field],
                 'properties': {field: {'type': 'object' if field == 'contract' else 'string'}}},
                validate, handle, 'none' if operation == 'work.status' else 'local_work_handoff'))
    return Registry(caps)
