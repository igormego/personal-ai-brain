"""Offline trust-bound publication checks; never require root in CI."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat

import pytest

from personal_ai_brain.host_audit import read_snapshot
from personal_ai_brain.work_snapshot_publish import (
    PublicationUnavailable, publish_copy,
)


def snapshot(*, minutes_ago=0, running=13):
    collected = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "schema_version": "0.1",
        "collected_at": collected.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpu_logical": 4,
        "memory_mib": {"total": 7335, "available": 4862},
        "disk_free_gib": {"root": 72, "data": 11824},
        "docker": {"status": "available", "running": running, "unhealthy": 0},
        "control": {
            "status": "available", "image_id": "sha256:" + "a" * 64,
            "read_only_rootfs": True, "privileged": False,
            "no_new_privileges": True, "docker_socket": False,
            "published_ports": 0,
        },
    }


@pytest.fixture
def paths(tmp_path):
    source_dir = tmp_path / "collector"
    source_dir.mkdir(mode=0o700)
    source_dir.chmod(0o700)
    source = source_dir / "snapshot.json"
    source.write_text(json.dumps(snapshot()))
    source.chmod(0o600)
    dest_dir = tmp_path / "builder"
    dest_dir.mkdir(mode=0o750)
    dest_dir.chmod(0o750)
    dest = dest_dir / "host-snapshot.json"
    return source, dest


def publish(source, dest):
    # In root-only production, destination ownership MUST be UID 0 and the
    # output is group-readable by a distinct Work Runner group.
    return publish_copy(
        source, collector_uid=os.geteuid(), reader_gid=os.getegid(),
        destination=dest, expected_owner=os.geteuid())


def test_atomically_publishes_only_schema_checked_fresh_snapshot(paths):
    source, dest = paths
    receipt = publish(source, dest)
    result = read_snapshot(dest)
    assert receipt["status"] == "published"
    assert len(receipt["sha256"]) == 64
    assert result["docker"]["running"] == 13
    assert result["source"] == "host_collector_snapshot_not_live_probe"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert dest.stat().st_nlink == 1
    assert dest.stat().st_uid == os.geteuid()
    assert dest.stat().st_gid == os.getegid()
    assert list(dest.parent.iterdir()) == [dest]


def test_second_atomic_replace_reads_new_generation_not_pinned_inode(paths):
    source, dest = paths
    publish(source, dest)
    first = os.stat(dest).st_ino
    source.write_text(json.dumps(snapshot(running=12)))
    source.chmod(0o600)
    publish(source, dest)
    assert os.stat(dest).st_ino != first
    assert read_snapshot(dest)["docker"]["running"] == 12
    assert list(dest.parent.iterdir()) == [dest]


def test_stale_or_untrusted_metadata_never_replaces_last_valid_generation(paths):
    source, dest = paths
    publish(source, dest)
    previous = dest.read_bytes()
    for bad in (snapshot(minutes_ago=120),
                {**snapshot(), "secret": "not allowlisted"},
                {**snapshot(), "docker": {"running": 13}}):
        source.write_text(json.dumps(bad))
        source.chmod(0o600)
        with pytest.raises(PublicationUnavailable, match="^snapshot_publication_unavailable$"):
            publish(source, dest)
        assert dest.read_bytes() == previous
        assert list(dest.parent.iterdir()) == [dest]


def test_symlink_and_wrong_permissions_fail_without_replacing(paths):
    source, dest = paths
    publish(source, dest)
    previous = dest.read_bytes()
    target = source.with_name("other.json")
    target.write_text(json.dumps(snapshot()))
    target.chmod(0o600)
    source.unlink()
    source.symlink_to(target)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert dest.read_bytes() == previous
    source.unlink()
    source.write_text(json.dumps(snapshot()))
    source.chmod(0o666)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert dest.read_bytes() == previous
    source.chmod(0o600)
    dest.parent.chmod(0o770)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert dest.read_bytes() == previous


def test_output_symlink_is_never_overwritten(paths):
    source, dest = paths
    other = dest.parent / "not-the-target"
    other.write_text("safe")
    dest.symlink_to(other)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert dest.is_symlink() and other.read_text() == "safe"


def test_wrong_collector_identity_and_source_directory_mode_are_denied(paths):
    source, dest = paths
    with pytest.raises(PublicationUnavailable):
        publish_copy(source, collector_uid=os.geteuid()+10000,
                     reader_gid=os.getegid(), destination=dest,
                     expected_owner=os.geteuid())
    source.parent.chmod(0o750)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert not dest.exists()


def test_source_filename_is_fixed_and_not_model_selectable(paths):
    source, dest = paths
    other = source.with_name("host-token.json")
    other.write_text(json.dumps(snapshot()))
    other.chmod(0o600)
    with pytest.raises(PublicationUnavailable):
        publish(other, dest)
    assert not dest.exists()


def test_source_duplicate_json_keys_fail_closed(paths):
    source, dest = paths
    source.write_text('{"schema_version":"0.1","schema_version":"0.1"}')
    source.chmod(0o600)
    with pytest.raises(PublicationUnavailable):
        publish(source, dest)
    assert not dest.exists()


def test_destination_parent_symlink_is_denied(paths):
    source, dest = paths
    symlink = dest.parent.parent / "builder-link"
    symlink.symlink_to(dest.parent, target_is_directory=True)
    with pytest.raises(PublicationUnavailable):
        publish(source, symlink / dest.name)
    assert not dest.exists()
