"""Fail-closed private Sonarr snapshot and existing opt-in registry seam."""

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from personal_ai_brain.assistant_media_resources import MediaResourceUnavailable
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.media_snapshot import read_media_snapshot, read_mounted_media_snapshot
import personal_ai_brain.media_snapshot as media_module


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def packet(*, collected_at=None):
    return {
        "schema_version": "0.1",
        "resource_id": "sonarr/overview",
        "collected_at": (collected_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "series": {"total": 64, "monitored": 61, "unmonitored": 3},
        "episodes": {"total": 3196, "have": 2900, "missing": 37},
        "queue": {"total": 58, "warning": 2, "error": 0},
        "health": {"warnings": 1, "errors": 0},
        "findings": [{"code": "missing_episodes", "series_id": 24, "count": 4}],
        "findings_truncated": False,
    }


def write_private(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def test_fresh_private_snapshot_returns_only_source_fields(tmp_path):
    path = tmp_path / "snapshot.json"
    write_private(path, packet())
    result = read_media_snapshot(path, "sonarr/overview", now=NOW)
    assert result == packet()
    assert "secret" not in str(result)
    assert result["series"]["total"] == 64


def test_exact_registry_seam_filters_validated_private_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    write_private(path, packet())
    provider = lambda key: read_media_snapshot(path, key)
    reg = build_registry(tmp_path, None, Redactor(), media_resource_provider=provider)
    capability, selected = reg.resolve("media.resource_read", {"resource_id": "sonarr/overview"})
    result = capability.handler(selected)
    assert result["effect_performed"] is False
    assert result["source"] == "operator_media_snapshot_not_live_probe"
    assert result["episodes"]["missing"] == 37
    assert "api_key" not in result


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o640, 0o777])
def test_reject_excessive_snapshot_permissions(tmp_path, mode):
    path = tmp_path / "snapshot.json"
    write_private(path, packet())
    path.chmod(mode)
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)


def test_symlink_is_not_followed(tmp_path):
    original = tmp_path / "original.json"
    link = tmp_path / "snapshot.json"
    write_private(original, packet())
    link.symlink_to(original)
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(link, "sonarr/overview", now=NOW)


def test_reject_duplicate_json_keys(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(packet())[:-1] + ',"series":{}}', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)


@pytest.mark.parametrize("value", [
    b"not json", b"{}", b" " * 4097,
    json.dumps({**packet(), "api_key": "secret"}).encode("utf-8"),
    json.dumps(packet(collected_at=NOW - timedelta(minutes=31))).encode("utf-8"),
    json.dumps(packet(collected_at=NOW + timedelta(minutes=3))).encode("utf-8"),
])
def test_reject_incomplete_oversized_or_stale(tmp_path, value):
    path = tmp_path / "snapshot.json"
    path.write_bytes(value)
    path.chmod(0o600)
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)


def test_unapproved_resource_fails_closed_before_open(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(missing, "sonarr/../secrets", now=NOW)
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(missing, "http://localhost:8989", now=NOW)


def test_snapshot_missing_or_directory_fails_closed(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)
    path.mkdir()
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)


def test_no_provider_by_default(tmp_path):
    registry = build_registry(tmp_path, None, Redactor())
    with pytest.raises(Rejected, match="unknown_capability"):
        registry.resolve("media.resource_read", {"resource_id": "sonarr/overview"})


def test_reader_does_not_mutate_file(tmp_path):
    path = tmp_path / "snapshot.json"
    write_private(path, packet())
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    read_media_snapshot(path, "sonarr/overview", now=NOW)
    assert before == (path.read_bytes(), os.stat(path).st_mtime_ns)

def test_movie_overview_fixed_path_private_reader(tmp_path):
    path = tmp_path / "snapshot.json"
    packet = {
        "schema_version": "0.1",
        "resource_id": "movies/watchlist-overview",
        "collected_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "source_status": {"movie_db": "ok", "plex": "ok", "radarr": "ok", "seerr": "ok"},
        "catalog": {"total": 6, "watched": 4, "watchlist": 2, "overlap": 0},
        "disposition": {
            "playable": 1, "managed_or_requested": 0,
            "reviewable_missing": 1, "unresolved": 0,
        },
        "plex": {"playable_movies": 3},
    }
    write_private(path, packet)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert read_media_snapshot(path, "movies/watchlist-overview", now=NOW) == packet
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with pytest.raises(MediaResourceUnavailable):
        read_media_snapshot(path, "sonarr/overview", now=NOW)


