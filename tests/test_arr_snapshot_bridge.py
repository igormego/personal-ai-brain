"""Offline ARR snapshot publication/Control disclosure security and regressions."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

import pytest

from personal_ai_brain.arr_snapshot_bridge import (
    ARRBridgeUnavailable, publish_arr, read_mounted_arr,
)
from personal_ai_brain.control import Capability, Redactor, Registry
from personal_ai_brain.control_worker import Worker
from personal_ai_brain.github_issues import TITLE
from personal_ai_brain.resource_query import ResourceQueryBroker, QueryUnavailable


def source(service="sonarr", *, view="items", ago=0, monitored=True):
    at = datetime.now(timezone.utc) - timedelta(minutes=ago)
    if view == "items":
        items = [
            {"id": 7, "title": "One Series" if service == "sonarr" else "One Film",
             "year": 2022, "monitored": monitored, "quality_profile_id": 2,
             "external_id": 110, "has_file": None if service == "sonarr" else True},
            {"id": 8, "title": "Second Series" if service == "sonarr" else "Second Film",
             "year": 2023, "monitored": True, "quality_profile_id": 2,
             "external_id": 120, "has_file": None if service == "sonarr" else False},
        ]
    else:
        items = [{"id": 2, "name": "4K with 1080p fallback"}]
    return {
        "schema_version": "0.1", "resource_id": "arr/" + service,
        "view": view, "collected_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True, "source_status": {service: "ok"}, "items": items,
    }


@pytest.fixture
def directory(tmp_path):
    output = tmp_path / "arr-audit"
    output.mkdir(mode=0o750)
    output.chmod(0o750)
    return output


def pub(dir, service="sonarr", view="items", *, data=None):
    resource_id = "arr/" + service
    payload = data or source(service, view=view)
    return publish_arr(lambda _view: payload, resource_id, view,
                       directory=dir, expected_owner=os.geteuid(),
                       reader_gid=os.getegid())


def read(dir, service="sonarr", view="items"):
    return read_mounted_arr("arr/" + service, view, directory=dir,
                            expected_owner=os.geteuid())


@pytest.mark.parametrize("service", ["sonarr", "radarr"])
def test_both_arr_families_share_one_publisher_and_query_broker(directory, service):
    pub(directory, service, "items")
    pub(directory, service, "profiles")
    query = ResourceQueryBroker(
        {"arr/" + service: lambda view: read(directory, service, view)},
        frozenset({"arr/" + service}), b"k" * 32, disclosure_approved=True,
    )
    rid = "arr/" + service
    a = query.read({"resource_id": rid, "view": "items", "limit": 1},
                   principal="owner")
    assert a["resource_id"] == rid and len(a["items"]) == 1
    assert a["items"][0]["id"] == 7
    assert a["next_cursor"]
    second = query.read({"resource_id": rid, "view": "items", "limit": 1,
                         "cursor": a["next_cursor"]}, principal="owner")
    assert second["items"][0]["id"] == 8 and second["next_cursor"] is None
    profiles = query.read({"resource_id": rid, "view": "profiles", "limit": 10},
                          principal="owner")
    assert profiles["items"] == [{"id": 2, "name": "4K with 1080p fallback"}]
    assert a["effect_performed"] is False
    with pytest.raises(QueryUnavailable):
        query.read({"resource_id": rid, "view": "items", "limit": 1}, principal="anyone")
    with pytest.raises(QueryUnavailable):
        query.read({"resource_id": "movies/watchlist", "view": "items", "limit": 1},
                   principal="owner")
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o640 for p in directory.iterdir())


def test_atomic_replace_and_expiry_keep_last_generation_but_never_expose_stale(directory):
    pub(directory)
    target = directory / "sonarr-items.json"
    before_inode = target.stat().st_ino
    before_bytes = target.read_bytes()
    pub(directory, data=source(monitored=False))
    assert target.stat().st_ino != before_inode
    assert read(directory)["items"][0]["monitored"] is False
    valid = target.read_bytes()
    for bad in [source(ago=31), {**source(), "api_key": "must-not-publish"},
                {**source(), "items": [{"id": 7, "title": "bad"}]}]:
        with pytest.raises(ARRBridgeUnavailable):
            pub(directory, data=bad)
        assert target.read_bytes() == valid
    target.write_text(json.dumps(source(ago=31)))
    target.chmod(0o640)
    with pytest.raises(ARRBridgeUnavailable):
        read(directory)
    assert before_bytes != valid
    assert list(directory.iterdir()) == [target]


def test_sources_refuse_symlinks_permission_drifts_and_unknown_paths(directory, tmp_path):
    pub(directory)
    target = directory / "sonarr-items.json"
    old = target.read_bytes()
    other = directory / "other.json"
    other.write_bytes(b"do-not-replace")
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(ARRBridgeUnavailable):
        pub(directory)
    with pytest.raises(ARRBridgeUnavailable):
        read(directory)
    assert other.read_bytes() == b"do-not-replace"
    target.unlink()
    other.unlink()
    pub(directory)
    target.chmod(0o666)
    with pytest.raises(ARRBridgeUnavailable):
        read(directory)
    with pytest.raises(ARRBridgeUnavailable):
        pub(directory)
    target.chmod(0o640)
    directory.chmod(0o770)
    with pytest.raises(ARRBridgeUnavailable):
        pub(directory)
    with pytest.raises(ARRBridgeUnavailable):
        read(directory)
    directory.chmod(0o750)
    with pytest.raises(ARRBridgeUnavailable):
        read_mounted_arr("arr/private", "items", directory=directory,
                         expected_owner=os.geteuid())
    with pytest.raises(ARRBridgeUnavailable):
        read_mounted_arr("arr/sonarr", "../host", directory=directory,
                         expected_owner=os.geteuid())


def test_published_titles_only_allowlisted_and_no_credentials_or_paths(directory):
    raw = source()
    raw["items"][0].update({"path": "/private/show", "api_key": "credential"})
    # Strict broker refuses non-sanitized extra fields; publisher cannot
    # launder upstream data into the private GitHub mailbox.
    with pytest.raises(ARRBridgeUnavailable):
        pub(directory, data=raw)
    assert not list(directory.iterdir())
    pub(directory)
    data = read(directory)
    assert set(data["items"][0]) == {
        "id", "title", "year", "monitored", "quality_profile_id",
        "external_id", "has_file",
    }


class FakeTransport:
    def __init__(self, issues):
        self.issues = {i + 1: dict(v, number=i + 1, state="open", title=TITLE)
                       for i, v in enumerate(issues)}
        self.comments = []

    def list_page(self, page):
        return list(self.issues.values()) if page == 1 else []

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, body):
        self.comments.append((number, body))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"


def request(*, author_id=None, login="igormego", requested_by="igormego",
            rid="arr/sonarr"):
    return {
        "body": json.dumps({
            "schema_version": "0.1", "request_id": str(uuid4()),
            "capability": "media.resource_read",
            "arguments": {"resource_id": rid, "view": "profiles", "limit": 1},
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }),
        "user": {"id": author_id, "login": login} if author_id else {},
    }


def test_issue_owner_check_does_not_trust_requested_by_or_other_collaborators(tmp_path):
    calls = []
    cap = Capability("media.resource_read", "read-only ARR", "read_only", {},
                     lambda q: q, lambda q: calls.append(q) or {"items": [{"name": "private"}]})
    issues = [
        request(requested_by="igormego"),
        request(author_id=42, requested_by="igormego"),
        request(author_id=55916200, login="forged-other", requested_by="igormego"),
        request(author_id=55916200, login="igormego", requested_by="arbitrary"),
    ]
    transport = FakeTransport(issues)
    worker = Worker(tmp_path, transport, Registry([cap]), Redactor())
    worker.tick()
    assert len(calls) == 1
    assert [c[1]["status"] for c in transport.comments] == [
        "request_rejected", "request_rejected",
        "request_rejected", "capability_completed",
    ]
    assert all(c[1].get("error") == "owner_auth_required"
               for c in transport.comments[:3])
    # Reject before reservation; no private result persisted for rejected IDs.
    assert len(list((tmp_path / "requests").iterdir())) == 1


def test_non_arr_media_request_preserves_existing_worker_behavior(tmp_path):
    calls = []
    cap = Capability("media.resource_read", "existing media read", "read_only", {},
                     lambda q: q, lambda q: calls.append(q) or {"count": 3})
    transport = FakeTransport([request(rid="sonarr/overview")])
    worker = Worker(tmp_path, transport, Registry([cap]), Redactor())
    worker.tick()
    assert len(calls) == 1
    assert transport.comments[0][1]["status"] == "capability_completed"
