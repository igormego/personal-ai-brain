"""Synthetic fixtures only; no credentials or network access."""
from copy import deepcopy
from email.message import Message
import io
import json
import urllib.error

import pytest

from personal_ai_brain import sonarr_overview as mod
from personal_ai_brain.assistant_media_resources import MediaResourceUnavailable
from personal_ai_brain.control import build_registry, Redactor


def fixtures():
    return {
        "system/status": {"version": mod.VERSION},
        "series": [{"id": 1, "monitored": True, "title": "PRIVATE", "path": "/private"}],
        "episode?seriesId=1": [
            {"id": 1, "seriesId": 1, "hasFile": True, "monitored": True},
            {"id": 2, "seriesId": 1, "hasFile": False, "monitored": True, "airDateUtc": "2020-01-01T00:00:00Z"},
            {"id": 3, "seriesId": 1, "hasFile": False, "monitored": True, "airDateUtc": "2099-01-01T00:00:00Z"},
            {"id": 4, "seriesId": 1, "hasFile": False, "monitored": False, "airDateUtc": "2020-01-01T00:00:00Z"},
            {"id": 5, "seriesId": 1, "hasFile": False, "monitored": True},
        ],
        mod.QUEUE_ROUTE: {"page": 1, "pageSize": 1000, "sortKey": "added", "sortDirection": "ascending",
                          "totalRecords": 1, "records": [{"id": -1, "trackedDownloadStatus": "warning", "title": "PRIVATE"}]},
        "health": [{"type": "warning", "message": "PRIVATE credential path"}],
    }


class Response(io.BytesIO):
    status = 200
    headers = Message()
    headers["Content-Type"] = "application/json"


class Transport:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def open(self, request, timeout):
        assert request.method == "GET" and request.data is None
        assert request.full_url.startswith(mod.BASE)
        assert 0 < timeout <= 10
        route = request.full_url[len(mod.BASE):]
        self.calls.append(route)
        value = self.values[route]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value()
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode())


def collector(values=None):
    obj = mod.SonarrOverviewCollector("a" * 32)
    obj._opener = Transport(fixtures() if values is None else values)
    return obj


