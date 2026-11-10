"""GET-only ARR adapter and registered broker tests; no host credentials or network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json

import pytest

from personal_ai_brain.arr_read import ARRReadProvider, ARRSourceUnavailable
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.resource_query import QueryUnavailable, ResourceQueryBroker, validate_query


class Response(io.BytesIO):
    def __init__(self, payload, url, *, status=200):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.url, self.status = url, status

    def geturl(self):
        return self.url


class FakeAPI:
    def __init__(self, rows, *, redirect=False, status=200):
        self.rows, self.calls, self.redirect, self.status = rows, [], redirect, status

    def __call__(self, request, timeout):
        self.calls.append(request)
        assert request.get_method() == "GET"
        assert request.get_header("X-api-key") == "local-test-key"
        assert timeout == 8
        url = request.full_url
        return Response(self.rows, url + "/unexpected" if self.redirect else url, status=self.status)


def radarr_rows():
    return [
        {"id": 9, "title": "Film B", "year": 2024, "monitored": False,
         "qualityProfileId": 3, "tmdbId": 99, "hasFile": False, "path": "/SECRET"},
        {"id": 2, "title": "Film A", "year": 2023, "monitored": True,
         "qualityProfileId": 4, "tmdbId": 22, "hasFile": True,
         "apiKey": "SHOULD_NOT_BE_EXPOSED"},
    ]


def api(service, rows, **kwargs):
    fake = FakeAPI(rows, **kwargs)
    provider = ARRReadProvider(service, "http://" + service + (":7878" if service == "radarr" else ":8989"),
                               "local-test-key", opener=fake)
    return provider, fake


def typed(service, view="items", limit=1, **kwargs):
    return {"resource_id": "arr/" + service, "view": view, "limit": limit, **kwargs}


def broker(providers, grants=None):
    return ResourceQueryBroker(
        providers, frozenset(providers if grants is None else grants), b"k" * 32,
        disclosure_approved=True,
    )


def test_radarr_live_get_allowlisted_and_no_upstream_leak():
    provider, fake = api("radarr", radarr_rows())
    answer = broker({"arr/radarr": provider}).read(typed("radarr", limit=2), principal="owner")
    assert [r["id"] for r in answer["items"]] == [2, 9]
    assert answer["items"][0]["monitored"] is True
    assert answer["items"][1]["has_file"] is False
    assert set(answer["items"][0]) == {
        "id", "title", "year", "monitored", "quality_profile_id", "external_id", "has_file",
    }
    assert "/SECRET" not in json.dumps(answer)
    assert "SHOULD_NOT_BE_EXPOSED" not in json.dumps(answer)
    assert answer["effect_performed"] is False
    assert fake.calls[0].full_url.endswith("/api/v3/movie")


def test_sonarr_live_get_and_profiles_registered_same_query_capability():
    source, fake = api("sonarr", [{
        "id": 17, "title": "Series", "year": 2023, "monitored": True,
        "qualityProfileId": 7, "tvdbId": 123,
    }])
    result = broker({"arr/sonarr": source}).read(typed("sonarr"), principal="owner")
    assert result["items"][0] == {
        "id": 17, "title": "Series", "year": 2023,
        "monitored": True, "quality_profile_id": 7, "external_id": 123,
        "has_file": None,
    }
    profiles, fake_profiles = api("sonarr", [
        {"id": 11, "name": "WEB-2160p", "cutoff": {"apiKey": "NO"}},
        {"id": 9, "name": "HD-1080p"},
    ])
    answer = broker({"arr/sonarr": profiles}).read(
        typed("sonarr", view="profiles", limit=2), principal="owner")
    assert answer["items"] == [{"id": 9, "name": "HD-1080p"}, {"id": 11, "name": "WEB-2160p"}]
    assert fake_profiles.calls[0].full_url.endswith("/api/v3/qualityprofile")


def test_cursor_stays_valid_across_fresh_gets_if_items_unchanged():
    provider, fake = api("radarr", radarr_rows())
    b = broker({"arr/radarr": provider})
    first = b.read(typed("radarr"), principal="owner")
    assert first["next_cursor"]
    second = b.read(typed("radarr", cursor=first["next_cursor"]), principal="owner")
    assert second["items"][0]["id"] == 9
    fake.rows[0]["monitored"] = True
    with pytest.raises(QueryUnavailable):
        b.read(typed("radarr", cursor=first["next_cursor"]), principal="owner")


def test_timestamp_change_does_not_break_cursor_when_data_unchanged():
    provider, _ = api("radarr", radarr_rows())
    source = provider("items")
    current = datetime.now(timezone.utc)
    class Replay:
        def __call__(self, view):
            assert view == "items"
            return deepcopy(source)
    source_provider = Replay()
    b = broker({"arr/radarr": source_provider})
    first = b.read(typed("radarr"), principal="owner", now=current)
    source["collected_at"] = (current + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert b.read(typed("radarr", cursor=first["next_cursor"]), principal="owner",
                  now=current + timedelta(seconds=1))["items"][0]["id"] == 9


@pytest.mark.parametrize("service,origin,key", [
    ("evil", "http://radarr:7878", "local-test-key"),
    ("radarr", "http://radarr:7878/other", "local-test-key"),
    ("radarr", "https://user:pwd@radarr:7878", "local-test-key"),
    ("radarr", "https://radarr:7878?url=https://bad.example", "local-test-key"),
    ("radarr", "http://radarr:7878", "bad\nkey"),
    ("radarr", "http://radarr:7878", ""),
    ("radarr", "http://radarr", "local-test-key"),
])
def test_untrusted_or_misconfigured_operator_origins_rejected(service, origin, key):
    with pytest.raises(ARRSourceUnavailable, match="^arr_source_unavailable$"):
        ARRReadProvider(service, origin, key, opener=lambda *a, **k: None)


@pytest.mark.parametrize("rows,options", [
    ([{"id": 2, "title": "Film", "year": 2020, "monitored": "yes",
       "qualityProfileId": 1, "tmdbId": 1, "hasFile": False}], {}),
    ([{"id": 2, "title": "Film", "year": 2020, "monitored": True,
       "qualityProfileId": 1, "tmdbId": 1, "hasFile": 1}], {}),
    (radarr_rows(), {"redirect": True}),
    (radarr_rows(), {"status": 301}),
    (b'{"id": 1, "id": 2}', {}),
    ({"error": "partial"}, {}),
    (b'[' + b' ' * 4_194_305 + b']', {}),
])
def test_bad_api_fails_closed_without_upstream_error(rows, options):
    provider, fake = api("radarr", rows, **options)
    with pytest.raises(ARRSourceUnavailable, match="^arr_source_unavailable$"):
        provider("items")


def test_independent_grant_and_source_validator_reject_tampering():
    provider, _ = api("radarr", radarr_rows())
    b = broker({"arr/radarr": provider}, grants=frozenset())
    with pytest.raises(QueryUnavailable):
        b.read(typed("radarr"), principal="owner")
    with pytest.raises(QueryUnavailable):
        broker({"arr/radarr": provider}).read(typed("radarr"), principal="unverified")
    bad = provider("items")
    bad["items"][0]["quality_profile_id"] = "wrong"
    with pytest.raises(QueryUnavailable):
        broker({"arr/radarr": lambda _view: bad}).read(typed("radarr"), principal="owner")


@pytest.mark.parametrize("bad", [
    {**typed("radarr"), "path": "/api/v3/command"},
    {**typed("radarr"), "method": "PUT"},
    {**typed("radarr"), "profile": 5},
    {**typed("radarr"), "limit": 50},
    {**typed("radarr"), "view": "settings-edit"},
    {**typed("radarr"), "target_id": 123},
])
def test_request_has_no_direct_api_controls(bad):
    with pytest.raises(QueryUnavailable):
        validate_query(bad)


def test_existing_registry_is_off_by_default_and_opt_in_is_shared():
    from pathlib import Path
    def legacy(_resource_id):
        return {}
    regular = build_registry(Path("."), lambda: None, Redactor(),
                             media_resource_provider=legacy)
    with pytest.raises(Rejected):
        regular.resolve("media.resource_read", typed("radarr"))
    provider, _ = api("radarr", radarr_rows())
    class Injected:
        query_broker = broker({"arr/radarr": provider})
        def __call__(self, _resource_id):
            return {}
    registry = build_registry(Path("."), lambda: None, Redactor(),
                              media_resource_provider=Injected())
    cap, args = registry.resolve("media.resource_read", typed("radarr"))
    assert cap.handler(args)["items"][0]["title"] == "Film A"
    with pytest.raises(Rejected):
        registry.resolve("media.resource_read", typed("sonarr"))
