"""Source-only systemd audit timer checks; never call systemctl, Docker or SSH."""

from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "ops" / "systemd"


def unit(filename: str):
    raw = (ROOT / filename).read_text(encoding="utf-8")
    parser = ConfigParser(interpolation=None, strict=True)
    parser.read_string(raw)
    return raw, parser


def test_collector_template_requires_explicit_host_operator_rendering():
    raw, settings = unit("pab-host-audit-refresh.service.in")
    assert raw.count("@APPROVED_COLLECTOR_USER@") >= 1
    assert raw.count("@APPROVED_PYTHON@") >= 1
    assert "@PINNED_COLLECTOR_FILE@" in raw
    assert "@EXISTING_SNAPSHOT_DIR@" in raw
    svc = settings["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["User"] == "@APPROVED_COLLECTOR_USER@"
    assert svc["ExecStart"] == (
        "@APPROVED_PYTHON@ -I -B /run/pab-host-audit-collector.py collect "
        "--output /run/pab-host-audit-output/snapshot.json"
    )
    assert set(settings.sections()) == {"Unit", "Service"}
    assert "Install" not in settings  # The service itself cannot be enabled.
    assert "ExecStartPre" not in svc and "ExecStartPost" not in svc
    assert "ExecStop" not in svc and "Restart" not in svc


def test_collector_is_sandboxed_and_can_write_only_the_snapshot_directory():
    _, settings = unit("pab-host-audit-refresh.service.in")
    svc = settings["Service"]
    assert svc["ReadWritePaths"] == "/run/pab-host-audit-output"
    assert settings["Unit"]["ConditionPathExists"] == "@PINNED_COLLECTOR_FILE@"
    assert settings["Unit"]["ConditionPathIsDirectory"] == "@EXISTING_SNAPSHOT_DIR@"
    assert svc["BindReadOnlyPaths"] == (
        "@PINNED_COLLECTOR_FILE@:/run/pab-host-audit-collector.py"
    )
    assert svc["BindPaths"] == (
        "@EXISTING_SNAPSHOT_DIR@:/run/pab-host-audit-output"
    )
    assert svc["ProtectHome"] == "yes"  # no access to the rest of /home
    assert "PYTHONPATH" not in svc["Environment"]
    assert "ProtectHome=no" not in (ROOT / "pab-host-audit-refresh.service.in").read_text()
    assert svc["UMask"] == "0077"
    assert svc["TimeoutStartSec"] == "25s"
    for key in (
        "NoNewPrivileges", "ProtectHome", "PrivateTmp", "PrivateDevices",
        "ProtectKernelTunables", "ProtectKernelModules",
        "ProtectControlGroups", "ProtectHostname", "RestrictSUIDSGID",
        "RestrictRealtime", "LockPersonality",
    ):
        assert svc[key] == "yes"
    assert svc["ProtectSystem"] == "strict"
    assert svc["RestrictAddressFamilies"] == "AF_UNIX"
    assert svc["CapabilityBoundingSet"] == ""
    assert svc["AmbientCapabilities"] == ""
    assert svc["Environment"] == "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1"
    assert not any(
        key in svc for key in (
            "SupplementaryGroups", "Group", "EnvironmentFile", "RootDirectory",
            "ExecReload", "Sockets", "RootImage",
        )
    )


def test_timer_is_opt_in_with_10_minute_monotonic_refresh():
    raw, timer = unit("pab-host-audit-refresh.timer")
    assert set(timer.sections()) == {"Unit", "Timer", "Install"}
    spec = timer["Timer"]
    assert spec["Unit"] == "pab-host-audit-refresh.service"
    assert spec["OnBootSec"] == "2min"
    assert spec["OnUnitInactiveSec"] == "10min"
    assert spec["AccuracySec"] == "1min"
    assert "Persistent" not in spec
    assert "OnCalendar" not in spec and "OnUnitActiveSec" not in spec
    assert timer["Install"]["WantedBy"] == "timers.target"
    assert "systemctl" not in raw
    assert "docker" not in raw.lower()


def test_template_and_timer_have_no_activation_or_admin_secrets():
    for name in (
        "pab-host-audit-refresh.service.in",
        "pab-host-audit-refresh.timer",
    ):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "sudo " not in content
        assert "systemctl enable" not in content
        assert "systemctl start" not in content
        assert "DOCKER_HOST=" not in content
        assert "docker.sock" not in content or (
            "Do not grant Docker socket" in content
        )
        assert "pab-codex" not in content