def test_real_http_shape_to_existing_registry_contract(tmp_path):
    obj = collector()
    cap, args = build_registry(tmp_path, None, Redactor(), media_resource_provider=obj).resolve(
        "media.resource_read", {"resource_id": "sonarr/overview"})
    result = cap.handler(args)
    assert result["episodes"] == {"total": 5, "have": 1, "missing": 1}
    assert result["queue"] == {"total": 1, "warning": 1, "error": 0}
    assert result["health"] == {"warnings": 1, "errors": 0}
    assert result["effect_performed"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert obj._opener.calls.count(mod.QUEUE_ROUTE) == 2
    assert obj._opener.calls.count("series") == 2


@pytest.mark.parametrize("change", [
    lambda f: f["system/status"].update(version="5.0.0"),
    lambda f: f["series"].append(deepcopy(f["series"][0])),
    lambda f: f["series"][0].update(id=True),
    lambda f: f["series"][0].update(monitored="true"),
    lambda f: f["episode?seriesId=1"][0].update(seriesId=2),
    lambda f: f["episode?seriesId=1"].append(deepcopy(f["episode?seriesId=1"][0])),
    lambda f: f["episode?seriesId=1"][0].update(hasFile=None),
    lambda f: f["episode?seriesId=1"][0].update(airDateUtc="2020-01-01"),
    lambda f: f["episode?seriesId=1"][0].update(airDateUtc="invalid"),
    lambda f: f[mod.QUEUE_ROUTE].update(totalRecords=2),
    lambda f: f[mod.QUEUE_ROUTE].update(totalRecords=1001),
    lambda f: f[mod.QUEUE_ROUTE].update(page=2),
    lambda f: f[mod.QUEUE_ROUTE].update(pageSize=100),
    lambda f: f[mod.QUEUE_ROUTE]["records"][0].update(trackedDownloadStatus="unknown"),
    lambda f: f["health"][0].update(type="unexpected"),
    lambda f: f.update(health=b'{"type":1,"type":2}'),
    lambda f: f.update(health=b'[NaN]'),
    lambda f: f.update(health=b'not json'),
    lambda f: f.update(health=TimeoutError("PRIVATE")),
    lambda f: f.update(health=urllib.error.HTTPError("PRIVATE", 401, "PRIVATE", {}, None)),
])
def test_fail_closed_no_partial_packet_or_sensitive_error(change):
    values = fixtures()
    change(values)
    with pytest.raises(MediaResourceUnavailable) as exc:
        collector(values)("sonarr/overview")
    assert str(exc.value) == "media_resource_unavailable"
    assert exc.value.__suppress_context__


@pytest.mark.parametrize("selector", ["sonarr/episodes", "http://evil", {}, None])
def test_selector_denied_before_access(selector):
    obj = collector()
    with pytest.raises(MediaResourceUnavailable):
        obj(selector)
    assert obj._opener.calls == []


@pytest.mark.parametrize("route", ["command", "series/1", "episode?seriesId=1&x=y", "http://evil"])
def test_no_arbitrary_get_route(route):
    with pytest.raises(MediaResourceUnavailable):
        collector()._get(route)


def test_redirect_and_proxy_boundary(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://untrusted.invalid")
    obj = mod.SonarrOverviewCollector("b" * 32)
    assert not any(isinstance(h, mod.urllib.request.ProxyHandler) and h.proxies
                   for h in obj._opener.handlers)
    redirect = next(h for h in obj._opener.handlers if isinstance(h, mod._NoRedirect))
    with pytest.raises(MediaResourceUnavailable):
        redirect.redirect_request(None, None, 302, "", {}, "http://untrusted.invalid")


def test_queue_drift_fails_without_retry():
    values = fixtures()
    first = deepcopy(values[mod.QUEUE_ROUTE])
    second = deepcopy(first)
    second["records"][0]["id"] = 100
    answers = iter([first, second])
    values[mod.QUEUE_ROUTE] = lambda: next(answers)
    obj = collector(values)
    with pytest.raises(MediaResourceUnavailable):
        obj("sonarr/overview")
    assert obj._opener.calls.count(mod.QUEUE_ROUTE) == 2


def test_empty_library_is_verified_zero():
    values = fixtures()
    values["series"] = []
    values["health"] = []
    values[mod.QUEUE_ROUTE].update(records=[], totalRecords=0)
    assert collector(values)("sonarr/overview")["episodes"] == {"total": 0, "have": 0, "missing": 0}


@pytest.mark.parametrize("status,content_type", [(302, "application/json"), (200, "text/html"), (500, "application/json")])
def test_http_status_and_content_type_rejected(monkeypatch, status, content_type):
    headers = Message()
    headers["Content-Type"] = content_type
    monkeypatch.setattr(Response, "status", status)
    monkeypatch.setattr(Response, "headers", headers)
    with pytest.raises(MediaResourceUnavailable):
        collector()("sonarr/overview")


def test_series_drift_rejected():
    values = fixtures()
    answers = iter([deepcopy(values["series"]), []])
    values["series"] = lambda: next(answers)
    with pytest.raises(MediaResourceUnavailable):
        collector(values)("sonarr/overview")


@pytest.mark.parametrize("limit", ["MAX_BYTES", "MAX_TOTAL_BYTES", "MAX_SERIES", "MAX_EPISODES", "MAX_SECONDS"])
def test_resource_budgets_fail_closed(monkeypatch, limit):
    monkeypatch.setattr(mod, limit, 0)
    with pytest.raises(MediaResourceUnavailable):
        collector()("sonarr/overview")


def test_findings_capped_and_explicitly_truncated():
    values = fixtures()
    values["series"] = [{"id": n, "monitored": False} for n in range(1, 15)]
    for n in range(1, 15):
        values[f"episode?seriesId={n}"] = []
    result = collector(values)("sonarr/overview")
    assert result["findings_truncated"] is True and len(result["findings"]) == 12
