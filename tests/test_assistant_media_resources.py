"""Source-only read contract for a future Sonarr operator-provided resource.

No real HTTP, credentials, worker deploy, or library mutation in these tests.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from personal_ai_brain.assistant_media_resources import (
    MediaResourceUnavailable, read_media_resource,
    sanitize_media_resource, validate_media_selector,
)
from personal_ai_brain.control import CapabilityFailed, Redactor, Rejected, build_registry


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def sample():
    return {
        "schema_version": "0.1",
        "resource_id": "sonarr/overview",
        "collected_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "series": {"total": 10, "monitored": 8, "unmonitored": 2},
        "episodes": {"total": 500, "have": 480, "missing": 12},
        "queue": {"total": 2, "warning": 1, "error": 0},
        "health": {"warnings": 1, "errors": 0},
        "findings": [
            {"code": "missing_episodes", "series_id": 42, "count": 12},
            {"code": "queue_warning", "series_id": None, "count": 1},
        ],
        "findings_truncated": False,
    }


def test_normalized_read_contains_no_raw_names_paths_or_mutations():
    result = sanitize_media_resource("sonarr/overview", sample(), now=NOW)
    assert result["resource_id"] == "sonarr/overview"
    assert result["series"]["monitored"] == 8
    assert result["episodes"]["missing"] == 12
    assert result["source"] == "operator_media_snapshot_not_live_probe"
    assert result["effect_performed"] is False
    assert "path" not in str(result).lower()
    assert result["freshness"] == "current"


@pytest.mark.parametrize("bad_selector", [
    {}, {"resource_id": "sonarr/overview", "path": "/etc/passwd"},
    {"resource_id": "sonarr/episodes?series_id=1"},
    {"resource_id": "http://localhost:8989/api/v3/series"},
    {"resource_id": "sonarr/../etc"}, {"resource_id": 1},
    {"resource_id": "sonarr/overview;DELETE"}, {"resource_id": "radarr/overview"},
])
def test_no_arbitrary_resource_or_endpoint(bad_selector):
    with pytest.raises(MediaResourceUnavailable):
        validate_media_selector(bad_selector)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="raw-secret"),
    lambda p: p.update(complete=False),
    lambda p: p.update(collected_at="malformed"),
    lambda p: p.update(collected_at=(NOW-timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    lambda p: p.update(collected_at=(NOW+timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    lambda p: p.update(episodes={"total": 10, "have": 9, "missing": 9}),
    lambda p: p.update(series={"total": 4, "monitored": 4, "unmonitored": 4}),
    lambda p: p.update(queue={"total": 0, "warning": 1, "error": 0}),
    lambda p: p["findings"].append({"code": "remote_shell", "series_id": 1, "count": 1}),
    lambda p: p["findings"].append({"code": "missing_episodes", "series_id": 42, "count": 1}),
    lambda p: p["findings"].append({"code": "queue_error", "series_id": 99, "count": 1}),
    lambda p: p["findings"].append({"code": "health_error", "series_id": None, "count": True}),
    lambda p: p.update(findings_truncated="no"),
    lambda p: p.update(health={"warnings": -1, "errors": 0}),
])
def test_invalid_or_stale_provider_facts_fail_closed(mutation):
    packet = deepcopy(sample())
    mutation(packet)
    with pytest.raises(MediaResourceUnavailable):
        sanitize_media_resource("sonarr/overview", packet, now=NOW)


def test_findings_are_capped():
    packet = sample()
    packet["findings"] = [
        {"code": "missing_episodes", "series_id": i+1, "count": 1}
        for i in range(13)
    ]
    packet["findings_truncated"] = True
    with pytest.raises(MediaResourceUnavailable):
        sanitize_media_resource("sonarr/overview", packet, now=NOW)


def test_registry_capability_is_absent_by_default(tmp_path):
    registry = build_registry(tmp_path, None, Redactor())
    with pytest.raises(Rejected, match="unknown_capability"):
        registry.resolve("media.resource_read", {"resource_id": "sonarr/overview"})


def test_registry_opt_in_validates_before_access_and_sanitizes(tmp_path):
    calls = []

    def provider(resource_id):
        calls.append(resource_id)
        return sample()

    registry = build_registry(tmp_path, None, Redactor(), media_resource_provider=provider)
    capability, selector = registry.resolve(
        "media.resource_read", {"resource_id": "sonarr/overview"}
    )
    assert capability.risk_level == "read_only"
    assert capability.side_effect_class == "none"
    assert selector == {"resource_id": "sonarr/overview"}
    result = capability.handler(selector)
    assert result["health"]["warnings"] == 1
    assert calls == ["sonarr/overview"]
    for invalid in (
        {}, {"resource_id": "http://evil"}, {"resource_id": "sonarr/overview", "api_key": "secret"},
    ):
        with pytest.raises(Rejected, match="invalid_media_resource"):
            registry.resolve("media.resource_read", invalid)
    assert calls == ["sonarr/overview"]


def test_unknown_source_error_never_echoes_sensitive_exception(tmp_path):
    def provider(_):
        raise RuntimeError("SONARR_API_KEY=secret host=http://private")

    cap, args = build_registry(
        tmp_path, None, Redactor(), media_resource_provider=provider
    ).resolve("media.resource_read", {"resource_id": "sonarr/overview"})
    with pytest.raises(CapabilityFailed) as error:
        cap.handler(args)
    assert str(error.value) == "media_resource_unavailable"


def test_incomplete_unknown_source_does_not_fall_back_to_zero(tmp_path):
    cap, args = build_registry(
        tmp_path, None, Redactor(), media_resource_provider=lambda _: {"series": []}
    ).resolve("media.resource_read", {"resource_id": "sonarr/overview"})
    with pytest.raises(CapabilityFailed, match="media_resource_unavailable"):
        cap.handler(args)

def movie_sample():
    return {
        "schema_version": "0.1",
        "resource_id": "movies/watchlist-overview",
        "collected_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "source_status": {"movie_db": "ok", "plex": "ok", "radarr": "ok", "seerr": "ok"},
        "catalog": {"total": 687, "watched": 561, "watchlist": 126, "overlap": 0},
        "disposition": {
            "playable": 13, "managed_or_requested": 9,
            "reviewable_missing": 94, "unresolved": 10,
        },
        "plex": {"playable_movies": 49},
    }


def test_movie_overview_aggregate_only_and_consistent():
    result = sanitize_media_resource(
        "movies/watchlist-overview", movie_sample(), now=NOW
    )
    assert result["catalog"]["watchlist"] == 126
    assert result["disposition"]["reviewable_missing"] == 94
    assert sum(result["disposition"].values()) == result["catalog"]["watchlist"]
    assert result["source"] == "operator_media_snapshot_not_live_probe"
    assert result["effect_performed"] is False
    assert all(k not in result for k in ("items", "titles", "paths", "api_key"))
    assert len(str(result)) < 4096


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(secret="private-film-title"),
    lambda p: p.update(complete=False),
    lambda p: p.update(resource_id="sonarr/overview"),
    lambda p: p.update(collected_at=(NOW - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    lambda p: p.update(collected_at=(NOW + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    lambda p: p["source_status"].update(seerr="unavailable"),
    lambda p: p["source_status"].update(extra="api_key"),
    lambda p: p["catalog"].update(total=0),
    lambda p: p["catalog"].update(watched=True),
    lambda p: p["catalog"].update(overlap=562),
    lambda p: p["catalog"].update(total=686),
    lambda p: p["catalog"].update(watchlist=200),
    lambda p: p["disposition"].update(playable=40),
    lambda p: p["disposition"].update(unresolved=-1),
    lambda p: p["disposition"].update(extra=1),
    lambda p: p["plex"].update(playable_movies=1),
    lambda p: p["plex"].update(file_path="/secrets"),
])
def test_movie_overview_bad_provenance_or_counts_fail_closed(mutation):
    packet = deepcopy(movie_sample())
    mutation(packet)
    with pytest.raises(MediaResourceUnavailable):
        sanitize_media_resource("movies/watchlist-overview", packet, now=NOW)


def test_movie_resource_registry_reuses_opt_in_read_only_capability(tmp_path):
    calls = []

    def provider(resource_id):
        calls.append(resource_id)
        return movie_sample()

    registry = build_registry(
        tmp_path, None, Redactor(), media_resource_provider=provider
    )
    cap, selected = registry.resolve(
        "media.resource_read", {"resource_id": "movies/watchlist-overview"}
    )
    assert selected == {"resource_id": "movies/watchlist-overview"}
    assert cap.risk_level == "read_only"
    assert cap.side_effect_class == "none"
    assert cap.handler(selected)["disposition"]["playable"] == 13
    assert calls == ["movies/watchlist-overview"]
    for invalid in (
        {"resource_id": "movies/watchlist-overview", "cursor": "next"},
        {"resource_id": "movies/123"},
        {"resource_id": "../etc/passwd"},
        {"resource_id": "movies/watchlist-overview", "execute": True},
    ):
        with pytest.raises(Rejected, match="invalid_media_resource"):
            registry.resolve("media.resource_read", invalid)
    assert calls == ["movies/watchlist-overview"]


def test_movie_provider_exception_fails_closed_without_exposing_secret(tmp_path):
    def provider(_):
        raise RuntimeError("api_key=private-secret /home/host/location")

    cap, selector = build_registry(
        tmp_path, None, Redactor(), media_resource_provider=provider
    ).resolve("media.resource_read", {"resource_id": "movies/watchlist-overview"})
    with pytest.raises(CapabilityFailed) as error:
        cap.handler(selector)
    assert str(error.value) == "media_resource_unavailable"
