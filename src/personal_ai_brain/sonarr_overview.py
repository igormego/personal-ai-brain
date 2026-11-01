"""Operator-only Sonarr 4 GET collector. Not wired into production control.

The trusted host caller supplies the existing key in memory. Never give this
object/key to a model: publish only its bounded packet across the trust boundary.
"""
from datetime import datetime, timezone
import json
import re
import time
import urllib.request

from .assistant_media_resources import (
    MediaResourceUnavailable, sanitize_media_resource, validate_media_selector,
)

BASE = "http://127.0.0.1:8989/api/v3/"
VERSION = "4.0.17.2952"
MAX_BYTES = 8_000_000
MAX_TOTAL_BYTES = 64_000_000
MAX_SERIES = 256
MAX_EPISODES = 100_000
QUEUE_SIZE = 1000
MAX_SECONDS = 120
QUEUE_ROUTE = ("queue?page=1&pageSize=1000&sortKey=added&sortDirection=ascending"
               "&includeUnknownSeriesItems=true")


def _fail():
    raise MediaResourceUnavailable("media_resource_unavailable")


def _int(value, maximum=2_147_483_647, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _bool(value):
    if type(value) is not bool:
        _fail()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _rows(value, cap):
    if type(value) is not list or len(value) > cap:
        _fail()
    if any(type(row) is not dict for row in value):
        _fail()
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        _fail()


class SonarrOverviewCollector:
    """Fixed loopback target, no proxy/redirect, GET only, no retries or writes.

    This is an operator collection seam, not a production Registry provider.
    The API key is write-capable upstream; the method restriction is local, not
    a claim that Sonarr has issued a read-only credential.
    """

    def __init__(self, api_key):
        if type(api_key) is not str or not re.fullmatch(r"[a-fA-F0-9]{32}", api_key):
            _fail()
        self._key = api_key
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def _get(self, route):
        if route not in {"system/status", "series", "health", QUEUE_ROUTE} and not re.fullmatch(
            r"episode\?seriesId=[1-9][0-9]{0,9}", route
        ):
            _fail()
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            _fail()
        request = urllib.request.Request(
            BASE + route, headers={"X-Api-Key": self._key, "Accept": "application/json"},
            method="GET")
        with self._opener.open(request, timeout=min(10, remaining)) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                _fail()
            raw = response.read(MAX_BYTES + 1)
        self._bytes += len(raw)
        if (len(raw) > MAX_BYTES or self._bytes > MAX_TOTAL_BYTES
                or time.monotonic() > self._deadline):
            _fail()
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: _fail())

    def _series(self):
        result = {}
        for row in _rows(self._get("series"), MAX_SERIES):
            identity = _int(row["id"], minimum=1)
            if identity in result:
                _fail()
            result[identity] = _bool(row["monitored"])
        return result

    def _queue(self):
        raw = self._get(QUEUE_ROUTE)
        if type(raw) is not dict:
            _fail()
        total = _int(raw["totalRecords"], QUEUE_SIZE)
        if (type(raw["page"]) is not int or raw["page"] != 1
                or type(raw["pageSize"]) is not int or raw["pageSize"] != QUEUE_SIZE
                or raw["sortKey"] != "added" or raw["sortDirection"] != "ascending"):
            _fail()
        rows = _rows(raw["records"], QUEUE_SIZE)
        if len(rows) != total:
            _fail()
        result = {}
        for row in rows:
            # Pending queue IDs may be negative; do not mistake them for series IDs.
            identity = _int(row["id"], minimum=-2_147_483_648)
            status = row["trackedDownloadStatus"]
            if status not in ("ok", "warning", "error") or identity in result:
                _fail()
            result[identity] = status
        return result

    def __call__(self, resource_id):
        try:
            validate_media_selector({"resource_id": resource_id})
            self._deadline = time.monotonic() + MAX_SECONDS
            self._bytes = 0
            now = datetime.now(timezone.utc)
            if self._get("system/status")["version"] != VERSION:
                _fail()
            series = self._series()
            queue = self._queue()
            findings = []
            total = have = missing = 0
            episode_ids = set()
            for identity, monitored in sorted(series.items()):
                absent = 0
                for episode in _rows(self._get(f"episode?seriesId={identity}"), MAX_EPISODES):
                    eid = _int(episode["id"], minimum=1)
                    if eid in episode_ids or _int(episode["seriesId"], minimum=1) != identity:
                        _fail()
                    episode_ids.add(eid)
                    total += 1
                    if total > MAX_EPISODES:
                        _fail()
                    present = _bool(episode["hasFile"])
                    watched = _bool(episode["monitored"])
                    have += present
                    aired = episode.get("airDateUtc")
                    if aired is not None:
                        if type(aired) is not str or len(aired) > 40:
                            _fail()
                        aired = datetime.fromisoformat(aired.replace("Z", "+00:00"))
                        if aired.utcoffset() is None:
                            _fail()
                    if monitored and watched and not present and aired is not None and aired <= now:
                        absent += 1
                missing += absent
                if absent:
                    findings.append({"code": "missing_episodes", "series_id": identity, "count": absent})
                if not monitored:
                    findings.append({"code": "unmonitored_series", "series_id": identity, "count": 1})
            health = {"warnings": 0, "errors": 0}
            for row in _rows(self._get("health"), 1000):
                kind = row["type"]
                if kind not in ("warning", "error", "ok"):
                    _fail()
                if kind != "ok":
                    health[kind + "s"] += 1
            # Reject observed identity/status drift; this is not a database transaction.
            if series != self._series() or queue != self._queue():
                _fail()
            counts = {"total": len(queue), "warning": sum(s == "warning" for s in queue.values()),
                      "error": sum(s == "error" for s in queue.values())}
            for code, count in [("queue_warning", counts["warning"]), ("queue_error", counts["error"]),
                                ("health_warning", health["warnings"]), ("health_error", health["errors"])]:
                if count:
                    findings.append({"code": code, "series_id": None, "count": count})
            packet = {"schema_version": "0.1", "resource_id": resource_id,
                      "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "complete": True,
                      "series": {"total": len(series), "monitored": sum(series.values()),
                                 "unmonitored": len(series) - sum(series.values())},
                      "episodes": {"total": total, "have": have, "missing": missing},
                      "queue": counts, "health": health, "findings": findings[:12],
                      "findings_truncated": len(findings) > 12}
            if time.monotonic() > self._deadline:
                _fail()
            sanitize_media_resource(resource_id, packet)
            return packet
        except Exception:
            # Never expose upstream bodies, credential-bearing requests or paths.
            raise MediaResourceUnavailable("media_resource_unavailable") from None
