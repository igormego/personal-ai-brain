"""Offline-only tests for trusted storage inventory and generic resource route."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.control_worker import Worker
from personal_ai_brain.host_storage import (
    GIB, LSBLK, VG_FLAGS, RESOURCE_ID, StorageUnavailable, collect_snapshot,
    publish_snapshot, read_snapshot, validate_snapshot,
)
from personal_ai_brain.github_issues import TITLE


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _node(name, kind, size, *, fs=None, bus=None, mounts=None, children=None):
    data = {"name": name, "type": kind, "size": size,
            "tran": bus, "fstype": fs, "mountpoints": mounts or [None]}
    if children is not None:
        data["children"] = children
    return data


def _sample():
    system = _node("PRIVATE_SYSTEM", "disk", 1_000_000_000_000, bus="sata",
                   children=[
                       _node("efi", "part", 512_000_000, fs="vfat", mounts=["/boot/efi"]),
                       _node("lvm", "part", 200 * GIB, fs="LVM2_member",
                             children=[_node("ubuntu-lv", "lvm", 95 * GIB,
                                             fs="ext4", mounts=["/"])])])
    data = _node("PRIVATE_DATA", "disk", 18_000_000_000_000, bus="sata",
                 children=[_node("data-part", "part", 17_999_000_000_000,
                                 fs="ext4", mounts=["/data"])])
    usb = _node("PRIVATE_USB", "disk", 2_000_000_000_000, bus="usb",
                children=[_node("usb-part", "part", 1_999_000_000_000, fs="ntfs")])
    return {"blockdevices": [system, data, usb]}


def _packet():
    return collect_snapshot(
        now=NOW, runner=lambda _: json.dumps(_sample()).encode(),
        free_gib=lambda path: 72 if path == "/" else 11762,
        data_mounted=True,
    )


def test_fixed_lsblk_read_and_sanitized_both_physical_drives():
    calls = []

    def run(argv):
        calls.append(argv)
        if argv == VG_FLAGS:
            return json.dumps({"report": [{"vg": [{
                "vg_size": str(900 * GIB), "vg_free": str(800 * GIB),
            }]}]}).encode()
        return json.dumps(_sample()).encode()

    value = collect_snapshot(now=NOW, runner=run,
                             free_gib=lambda path: 72 if path == "/" else 11762,
                             data_mounted=True)
    assert calls == [[LSBLK, "-J", "-b", "-o",
                      "NAME,TYPE,SIZE,TRAN,FSTYPE,MOUNTPOINTS"], VG_FLAGS]
    assert len(value["devices"]) == 3
    assert value["devices"][0]["roles"] == ["root"]
    assert value["devices"][1]["roles"] == ["data"]
    assert value["devices"][2]["roles"] == []
    assert value["devices"][2]["transport"] == "usb"
    assert value["devices"][2]["volumes"][0]["filesystem"] == "ntfs"
    assert value["root_free_gib"] == 72
    assert value["schema_version"] == "0.2"
    assert value["lvm"] == {
        "status": "available", "volume_groups": [
            {"size_gib": 900, "free_gib": 800},
        ], "mapping": "unmapped",
    }
    assert value["data_free_gib"] == 11762
    assert value["devices"][0]["unpartitioned_gib_estimate"] > 600
    assert value["contents_inspected"] is False
    result = json.dumps(value)
    for secret in ("PRIVATE_", "ubuntu-lv", "efi", "data-part", "/boot", "/data",
                   "/dev", "serial", "uuid", "password"):
        assert secret not in result


def test_unmounted_data_does_not_make_up_capacity():
    value = collect_snapshot(now=NOW, runner=lambda _: json.dumps(_sample()).encode(),
                             free_gib=lambda _: 72, data_mounted=False)
    assert value["data_free_gib"] is None
    assert validate_snapshot(value, now=NOW)["freshness"] == "current"


@pytest.mark.parametrize("raw", [
    b"not-json",
    b'{"blockdevices":[]}',
    b'{"blockdevices":[{"type":"disk","size":true,"mountpoints":[null]}]}',
    b'{"blockdevices":[{"type":"disk","size":1000,"mountpoints":["/home"],"children":[{"type":"part","size":3000,"mountpoints":[null]}]}]}',
    b'{"blockdevices":[],"secret":"leak"}',
])
def test_bad_probe_fails_closed(raw):
    with pytest.raises(StorageUnavailable, match="^storage_unavailable$"):
        collect_snapshot(now=NOW, runner=lambda _: raw,
                         free_gib=lambda _: 72, data_mounted=False)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(secret="raw key"),
    lambda v: v.update(contents_inspected=True),
    lambda v: v.update(root_free_gib=True),
    lambda v: v["devices"][0].update(serial="secret"),
    lambda v: v["devices"][0].update(roles=["data", "root"]),
    lambda v: v["devices"][1]["volumes"][0].update(filesystem="/etc/passwd"),
    lambda v: v["devices"][0].update(disk_index=999),
])
def test_strict_fields_and_host_identifiers_rejected(mutate):
    value = _packet()
    mutate(value)
    with pytest.raises(StorageUnavailable):
        validate_snapshot(value, now=NOW)


def test_immutable_private_snapshot_and_expiry(tmp_path):
    path = tmp_path / "storage.json"
    publish_snapshot(path, _packet())
    assert read_snapshot(path, now=NOW)["source"] == (
        "operator_storage_snapshot_not_live_probe")
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".storage-audit-*"))
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW + timedelta(minutes=31))
    symlink = tmp_path / "alias.json"
    symlink.symlink_to(path)
    with pytest.raises(StorageUnavailable):
        read_snapshot(symlink, now=NOW)
    path.chmod(0o666)
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW)


def test_duplicate_keys_and_unsigned_source_are_rejected(tmp_path):
    path = tmp_path / "storage.json"
    path.write_text('{"schema_version":"0.1","schema_version":"0.1"}')
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW)
    path.write_text("X" * 8193)
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW)


def test_generic_resource_is_opt_in_and_strict(tmp_path):
    path = tmp_path / "storage.json"
    publish_snapshot(path, _packet())
    disabled = build_registry(tmp_path, None, Redactor())
    with pytest.raises(Rejected, match="unknown_capability"):
        disabled.resolve("homelab.resource_read", {"resource_id": RESOURCE_ID})
    active = build_registry(tmp_path, None, Redactor(), host_storage_snapshot=path)
    cap, arguments = active.resolve("homelab.resource_read", {"resource_id": RESOURCE_ID})
    assert cap.risk_level == "read_only" and cap.side_effect_class == "none"
    assert cap.handler(arguments)["devices"][1]["roles"] == ["data"]
    for request in ({}, {"resource_id": "storage/contents"},
                    {"resource_id": RESOURCE_ID, "path": "/etc/shadow"},
                    {"resource_id": RESOURCE_ID, "command": "mount -o rw"},
                    {"resource_id": 1}):
        with pytest.raises(Rejected, match="invalid_host_resource"):
            active.resolve("homelab.resource_read", request)


class FakeTransport:
    def __init__(self, bodies_and_authors):
        self.issues = {i: {"number": i, "title": TITLE, "body": body, "state": "open",
                           "user": author}
                       for i, (body, author) in enumerate(bodies_and_authors, 1)}
        self.comments = []

    def list_page(self, page):
        return [item for item in self.issues.values() if item["state"] == "open"]

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, result):
        self.comments.append((number, result))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"


def test_persistent_private_resource_requires_actual_issue_author(tmp_path):
    snapshot = tmp_path / "storage.json"
    publish_snapshot(snapshot, _packet())
    registry = build_registry(tmp_path, None, Redactor(),
                              host_storage_snapshot=snapshot)

    def request():
        return json.dumps({
            "schema_version": "0.1", "request_id": str(uuid4()),
            "capability": "homelab.resource_read",
            "arguments": {"resource_id": RESOURCE_ID},
            "requested_by": "igormego", "requested_at": NOW.isoformat(),
        })

    transport = FakeTransport([
        (request(), {"id": 42, "login": "igormego"}),
        (request(), {"id": 55916200, "login": "imposter"}),
        (request(), {"id": 55916200, "login": "igormego"}),
    ])
    Worker(tmp_path / "ledger", transport, registry, Redactor()).tick()
    assert transport.comments[0][1]["status"] == "request_rejected"
    assert transport.comments[1][1]["status"] == "request_rejected"
    assert transport.comments[2][1]["status"] == "capability_completed"
    assert transport.comments[2][1]["result"]["contents_inspected"] is False


def test_lvm_unavailable_is_unknown_never_guessed():
    commands = []
    def run(argv):
        commands.append(argv)
        if argv == VG_FLAGS:
            raise OSError("not permitted")
        return json.dumps(_sample()).encode()
    packet = collect_snapshot(now=NOW, runner=run,
                              free_gib=lambda _: 72, data_mounted=False)
    assert packet["lvm"] == {
        "status": "unavailable", "volume_groups": [], "mapping": "unmapped",
    }
    assert commands[-1] == VG_FLAGS


@pytest.mark.parametrize("vg_doc", [
    {"report": [{"vg": [{"vg_size": "123.00", "vg_free": "999.00"}]}]},
    {"report": [{"vg": [{"vg_size": "123", "vg_free": "12",
                          "vg_name": "PRIVATE_VG_NAME"}]}]},
    {"report": [{"vg": [{"vg_size": "-123", "vg_free": "0"}]}]},
    {"report": [{"vg": [{"vg_size": True, "vg_free": "0"}]}]},
    {"report": [{"vg": [{"vg_size": "0", "vg_free": "nan"}]}]},
    {"report": [{"vg": [{"vg_size": "1e12", "vg_free": "100"}]}]},
    {"report": [{"vg": [{"vg_size": "123.0TB", "vg_free": "0"}]}]},
    {"report": [{"vg": []}, {"vg": []}]},
])
def test_invalid_lvm_source_never_leaks_and_never_estimates(vg_doc):
    def run(argv):
        if argv == VG_FLAGS:
            return json.dumps(vg_doc).encode()
        return json.dumps(_sample()).encode()
    packet = collect_snapshot(now=NOW, runner=run,
                              free_gib=lambda _: 72, data_mounted=False)
    assert packet["lvm"]["status"] == "unavailable"
    assert "PRIVATE_VG_NAME" not in json.dumps(packet)


def test_group_readable_snapshot_needs_setgid_parent(tmp_path):
    packet = _packet()
    path = tmp_path / "storage.json"
    with pytest.raises(StorageUnavailable):
        publish_snapshot(path, packet, group_readable=True)
    tmp_path.chmod(0o2750)
    publish_snapshot(path, packet, group_readable=True)
    assert path.stat().st_mode & 0o777 == 0o640
    assert path.stat().st_gid == tmp_path.stat().st_gid
    assert read_snapshot(path, now=NOW)["lvm"]["status"] == "unavailable"


def test_group_read_authorization_and_world_read_denied(monkeypatch, tmp_path):
    import personal_ai_brain.host_storage as storage
    tmp_path.chmod(0o2750)
    path = tmp_path / "storage.json"
    publish_snapshot(path, _packet(), group_readable=True)
    owner = path.stat().st_uid
    wrong_uid = owner + 9230
    monkeypatch.setattr(storage.os, "geteuid", lambda: wrong_uid)
    monkeypatch.setattr(storage.os, "getegid", lambda: path.stat().st_gid)
    monkeypatch.setattr(storage.os, "getgroups", lambda: [])
    assert read_snapshot(path, now=NOW)["schema_version"] == "0.2"
    monkeypatch.setattr(storage.os, "getegid", lambda: path.stat().st_gid + 9230)
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW)
    path.chmod(0o644)
    monkeypatch.setattr(storage.os, "geteuid", lambda: owner)
    with pytest.raises(StorageUnavailable):
        read_snapshot(path, now=NOW)


def test_unprivileged_cli_collects_with_fixed_operator_output(monkeypatch, tmp_path):
    import sys
    import personal_ai_brain.host_storage as storage
    path = tmp_path / "storage.json"
    tmp_path.chmod(0o2750)
    monkeypatch.setattr(storage, "collect_snapshot", _packet)
    monkeypatch.setattr(sys, "argv",
                        ["storage-collector", "collect", "--output", str(path),
                         "--reader-group"])
    monkeypatch.setattr(storage.os, "geteuid", lambda: 10001)
    # The CLI translates the refused publisher into a fixed safe return code.
    assert storage.main() == 1
    assert not path.exists()
    monkeypatch.setattr(storage.os, "geteuid", lambda: tmp_path.stat().st_uid)
    assert storage.main() == 0
    assert read_snapshot(path, now=NOW)["resource_id"] == RESOURCE_ID
