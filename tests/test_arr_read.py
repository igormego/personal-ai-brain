"""GET-only ARR adapter and registered broker tests; no host credentials or network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json

import pytest

from personal_ai_brain.arr_read import ARRReadProvider, ARRSourceUnavailable
from personal_ai_brain.control import CapabilityFailed, Redactor, Rejected, build_registry
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
    port = {"radarr": 7878, "sonarr": 8989, "seerr": 5055}[service]
    provider = ARRReadProvider(service, f"http://{service}:{port}",
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


def test_sonarr_diagnostics_require_separate_grant_and_strip_private_text():
    provider, upstream = api("sonarr", {"totalRecords": 1, "records": [{
        "id": 18, "seriesId": 7, "episodeId": 25, "status": "completed",
        "trackedDownloadStatus": "warning", "trackedDownloadState": "importPending",
        "statusMessages": [{"title": "PRIVATE", "messages": ["Access to the path /PRIVATE: permission denied"]}],
        "outputPath": "/PRIVATE", "downloadId": "PRIVATE",
    }]})
    query = typed("sonarr", view="queue")
    with pytest.raises(QueryUnavailable):
        broker({"arr/sonarr": provider}).read(query, principal="owner")
    assert upstream.calls == []
    enabled = ResourceQueryBroker({"arr/sonarr": provider}, frozenset({"arr/sonarr"}), b"k" * 32,
                                  disclosure_approved=True, sonarr_diagnostics=True)
    response = enabled.read(query, principal="owner")
    assert response["items"] == [{"id": 18, "series_id": 7, "episode_id": 25, "status": "completed",
                                  "download_status": "warning", "download_state": "importPending",
                                  "reasons": ["permission"]}]
    assert "PRIVATE" not in json.dumps(response)
    assert response["effect_performed"] is False
    assert upstream.calls[0].full_url.endswith("/api/v3/queue?page=1&pageSize=200&includeUnknownSeriesItems=true")
    warnings = upstream.rows["records"][0]["statusMessages"][0]["messages"]
    warnings[:] = ["Not a custom format upgrade for existing episode file",
                   "Episode 4 was not found in the downloaded release"]
    assert enabled.read(query, principal="owner")["items"][0]["reasons"] == ["quality", "release_episode_missing"]
    upstream.rows["totalRecords"] = 2
    with pytest.raises(QueryUnavailable):
        enabled.read(query, principal="owner")


def test_sonarr_series_detail_selects_one_exact_id():
    provider, upstream = api("sonarr", [
        {"id": 17, "monitored": True, "qualityProfileId": 7, "status": "continuing",
         "statistics": {"episodeCount": 25, "episodeFileCount": 20, "seasonCount": 3}, "title": "PRIVATE"},
        {"id": 19, "monitored": False, "qualityProfileId": 3},
    ])
    active = ResourceQueryBroker({"arr/sonarr": provider}, frozenset({"arr/sonarr"}), b"k" * 32,
                                 disclosure_approved=True, sonarr_diagnostics=True)
    answer = active.read(typed("sonarr", view="series", target_id=17), principal="owner")
    assert answer["items"] == [{"id": 17, "monitored": True, "quality_profile_id": 7, "status": "continuing",
                                "episode_count": 25, "episode_file_count": 20, "season_count": 3}]
    assert answer["next_cursor"] is None
    assert active.read(typed("sonarr", view="series", target_id=20), principal="owner")["items"] == []
    for bad in (typed("sonarr", view="series"), typed("sonarr", view="series", target_id=True),
                typed("sonarr", view="series", limit=2, target_id=17),
                typed("sonarr", view="series", target_id=17, cursor="abc"),
                typed("sonarr", view="queue", target_id=17), typed("radarr", view="health")):
        with pytest.raises(QueryUnavailable):
            active.read(bad, principal="owner")
    assert all(call.full_url.endswith("/api/v3/series") for call in upstream.calls)


def test_health_view_filters_messages_and_only_discloses_the_granted_scope():
    from pathlib import Path

    provider, _ = api("sonarr", [{"source": "UpdateCheck", "type": "warning", "message": "PRIVATE"},
                                 {"source": "PRIVATE", "type": "PRIVATE", "message": "PRIVATE"}])

    def legacy(_):
        return {}

    legacy.query_broker = ResourceQueryBroker({"arr/sonarr": provider}, frozenset({"arr/sonarr"}), b"k" * 32,
                                             disclosure_approved=True, sonarr_diagnostics=True)
    registry = build_registry(Path("."), lambda: None, Redactor(), media_resource_provider=legacy)
    cap, args = registry.resolve("media.resource_read", typed("sonarr", view="health", limit=2))
    assert "arr/radarr" not in cap.argument_schema["properties"]["resource_id"]["enum"]
    assert "health" in cap.argument_schema["properties"]["view"]["enum"]
    result = cap.handler(args)
    assert result["items"] == [{"id": 1, "check": "other", "severity": "unknown"},
                               {"id": 2, "check": "update", "severity": "warning"}]
    assert "PRIVATE" not in json.dumps(result)


def test_status_only_seerr_and_radarr_join_use_exact_identity_without_titles():
    request = {"id": 17, "type": "movie", "is4k": True, "status": 2,
               "media": {"tmdbId": 111, "status": 5, "status4k": 3},
               "createdAt": "2026-11-21T20:00:00.123Z", "requestedBy": {"email": "PRIVATE"}}
    seerr, api_calls = api("seerr", {"pageInfo": {"results": 1}, "results": [request]})
    radarr, movie_calls = api("radarr", [{**radarr_rows()[0], "tmdbId": 111, "status": "released"}])
    providers = {"arr/seerr": seerr, "arr/radarr": radarr}
    with pytest.raises(QueryUnavailable):
        broker(providers).read(typed("seerr", view="request", target_id=17), principal="owner")
    assert not api_calls.calls
    active = ResourceQueryBroker(providers, frozenset(providers), b"k" * 32, disclosure_approved=True,
                                 view_grants={"arr/seerr": frozenset({"request", "requests"}),
                                              "arr/radarr": frozenset({"queue", "movie"})})
    with pytest.raises(QueryUnavailable):
        active.read(typed("radarr", view="items"), principal="owner")
    assert not movie_calls.calls
    a = active.read(typed("seerr", view="request", target_id=17), principal="owner")
    assert a["items"] == [{"id": 17, "tmdb_id": 111, "media_type": "movie", "request_status": "approved",
                           "media_status": "processing", "is_4k": True, "created_at": "2026-11-21T20:00:00Z",
                           "updated_at": None}]
    b = active.read(typed("radarr", view="movie", target_id=111), principal="owner")
    assert b["items"] == [{"id": 9, "tmdb_id": 111, "monitored": False, "has_file": False,
                           "quality_profile_id": 3, "status": "released"}]
    assert all("title" not in item for item in a["items"] + b["items"])
    assert "PRIVATE" not in json.dumps(a)
    assert api_calls.calls[0].full_url.endswith("/api/v1/request?take=100&skip=0&sort=added")
    assert movie_calls.calls[0].full_url.endswith("/api/v3/movie")


def test_request_status_rejects_incomplete_or_ambiguous_data():
    seerr, _ = api("seerr", {"pageInfo": {"results": 101}, "results": []})
    radarr, _ = api("radarr", [{**row, "tmdbId": 123} for row in radarr_rows()])
    providers = {"arr/seerr": seerr, "arr/radarr": radarr}
    active = ResourceQueryBroker(providers, frozenset(providers), b"k" * 32, disclosure_approved=True,
                                 view_grants={"arr/seerr": frozenset({"requests"}), "arr/radarr": frozenset({"movie"})})
    for requested in (typed("seerr", view="requests"), typed("radarr", view="movie", target_id=123)):
        with pytest.raises(QueryUnavailable):
            active.read(requested, principal="owner")


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
    # The selector is registered for the typed broker, but the Sonarr provider
    # and independent resource grant are absent: fail closed before any API call.
    cap, args = registry.resolve("media.resource_read", typed("sonarr"))
    with pytest.raises(CapabilityFailed):
        cap.handler(args)


def test_diagnostic_followups_join_named_seerr_request_radarr_and_queue():
    seerr_row = {"id": 18, "type": "movie", "is4k": False, "status": 2, "profileId": 4,
                 "serverId": 0, "profileName": "HD", "media": {"tmdbId": 22, "status": 3},
                 "requestedBy": {"email": "private@example.invalid", "plexToken": "secret-token"}}
    queue_row = {"id": 3, "movieId": 2, "status": "completed", "trackedDownloadStatus": "warning",
                 "trackedDownloadState": "importPending", "errorMessage": "Permission denied at /private/path"}
    seerr_rows = {"pageInfo": {"results": 1}, "results": [seerr_row], "serviceErrors": {
        "radarr": [{"id": 0, "name": "private-server-name"}], "sonarr": []}}
    routes = {
        "/api/v1/request?take=100&skip=0&sort=added": seerr_rows,
        "/api/v1/movie/22": {"id": 22, "title": "Étoile", "apiKey": "secret-token"},
        "/api/v3/movie": radarr_rows(),
        "/api/v3/queue?page=1&pageSize=200&includeUnknownMovieItems=true": {"totalRecords": 1, "records": [queue_row]},
    }

    def open_get(request, timeout):
        assert request.get_method() == "GET"
        return Response(routes[request.selector], request.full_url)

    providers = {f"arr/{name}": ARRReadProvider(name, f"http://{name}:1234", "local-test-key",
                                              opener=open_get, media_diagnostics=True)
                 for name in ("radarr", "seerr")}
    enabled = ResourceQueryBroker(providers, frozenset(providers), b"k" * 32, disclosure_approved=True,
                                  media_diagnostics=frozenset(providers))
    matches = enabled.read(typed("seerr", "requests", title="E\u0301TOILE"), principal="owner")
    assert matches["matching_count"] == 1 and matches["title_missing_count"] == 0
    request = matches["items"][0]
    assert request["title"] == "Étoile" and request["profile_name"] == "HD"
    identified = enabled.read(typed("radarr", "movie", target_id=request["tmdb_id"]), principal="owner")["items"][0]
    assert identified["id"] == 2 and identified["quality_profile_id"] == request["profile_id"]
    queue = enabled.read(typed("radarr", "queue", related_id=identified["id"]), principal="owner")
    assert queue["items"][0]["reasons"] == ["permission"]
    assert enabled.read(typed("radarr", "queue", related_id=99), principal="owner")["items"] == []
    health = enabled.read(typed("seerr", "health"), principal="owner")["items"]
    assert health == [{"id": 1, "service": "radarr", "service_id": 0, "severity": "error",
                       "check": "profile_lookup_failed"}]
    result = json.dumps([matches, queue, health])
    for private in ("private@example.invalid", "/private/path", "secret-token", "private-server-name"):
        assert private not in result
    limited = ResourceQueryBroker(providers, frozenset(providers), b"k" * 32, disclosure_approved=True,
                                  view_grants={"arr/radarr": frozenset({"queue"}), "arr/seerr": frozenset({"requests"})})
    assert "reasons" not in limited.read(typed("radarr", "queue"), principal="owner")["items"][0]
    assert "title" not in limited.read(typed("seerr", "requests"), principal="owner")["items"][0]
    with pytest.raises(QueryUnavailable):
        limited.read(typed("seerr", "requests", title="Étoile"), principal="owner")
    with pytest.raises(QueryUnavailable):
        limited.read(typed("seerr", "health"), principal="owner")


def test_filtered_cursor_cannot_be_reused_for_another_selector():
    provider, _ = api("radarr", radarr_rows())
    active = broker({"arr/radarr": provider})
    page = active.read(typed("radarr", title="Film"), principal="owner")
    cursor = page["next_cursor"]
    assert page["matching_count"] == 2 and cursor
    assert active.read(typed("radarr", title="Film", cursor=cursor), principal="owner")["items"][0]["id"] == 9
    for filters in ({}, {"title": "Film A"}):
        with pytest.raises(QueryUnavailable):
            active.read(typed("radarr", cursor=cursor, **filters), principal="owner")


def test_seerr_request_collection_follows_complete_pages_with_limited_title_lookups():
    rows = [{"id": item_id, "type": "movie", "is4k": False, "status": 4,
             "media": {"tmdbId": item_id, "status": 2}} for item_id in range(101, 0, -1)]
    calls = []

    def open_get(request, timeout):
        calls.append(request.selector)
        if request.selector.startswith("/api/v1/request?"):
            page = rows[:100] if "skip=0" in request.selector else rows[100:]
            return Response({"pageInfo": {"results": 101}, "results": page}, request.full_url)
        return Response({}, request.full_url, status=404)

    provider = ARRReadProvider("seerr", "http://seerr:5055", "local-test-key",
                               opener=open_get, media_diagnostics=True)
    active = ResourceQueryBroker({"arr/seerr": provider}, frozenset({"arr/seerr"}), b"k" * 32,
                                 disclosure_approved=True, media_diagnostics=frozenset({"arr/seerr"}))
    result = active.read(typed("seerr", "requests", title="Unresolved", request_status="failed"), principal="owner")
    assert result["items"] == [] and result["title_missing_count"] == 101
    assert "skip=100" in calls[1] and len(calls) == 26
    rows.pop()
    with pytest.raises(ARRSourceUnavailable, match="arr_source_unavailable"):
        provider("requests")
