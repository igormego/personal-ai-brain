"""Offline Linux sandbox integration; never connects to a Docker daemon."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from personal_ai_brain import compose_render
from personal_ai_brain.deployment_preflight import PreflightDenied
from test_deployment_preflight import inputs

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux sandbox required")


@pytest.fixture
def render_inputs(inputs, tmp_path):
    root, root_fd, m, _, _ = inputs
    bwrap = shutil.which("bwrap")
    candidates = [os.environ.get("PAB_TEST_COMPOSE"), "/usr/local/lib/docker/cli-plugins/docker-compose", "/usr/libexec/docker/cli-plugins/docker-compose", "/usr/lib/docker/cli-plugins/docker-compose"]
    compose = next((p for p in candidates if p and Path(p).is_file()), None)
    if not bwrap or not compose:
        if os.environ.get("CI"):
            pytest.fail("CI must provide bwrap and the Compose CLI")
        pytest.skip("isolated bwrap/Compose binary unavailable")
    tool = tmp_path / "compose"
    shutil.copyfile(compose, tool); tool.chmod(0o700)
    try:
        version = subprocess.run([str(tool), "version", "--short"], capture_output=True, text=True, timeout=15, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.fail("local Compose version check failed")
    (root / "project/handoff").mkdir(mode=0o700)
    (root / "project/private/fixture-secret").write_bytes(b"synthetic")
    (root / "project/private/fixture-secret").chmod(0o600)
    env = root / "private/env"; env.write_text("SYNTHETIC_VALUE=fixture\n"); env.chmod(0o600)
    m["environment"]["sha256"] = hashlib.sha256(env.read_bytes()).hexdigest()
    base = root / "project/base.yaml"
    base.write_text(base.read_text().replace("synthetic/control:baseline", m["source_image"]))
    candidate = root / "project/candidate.json"
    candidate.write_text(json.dumps({"services": {"control": {"image": m["target_image"]}}}))
    for layer in m["layers"]:
        layer["sha256"] = hashlib.sha256((root / "project" / layer["path"]).read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    bfd = os.open(bwrap, os.O_RDONLY | os.O_NOFOLLOW)
    cfd = os.open(tool, os.O_RDONLY | os.O_NOFOLLOW)
    args = dict(owner_uid=os.getuid(), expected_layer_order=tuple(x["path"] for x in m["layers"]),
                bwrap_fd=bfd, bwrap_sha256=hashlib.sha256(Path(bwrap).read_bytes()).hexdigest(),
                compose_fd=cfd, compose_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(),
                compose_version=version, project_name="fixture", timeout=20)
    yield root, root_fd, m, args
    os.close(bfd); os.close(cfd)


def run(i, **kwargs):
    _, fd, _, args = i
    return compose_render.render_preflight(fd, "/manifest.json", **(args | kwargs))


def test_real_sealed_compose_render_and_relative_secrets(render_inputs):
    result = run(render_inputs)
    assert result["normalization_provenance"] == "sealed_inputs_sandboxed"
    assert result["deploy_authorized"] is False
    assert result["image_identity"] == "unverified"
    assert result["manifest_sha256"] == hashlib.sha256((render_inputs[0] / "manifest.json").read_bytes()).hexdigest()
    assert result["compose_sha256"] == render_inputs[3]["compose_sha256"]
    assert result["compose_version"] == render_inputs[3]["compose_version"]


def test_real_render_uses_network_and_host_isolation(render_inputs, monkeypatch):
    original = compose_render._run
    observed = []
    def inspect(args, *rest, **kwargs):
        observed.append(args)
        return original(args, *rest, **kwargs)
    monkeypatch.setattr(compose_render, "_run", inspect)
    assert run(render_inputs)["deploy_authorized"] is False
    assert len(observed) == 3
    for args in observed:
        assert "--unshare-all" in args and "--disable-userns" in args and "--assert-userns-disabled" in args
        assert "--clearenv" in args and "--cap-drop" in args and "ALL" in args
        assert "unix:///nonexistent/docker.sock" in args
        assert "--bind" not in args and "--ro-bind" not in args


def test_wrong_version_tool_pin_and_timeout_fail_closed(render_inputs):
    with pytest.raises(PreflightDenied): run(render_inputs, compose_version="0.0.0")
    with pytest.raises(PreflightDenied): run(render_inputs, compose_sha256="0" * 64)
    with pytest.raises(PreflightDenied): run(render_inputs, timeout=0.000001)
    # A caller-owned executable in a writable ancestor cannot act as Bubblewrap.
    with pytest.raises(PreflightDenied):
        run(render_inputs, bwrap_fd=render_inputs[3]["compose_fd"], bwrap_sha256=render_inputs[3]["compose_sha256"])


def test_changed_layer_during_render_is_rejected(render_inputs, monkeypatch):
    root, _, _, _ = render_inputs
    original = compose_render._run
    changed = False
    def mutate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / "project/media.yaml").write_text("services: {}\n")
            changed = True
        return result
    monkeypatch.setattr(compose_render, "_run", mutate)
    with pytest.raises(PreflightDenied): run(render_inputs)
    assert changed


def test_unsupported_include_is_denied_before_running_tool(render_inputs, monkeypatch):
    root, _, m, _ = render_inputs
    layer = root / "project/media.yaml"
    layer.write_text("include: [../../outside.yaml]\n")
    m["layers"][3]["sha256"] = hashlib.sha256(layer.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    called = []
    monkeypatch.setattr(compose_render, "_run", lambda *a, **k: called.append(True))
    with pytest.raises(PreflightDenied): run(render_inputs)
    assert not called


def test_project_escape_or_unsupported_secret_denied(render_inputs):
    root, _, m, _ = render_inputs
    layer = root / "project/base.yaml"
    layer.write_text(layer.read_text().replace("./private/fixture-secret", "../../outside"))
    m["layers"][0]["sha256"] = hashlib.sha256(layer.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PreflightDenied): run(render_inputs)


def test_reserved_environment_control_is_denied(render_inputs):
    root, _, m, _ = render_inputs
    env = root / "private/env"; env.write_text("COMPOSE_FILE=/outside\n")
    m["environment"]["sha256"] = hashlib.sha256(env.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PreflightDenied): run(render_inputs)


def test_protected_env_changed_during_render_is_rejected(render_inputs, monkeypatch):
    root, _, _, _ = render_inputs
    original = compose_render._run
    changed = False
    def mutate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / "private/env").write_text("SYNTHETIC_VALUE=changed\n")
            changed = True
        return result
    monkeypatch.setattr(compose_render, "_run", mutate)
    with pytest.raises(PreflightDenied): run(render_inputs)
    assert changed


def test_identical_bytes_rewritten_during_render_are_rejected(render_inputs, monkeypatch):
    root, _, _, _ = render_inputs
    original = compose_render._run
    changed = False
    def mutate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            layer = root / "project/media.yaml"
            layer.write_bytes(layer.read_bytes())
            changed = True
        return result
    monkeypatch.setattr(compose_render, "_run", mutate)
    with pytest.raises(PreflightDenied): run(render_inputs)
    assert changed


def test_extra_candidate_change_denied(render_inputs):
    root, _, m, _ = render_inputs
    p = root / "project/candidate.json"
    p.write_text(json.dumps({"services": {"control": {"image": m["target_image"], "read_only": False}}}))
    m["layers"][4]["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PreflightDenied): run(render_inputs)


def test_secret_symlink_denied(render_inputs):
    root, _, _, _ = render_inputs
    secret = root / "project/private/fixture-secret"
    secret.unlink(); secret.symlink_to(root / "private/env")
    with pytest.raises(PreflightDenied): run(render_inputs)


def test_duplicate_yaml_and_output_limit_fail_closed(render_inputs, monkeypatch):
    root, _, m, _ = render_inputs
    layer = root / "project/media.yaml"
    layer.write_text("services: {}\nservices: {}\n")
    m["layers"][3]["sha256"] = hashlib.sha256(layer.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PreflightDenied): run(render_inputs)
    layer.write_text("services:\n  control:\n    environment:\n      SYNTHETIC_MODE: fixture\n")
    m["layers"][3]["sha256"] = hashlib.sha256(layer.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setattr(compose_render, "MAX_OUTPUT", 1)
    with pytest.raises(PreflightDenied): run(render_inputs)
