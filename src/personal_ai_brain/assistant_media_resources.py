"""Opt-in, read-only media resource summary contract.

An operator-owned adapter supplies source-grounded, bounded metrics. This layer
does NOT open network sockets, read Sonarr secrets, guess API versions, or expose
raw paths/titles. One generic resource-read capability can later address other
explicitly approved media resources; no model-provided URL or API route exists.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import re

RESOURCE_IDS = frozenset({"sonarr/overview"})
MAX_SOURCE_AGE = timedelta(minutes=30)
FUTURE_SKEW = timedelta(minutes=2)
MAX_FINDINGS = 12
MAX_OUTPUT_BYTES = 4096
FINDING_CODES = frozenset({
    "missing_episodes", "unmonitored_series", "queue_warning",
    "queue_error", "health_warning", "health_error",
})


class MediaResourceUnavailable(ValueError):
    """One safe outward error code; upstream paths/errors are never returned."""


def _fail():
    raise MediaResourceUnavailable("media_resource_unavailable")


def validate_media_selector(value: object) -> dict[str, str]:
    if (type(value) is not dict or set(value) != {"resource_id"}
            or type(value["resource_id"]) is not str
            or value["resource_id"] not in RESOURCE_IDS):
        _fail()
    return {"resource_id": value["resource_id"]}


def _counts(raw, fields, *, total=None):
    if type(raw) is not dict or set(raw) != set(fields):
        _fail()
    if any(type(raw[field]) is not int or not 0 <= raw[field] <= 1_000_000
           for field in fields):
        _fail()
    if total is not None and any(raw[key] > raw[total] for key in fields if key != total):
        _fail()
    return {key: raw[key] for key in fields}


def sanitize_media_resource(resource_id: str, source: object, *, now=None) -> dict:
    """Validate a complete operator-produced record before exposing any facts."""
    if resource_id not in RESOURCE_IDS or type(source) is not dict or set(source) != {
        "schema_version", "resource_id", "collected_at", "complete",
        "series", "episodes", "queue", "health", "findings", "findings_truncated",
    }:
        _fail()
    if (source["schema_version"] != "0.1" or source["resource_id"] != resource_id
            or source["complete"] is not True
            or type(source["findings_truncated"]) is not bool):
        _fail()
    timestamp = source["collected_at"]
    if type(timestamp) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp
    ):
        _fail()
    try:
        collected = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        age = (now or datetime.now(timezone.utc)) - collected
    except (ValueError, TypeError, OverflowError):
        _fail()
    if not -FUTURE_SKEW <= age <= MAX_SOURCE_AGE:
        _fail()

    series = _counts(source["series"], ("total", "monitored", "unmonitored"), total="total")
    if series["monitored"] + series["unmonitored"] != series["total"]:
        _fail()
    episodes = _counts(source["episodes"], ("total", "have", "missing"), total="total")
    if episodes["have"] + episodes["missing"] > episodes["total"]:
        _fail()
    queue = _counts(source["queue"], ("total", "warning", "error"), total="total")
    health = _counts(source["health"], ("warnings", "errors"))

    raw = source["findings"]
    if type(raw) is not list or len(raw) > MAX_FINDINGS:
        _fail()
    findings = []
    seen = set()
    for finding in raw:
        if type(finding) is not dict or set(finding) != {"code", "series_id", "count"}:
            _fail()
        code, series_id, count = (finding[k] for k in ("code", "series_id", "count"))
        if (type(code) is not str or code not in FINDING_CODES
                or type(count) is not int or not 1 <= count <= 1_000_000
                or (series_id is not None and (
                    type(series_id) is not int or not 1 <= series_id <= 2_147_483_647
                ))):
            _fail()
        if code not in {"missing_episodes", "unmonitored_series"} and series_id is not None:
            _fail()
        identity = (code, series_id)
        if identity in seen:
            _fail()
        seen.add(identity)
        findings.append({"code": code, "series_id": series_id, "count": count})

    result = {
        "resource_id": resource_id, "collected_at": timestamp,
        "source": "operator_media_snapshot_not_live_probe",
        "freshness": "current", "complete": True, "series": series,
        "episodes": episodes, "queue": queue, "health": health,
        "findings": findings, "findings_truncated": source["findings_truncated"],
        "effect_performed": False,
    }
    if len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_OUTPUT_BYTES:
        _fail()
    return result


def read_media_resource(arguments: dict, provider) -> dict:
    """The trusted caller injects a fixed, read-only, source-verified provider."""
    selected = validate_media_selector(arguments)
    if not callable(provider):
        _fail()
    try:
        source = provider(selected["resource_id"])
        return sanitize_media_resource(selected["resource_id"], source)
    except Exception:
        # Never forward HTTP errors, raw upstream documents or secrets.
        _fail()