def test_movie_snapshot_does_not_use_untrusted_resource_path(tmp_path):
    missing = tmp_path / "absent.json"
    for resource in ("movies/../../etc", "movies/watchlist-overview?url=x"):
        with pytest.raises(MediaResourceUnavailable):
            read_media_snapshot(missing, resource, now=NOW)


def movie_packet(*, collected_at=None):
    return {
        "schema_version": "0.1",
        "resource_id": "movies/watchlist-overview",
        "collected_at": (collected_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "source_status": {"movie_db": "ok", "plex": "ok", "radarr": "ok", "seerr": "ok"},
        "catalog": {"total": 690, "watched": 565, "watchlist": 125, "overlap": 0},
        "disposition": {
            "playable": 0, "managed_or_requested": 0,
            "reviewable_missing": 0, "unresolved": 125,
        },
        "plex": {"playable_movies": 50},
    }


def test_fixed_mounted_resources_are_isolated_and_movie_opt_in_required(tmp_path, monkeypatch):
    sonarr = tmp_path / "sonarr.json"
    movies = tmp_path / "movies.json"
    write_private(sonarr, packet())
    write_private(movies, movie_packet())
    monkeypatch.setattr(media_module, "MOUNTED_SNAPSHOT_PATH", sonarr)
    monkeypatch.setattr(media_module, "MOUNTED_MOVIES_SNAPSHOT_PATH", movies)

    assert read_mounted_media_snapshot("sonarr/overview", now=NOW)["series"]["total"] == 64
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("movies/watchlist-overview", now=NOW)
    assert read_mounted_media_snapshot(
        "movies/watchlist-overview", movies_enabled=True, now=NOW
    )["catalog"]["watchlist"] == 125
    assert read_mounted_media_snapshot(
        "sonarr/overview", movies_enabled=True, now=NOW
    )["series"]["total"] == 64

    # One resource must never read the other's fixed file.
    write_private(movies, packet())
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("movies/watchlist-overview", movies_enabled=True, now=NOW)
    write_private(movies, movie_packet())
    write_private(sonarr, movie_packet())
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("sonarr/overview", now=NOW)


@pytest.mark.parametrize("resource", [
    "movies/../secrets", "movies/watchlist-overview?path=/etc/passwd",
    "file:///etc/passwd", "radarr/overview", "", None,
])
def test_mounted_reader_denies_unknown_or_arbitrary_resource(resource, tmp_path, monkeypatch):
    monkeypatch.setattr(media_module, "MOUNTED_SNAPSHOT_PATH", tmp_path / "unreadable")
    monkeypatch.setattr(media_module, "MOUNTED_MOVIES_SNAPSHOT_PATH", tmp_path / "unreadable")
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot(resource, movies_enabled=True, now=NOW)


def test_movie_mounted_reader_stale_permissions_and_missing_are_fail_closed(tmp_path, monkeypatch):
    movies = tmp_path / "movie.json"
    monkeypatch.setattr(media_module, "MOUNTED_MOVIES_SNAPSHOT_PATH", movies)
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("movies/watchlist-overview", movies_enabled=True, now=NOW)
    write_private(movies, movie_packet(collected_at=NOW - timedelta(minutes=31)))
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("movies/watchlist-overview", movies_enabled=True, now=NOW)
    write_private(movies, movie_packet())
    movies.chmod(0o644)
    with pytest.raises(MediaResourceUnavailable):
        read_mounted_media_snapshot("movies/watchlist-overview", movies_enabled=True, now=NOW)
    movies.chmod(0o600)
    assert read_mounted_media_snapshot(
        "movies/watchlist-overview", movies_enabled=True, now=NOW
    )["disposition"]["unresolved"] == 125


def test_control_registry_mounted_provider_read_only_contract(tmp_path, monkeypatch):
    movies = tmp_path / "movie.json"
    write_private(movies, movie_packet())
    monkeypatch.setattr(media_module, "MOUNTED_MOVIES_SNAPSHOT_PATH", movies)

    provider = lambda name: read_mounted_media_snapshot(
        name, movies_enabled=True, now=NOW
    )
    registry = build_registry(tmp_path, None, Redactor(), media_resource_provider=provider)
    capability, selector = registry.resolve(
        "media.resource_read", {"resource_id": "movies/watchlist-overview"}
    )
    assert capability.risk_level == "read_only"
    assert capability.side_effect_class == "none"
    result = capability.handler(selector)
    assert result["catalog"]["watchlist"] == 125
    assert result["disposition"]["unresolved"] == 125
    assert result["effect_performed"] is False
    assert "title" not in str(result).lower()
