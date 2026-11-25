"""Offline guard for the independent storage-only Control mount.

No Docker, SSH, systemctl, production paths, or credentials are touched.
Actual merged Compose/host ownership still require local operator acceptance.
"""
from pathlib import Path

from personal_ai_brain.host_storage import CONTROL_SNAPSHOT_PATH

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ops" / "storage" / "compose.control-storage.override.yaml.in"
CONTROL = ROOT / "src" / "personal_ai_brain" / "control_worker.py"


def test_source_only_overlay_adds_only_isolated_storage_read():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count("services:") == 1
    assert text.count("  control:") == 1
    assert text.count("target: /storage-audit") == 1
    assert text.count("read_only: true") == 1
    assert text.count("PAB_HOST_STORAGE_ENABLED: \"1\"") == 1
    assert text.count("@APPROVED_STORAGE_READER_GID@") >= 1
    assert text.count("@APPROVED_STORAGE_SNAPSHOT_DIR@") >= 1
    assert "create_host_path: false" in text
    assert "target: /host-audit" not in text
    payload = "\n".join(line for line in text.splitlines()
                        if not line.lstrip().startswith("#"))
    for forbidden in (
        "docker.sock", "privileged: true", "cap_add", "devices:",
        "pid: host", "network_mode: host", "ports:", "secrets:",
        "image:", "build:", "command:", "entrypoint:", "user:",
        "PAB_HOST_AUDIT_ENABLED", "/control", "/core-state",
        "systemctl", "sudo ", "/dev/", "effect.execute",
    ):
        assert forbidden not in payload


def test_control_uses_fixed_distinct_mount_default_off():
    source = CONTROL.read_text(encoding="utf-8")
    assert str(CONTROL_SNAPSHOT_PATH) == "/storage-audit/storage.json"
    assert 'Path("/storage-audit/storage.json")' in source
    assert 'os.environ.get("PAB_HOST_STORAGE_ENABLED") == "1"' in source
    assert 'host_storage_snapshot=storage_file' in source
    assert 'Path("/host-audit/snapshot.json")' in source
    assert 'os.environ.get("PAB_HOST_AUDIT_ENABLED") == "1"' in source
    # A separate, explicitly enabled fixed Unix socket is now supported.
    # Storage-only overlays still cannot activate this independent effect lane.
    assert """    effect_provider = None
    if os.environ.get('PAB_EFFECT_SOCKET_ENABLED') == '1':
        from .host_effect_socket import HostEffectClient
        effect_provider = HostEffectClient(Path('/host-effects/effect.sock'))
""" in source
    assert "approved_effect_provider=effect_provider" in source
    assert "PAB_EFFECT_SOCKET_ENABLED" not in TEMPLATE.read_text(encoding="utf-8")
