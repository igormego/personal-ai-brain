"""Offline fixture contract, not production deployment code or host acceptance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parent / "fixtures" / "issue83"
HEX = re.compile(r"^[0-9a-f]{64}$")
ACTIONS = ["stop_control", "stop_builder", "start_builder", "start_control"]


def fixture():
    return json.loads((ROOT / "manifest.json").read_text())


def digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def absolute(value):
    return isinstance(value, str) and PurePosixPath(value).is_absolute() and ".." not in PurePosixPath(value).parts


def validate(m):
    """Validate the simulated manifest shape; not a host security validator."""
    assert set(m) == {"schema_version", "source_image", "target_image", "project_directory", "environment", "layers", "modules", "profile", "supervisor_actions", "ledger", "recovery"}
    assert m["schema_version"] == "fixture-1"
    for key in ("source_image", "target_image"):
        assert isinstance(m[key], str) and m[key].startswith("sha256:") and digest(m[key][7:])
    assert absolute(m["project_directory"])
    for key in ("environment", "profile"):
        item = m[key]
        assert set(item) == ({"reference", "sha256"} if key == "environment" else {"reference", "sha256", "fresh_baseline"})
        assert absolute(item["reference"]) and digest(item["sha256"])
    assert m["profile"]["fresh_baseline"] is True
    assert len(m["layers"]) == 5
    names = []
    for item in m["layers"]:
        assert set(item) == {"path", "sha256"} and digest(item["sha256"])
        p = PurePosixPath(item["path"])
        assert not p.is_absolute() and len(p.parts) == 1 and p.name not in ("", ".", "..")
        names.append(p.name)
    assert len(set(names)) == 5
    assert m["modules"] and all(set(x) == {"name", "before_sha256", "after_sha256"} and re.fullmatch(r"[a-z_]+\.py", x["name"]) and digest(x["before_sha256"]) and digest(x["after_sha256"]) for x in m["modules"])
    assert m["supervisor_actions"] == ACTIONS
    assert m["ledger"] == {"preserve_current": True, "restore_historical": False}
    assert m["recovery"] == {"retry_uncertain": False, "rollback_uncertain": False, "require_review_on_failed_rollback": True}


def simulate(m, *, layer_ok=True, path_ok=True, failure=None, rollback_ok=True, uncertain=False):
    """State model of observed ordering; no subprocess or filesystem mutation."""
    validate(m)
    events = ["preflight"]
    if not layer_ok or not path_ok:
        return events + ["denied"]
    if m["source_image"] == m["target_image"]:
        return events + ["no_change"]
    events += ["render_compare", "ledger_check", "backup", "stop_control", "lock_recheck", "stop_builder", "replace_modules", "sandbox", "profile", "start_builder", "start_control", "readback"]
    if failure:
        index = events.index(failure)
        events = events[:index + 1]
        if uncertain:
            return events + ["uncertain_review"]
        if failure in ("stop_control", "lock_recheck", "stop_builder"):
            return events + ["restore_prior_control"]
        return events + (["rollback", "rollback_verified"] if rollback_ok else ["rollback", "rollback_failed_review"])
    return events + ["complete"]


def test_manifest_and_five_layer_relative_resolution():
    m = fixture()
    validate(m)
    folder = ROOT / "compose"
    docs = [yaml.safe_load((folder / layer["path"]).read_text()) for layer in m["layers"]]
    assert all(hashlib.sha256((folder / layer["path"]).read_bytes()).hexdigest() == layer["sha256"] for layer in m["layers"])
    assert [layer["path"] for layer in m["layers"]] == ["base.yaml", "security.yaml", "work.yaml", "media.yaml", "candidate.json"]
    assert docs[0]["services"]["control"]["volumes"][0].startswith("./state:")
    assert docs[0]["secrets"]["fixture_secret"]["file"] == "./private/fixture-secret"
    assert docs[2]["services"]["control"]["volumes"][0].startswith("./handoff:")
    assert docs[4]["services"]["control"]["image"] == "synthetic/control:candidate"
    # Relocation must not silently reinterpret relative input paths.
    assert PurePosixPath(m["project_directory"], "state") != PurePosixPath("/fixture/relocated", "state")


@pytest.mark.parametrize("change", [
    lambda m: m["profile"].update(fresh_baseline=False),
    lambda m: m["ledger"].update(restore_historical=True),
    lambda m: m["recovery"].update(retry_uncertain=True),
    lambda m: m["layers"][0].update(path="../escape"),
    lambda m: m["layers"][0].update(sha256="bad"),
    lambda m: m["layers"].append(deepcopy(m["layers"][0])),
    lambda m: m["supervisor_actions"].append("shell"),
    lambda m: m.update(secret="not allowed"),
])
def test_manifest_fails_closed(change):
    m = fixture()
    change(m)
    with pytest.raises((AssertionError, KeyError, TypeError)):
        validate(m)


def test_layer_drift_and_symlink_are_denied_before_effects(tmp_path):
    source = tmp_path / "layer"
    source.write_bytes(b"approved")
    approved = hashlib.sha256(source.read_bytes()).hexdigest()
    assert simulate(fixture(), layer_ok=hashlib.sha256(source.read_bytes()).hexdigest() == approved)[-1] == "complete"
    source.write_bytes(b"changed")
    assert simulate(fixture(), layer_ok=hashlib.sha256(source.read_bytes()).hexdigest() == approved) == ["preflight", "denied"]
    source.unlink()
    assert simulate(fixture(), layer_ok=source.exists()) == ["preflight", "denied"]
    link = tmp_path / "alias"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation unavailable")
    # Simulated no-follow decision; real no-follow enforcement is a future gate.
    assert simulate(fixture(), path_ok=not link.is_symlink()) == ["preflight", "denied"]


def test_partial_failure_interruption_and_rollback_failure():
    m = fixture()
    assert simulate(m, failure="sandbox")[-2:] == ["rollback", "rollback_verified"]
    assert simulate(m, failure="profile", rollback_ok=False)[-1] == "rollback_failed_review"
    assert simulate(m, failure="start_control", uncertain=True)[-1] == "uncertain_review"
    assert "rollback" not in simulate(m, failure="start_control", uncertain=True)
    assert "restore_ledger" not in simulate(m, failure="sandbox")
    assert "start_control" not in simulate(m, failure="stop_builder")
    assert simulate(m, failure="stop_builder")[-1] == "restore_prior_control"
    # Interrupted after a write has started has the same uncertain gate as a timeout.
    assert simulate(m, failure="start_builder", uncertain=True)[-1] == "uncertain_review"
    assert simulate(m, failure="start_builder", uncertain=True).count("start_builder") == 1


def test_real_compose_cli_resolves_five_layers_without_daemon(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI not available")
    project = tmp_path / "project"
    shutil.copytree(ROOT / "compose", project)
    args = [docker, "compose", "--project-directory", str(project)]
    for layer in fixture()["layers"]:
        args.extend(["-f", str(project / layer["path"])])
    p = subprocess.run(args + ["config", "--format", "json"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, "synthetic Compose resolution failed"
    result = json.loads(p.stdout)
    assert result["services"]["control"]["image"] == "synthetic/control:candidate"
    assert result["secrets"]["fixture_secret"]["file"] == str(project / "private" / "fixture-secret")
    assert {v["source"] for v in result["services"]["control"]["volumes"]} == {str(project / "state"), str(project / "handoff")}


def test_same_image_is_safe_noop():
    m = fixture()
    m["target_image"] = m["source_image"]
    assert simulate(m) == ["preflight", "no_change"]
