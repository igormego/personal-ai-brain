"""Source-only supervised storage refresh guard; never invoke systemd or host tools."""
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "ops" / "systemd"


def _unit(name):
    content = (ROOT / name).read_text(encoding="utf-8")
    parser = ConfigParser(interpolation=None, strict=True)
    parser.read_string(content)
    return content, parser


def test_service_rootless_fixed_read_only_publisher():
    raw, unit = _unit("pab-storage-refresh.service.in")
    assert set(unit.sections()) == {"Unit", "Service"}
    service = unit["Service"]
    assert service["Type"] == "oneshot"
    assert service["User"] == "@APPROVED_STORAGE_COLLECTOR_USER@"
    assert service["Group"] == "@APPROVED_STORAGE_READER_GROUP@"
    assert service["ExecStart"] == (
        "@APPROVED_PYTHON@ -I -B /run/pab-storage-collector.py collect "
        "--output /run/pab-storage-output/storage.json --reader-group")
    assert service["BindReadOnlyPaths"] == (
        "@PINNED_STORAGE_COLLECTOR@:/run/pab-storage-collector.py")
    assert service["BindPaths"] == (
        "@OPERATOR_STORAGE_SNAPSHOT_DIR@:/run/pab-storage-output")
    assert service["ReadWritePaths"] == "/run/pab-storage-output"
    assert service["NoNewPrivileges"] == "yes"
    assert service["ProtectHome"] == "yes"
    assert service["ProtectSystem"] == "strict"
    assert service["PrivateDevices"] == "yes"
    assert service["RestrictAddressFamilies"] == "AF_UNIX"
    assert service["CapabilityBoundingSet"] == ""
    assert service["AmbientCapabilities"] == ""
    assert service["UMask"] == "0027"
    assert "Install" not in unit
    for forbidden in ("docker.sock", "sudo ", "systemctl enable", "ssh ",
                      "PAB_GITHUB_TOKEN", "ExecStartPre", "ExecStartPost"):
        assert forbidden not in raw
    assert "@APPROVED_" in raw


def test_timer_source_only_and_never_automatic_deployment():
    raw, unit = _unit("pab-storage-refresh.timer")
    assert set(unit.sections()) == {"Unit", "Timer", "Install"}
    assert unit["Timer"]["Unit"] == "pab-storage-refresh.service"
    assert unit["Timer"]["OnBootSec"] == "2min"
    assert unit["Timer"]["OnUnitInactiveSec"] == "10min"
    assert unit["Install"]["WantedBy"] == "timers.target"
    assert "systemctl enable" not in raw
    assert "ExecStart" not in raw
