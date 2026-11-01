"""Fail-closed private Sonarr snapshot and existing opt-in registry seam."""

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from personal_ai_brain.assistant_media_resources import MediaResourceUnavailable
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.media_snapshot import read_media_snapshot


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
