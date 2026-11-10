from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from personal_ai_brain.control import CapabilityFailed, Redactor, build_registry
from personal_ai_brain.resource_query import QueryUnavailable, ResourceQueryBroker, validate_query

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
KEY = b"x" * 32


def snapshot():
    return {"schema_version": "0.1", "resource_id": "movies/watchlist",
            "collected_at": "2026-11-10T12:00:00Z", "complete": True, "source_status": {"movie_db": "ok"},
            "items": [{"id": i, "csfd_id": str(1000 + i), "title": "Synthetic " + str(i),
                       "year": 2020, "tmdb_id": None, "imdb_id": None,
                       "identity_status": "unresolved"} for i in range(1, 6)]}


def broker(source, *, grants=frozenset({"movies/watchlist"}), disclosure=True):
    return ResourceQueryBroker({"movies/watchlist": lambda: source}, grants, KEY,
                               disclosure_approved=disclosure)


def query(**kw):
    return {"resource_id": "movies/watchlist", "view": "items", "limit": 2, **kw}


def test_page_cursor_is_bound_to_generation_and_replays_deterministically():
    source = snapshot(); b = broker(source)
    first = b.read(query(), principal="owner", now=NOW)
    assert [x["id"] for x in first["items"]] == [1, 2]
    second = b.read(query(cursor=first["next_cursor"]), principal="owner", now=NOW)
    assert [x["id"] for x in second["items"]] == [3, 4]
    assert second == b.read(query(cursor=first["next_cursor"]), principal="owner", now=NOW)
    source["items"][0]["title"] = "Changed synthetic title"
    with pytest.raises(QueryUnavailable):
        b.read(query(cursor=first["next_cursor"]), principal="owner", now=NOW)


@pytest.mark.parametrize("bad", [
    {"resource_id": "movies/watchlist", "view": "items", "limit": True},
    {"resource_id": "movies/watchlist", "view": "items", "limit": 11},
    {"resource_id": "movies/watchlist", "view": "items", "limit": 1, "path": "/tmp/x"},
    {"resource_id": "movies/watchlist", "view": "items", "limit": 1, "sql": "SELECT"},
    {"resource_id": "movies/watchlist", "view": "items", "limit": 1, "url": "https://example.org"},
    {"resource_id": "movies/watchlist", "view": "all", "limit": 1},
    {"resource_id": "../other", "view": "items", "limit": 1},
])
def test_query_rejects_unregistered_or_unbounded_inputs(bad):
    with pytest.raises(QueryUnavailable):
        validate_query(bad)


def test_private_transport_and_resource_grants_are_independent():
    source = snapshot()
    for b in (broker(source, grants=frozenset()), broker(source, disclosure=False)):
        with pytest.raises(QueryUnavailable):
            b.read(query(), principal="owner", now=NOW)
    with pytest.raises(QueryUnavailable):
        broker(source).read(query(), principal="other", now=NOW)


def test_forged_cursor_wrong_limit_and_stale_snapshot_denied():
    source = snapshot(); b = broker(source)
    token = b.read(query(), principal="owner", now=NOW)["next_cursor"]
    with pytest.raises(QueryUnavailable):
        b.read(query(cursor=token[:-1] + ("A" if token[-1] != "A" else "B")), principal="owner", now=NOW)
    with pytest.raises(QueryUnavailable):
        b.read(query(limit=1, cursor=token), principal="owner", now=NOW)
    with pytest.raises(QueryUnavailable):
        b.read(query(), principal="owner", now=NOW + timedelta(minutes=31))


@pytest.mark.parametrize("mutation", [
    lambda s: s["items"].append(deepcopy(s["items"][0])),
    lambda s: s["items"][0].update(csfd_id=s["items"][1]["csfd_id"]),
    lambda s: s["items"][0].update(identity_status="verified"),
    lambda s: s["items"][0].update(password="fixture"),
    lambda s: s["items"][0].update(title="bad\ntext"),
    lambda s: s.update(complete=False),
])
def test_invalid_or_tainted_source_fails_closed(mutation):
    source = snapshot(); mutation(source)
    with pytest.raises(QueryUnavailable):
        broker(source).read(query(), principal="owner", now=NOW)


def test_output_byte_limit_denies_instead_of_truncating():
    source = snapshot()
    for row in source["items"]:
        row["title"] = "ž" * 200
    with pytest.raises(QueryUnavailable):
        broker(source).read(query(limit=5), principal="owner", now=NOW)


def test_registry_has_default_deny_and_explicit_injected_query():
    from pathlib import Path
    provider = lambda _: {}
    default = build_registry(Path("."), lambda: None, Redactor(), media_resource_provider=provider)
    cap, args = default.resolve("media.resource_read", query())
    with pytest.raises(CapabilityFailed):
        cap.handler(args)
    source = snapshot()
    source["collected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = build_registry(Path("."), lambda: None, Redactor(), media_resource_provider=provider,
                         media_query_broker=broker(source))
    cap, args = reg.resolve("media.resource_read", query())
    assert len(cap.handler(args)["items"]) == 2


def test_worker_redactor_still_applies_to_private_titles():
    source = snapshot()
    source["items"][0]["title"] = "token=example-secret"
    page = broker(source).read(query(), principal="owner", now=NOW)
    cleaned = Redactor().clean(page)
    assert "example-secret" not in cleaned["items"][0]["title"]


def test_cursor_cannot_cross_signing_keys():
    source = snapshot()
    token = broker(source).read(query(), principal="owner", now=NOW)["next_cursor"]
    other = ResourceQueryBroker({"movies/watchlist": lambda: source},
                                frozenset({"movies/watchlist"}), b"y" * 32,
                                disclosure_approved=True)
    with pytest.raises(QueryUnavailable):
        other.read(query(cursor=token), principal="owner", now=NOW)


def test_no_source_proof_or_duplicate_canonical_id():
    source = snapshot(); source["source_status"] = {}
    with pytest.raises(QueryUnavailable):
        broker(source).read(query(), principal="owner", now=NOW)
    source = snapshot()
    source["items"][0]["tmdb_id"] = 42
    source["items"][1]["tmdb_id"] = 42
    with pytest.raises(QueryUnavailable):
        broker(source).read(query(), principal="owner", now=NOW)


@pytest.mark.skipif(os.name != "posix", reason="private snapshot requires POSIX file permissions")
def test_fixed_snapshot_provider_rejects_symlink_and_unsafe_mode(tmp_path, monkeypatch):
    from personal_ai_brain import media_snapshot
    from personal_ai_brain.assistant_media_resources import MediaResourceUnavailable
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(snapshot()), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(media_snapshot, "MOUNTED_WATCHLIST_SNAPSHOT_PATH", path)
    assert media_snapshot.read_mounted_watchlist_items()["resource_id"] == "movies/watchlist"
    path.chmod(0o644)
    with pytest.raises(MediaResourceUnavailable):
        media_snapshot.read_mounted_watchlist_items()
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    monkeypatch.setattr(media_snapshot, "MOUNTED_WATCHLIST_SNAPSHOT_PATH", link)
    with pytest.raises(MediaResourceUnavailable):
        media_snapshot.read_mounted_watchlist_items()
