"""Linux-only, offline checks; no Docker, services, ledger or host access."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from personal_ai_brain.deployment_preflight import PreflightDenied, parse_manifest, preflight

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-relative security semantics")
FIXTURE = Path(__file__).parent / "fixtures" / "issue83"


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    for name in ("project", "private", "project/state", "project/private"):
        (root / name).mkdir(mode=0o700)
    m = json.loads((FIXTURE / "manifest.json").read_text())
    m["project_directory"] = "/project"
    for key, filename, data in (("environment", "env", b"env"), ("profile", "profile", b"profile")):
        p = root / "private" / filename
        p.write_bytes(data); p.chmod(0o600)
        m[key]["reference"] = "/private/" + filename
        m[key]["sha256"] = hashlib.sha256(data).hexdigest()
    for layer in m["layers"]:
        p = root / "project" / layer["path"]
        p.write_bytes((FIXTURE / "compose" / layer["path"]).read_bytes()); p.chmod(0o600)
    (root / "project/private/secret").write_bytes(b"fixture-only")
    (root / "project/private/secret").chmod(0o600)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(m)); manifest.chmod(0o600)
    original = {"services": {"control": {"image": m["source_image"], "volumes": [{"type": "bind", "source": "/project/state", "target": "/state"}]}}, "secrets": {"test": {"file": "/project/private/secret"}}}
    candidate = deepcopy(original)
    candidate["services"]["control"]["image"] = m["target_image"]
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    yield root, fd, m, original, candidate
    os.close(fd)


def run(i, original=None, candidate=None, order=None):
    root, fd, m, a, b = i
    return preflight(fd, "/manifest.json", owner_uid=os.getuid(), expected_layer_order=order or tuple(x["path"] for x in m["layers"]), original=a if original is None else original, candidate=b if candidate is None else candidate)


def test_fixture_contract_and_image_only_diff(inputs):
    assert run(inputs) == {"status": "inputs_and_supplied_diff_verified", "deploy_authorized": False, "normalization_provenance": "unverified", "baseline_freshness": "self_attested", "image_identity": "unverified", "module_contents": "unverified"}


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(source_image="latest"),
    lambda m: m["layers"][0].update(path="../escape"),
    lambda m: m["profile"].update(fresh_baseline=False),
    lambda m: m["ledger"].update(preserve_current=1),
    lambda m: m["recovery"].update(retry_uncertain=True),
    lambda m: m["supervisor_actions"].append("shell"),
    lambda m: m.update(extra="unexpected"),
])
def test_manifest_shape_and_policy_fail_closed(inputs, mutation):
    root, _, m, _, _ = inputs
    mutation(m)
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PreflightDenied, match="^deployment_preflight_denied$"):
        run(inputs)


def test_duplicate_keys_denied():
    with pytest.raises(PreflightDenied):
        parse_manifest(b'{"schema_version":"fixture-1","schema_version":"fixture-1"}')


def test_integrity_order_modes_and_links(inputs):
    root, _, m, _, _ = inputs
    layer = root / "project" / m["layers"][0]["path"]
    with pytest.raises(PreflightDenied): run(inputs, order=tuple(reversed([x["path"] for x in m["layers"]])))
    original = layer.read_bytes()
    layer.write_bytes(b"modified")
    with pytest.raises(PreflightDenied): run(inputs)
    layer.write_bytes(original); layer.chmod(0o666)
    with pytest.raises(PreflightDenied): run(inputs)
    layer.unlink(); layer.symlink_to(root / "private/env")
    with pytest.raises(PreflightDenied): run(inputs)


def test_protected_references_and_unapproved_diffs(inputs):
    root, _, _, original, candidate = inputs
    bad = deepcopy(candidate); bad["services"]["control"]["privileged"] = True
    with pytest.raises(PreflightDenied): run(inputs, candidate=bad)
    bad = deepcopy(candidate); bad["services"]["control"]["volumes"][0]["source"] = "/project/../private"
    with pytest.raises(PreflightDenied): run(inputs, candidate=bad)
    (root / "project/private/secret").chmod(0o644)
    with pytest.raises(PreflightDenied): run(inputs)
    (root / "project/private/secret").chmod(0o600)
    (root / "private/profile").write_bytes(b"changed")
    with pytest.raises(PreflightDenied): run(inputs)


def test_reference_symlink_and_parent_permissions_fail_closed(inputs):
    root, _, _, _, _ = inputs
    secret = root / "project/private/secret"
    secret.unlink(); secret.symlink_to(root / "private/env")
    with pytest.raises(PreflightDenied): run(inputs)
    secret.unlink(); secret.write_bytes(b"fixture"); secret.chmod(0o600)
    (root / "project/private").chmod(0o777)
    with pytest.raises(PreflightDenied): run(inputs)


def test_bool_vs_number_is_not_equal(inputs):
    _, _, _, original, candidate = inputs
    a = deepcopy(original); b = deepcopy(candidate)
    a["services"]["control"]["read_only"] = True
    b["services"]["control"]["read_only"] = 1
    with pytest.raises(PreflightDenied): run(inputs, original=a, candidate=b)


def test_hardlink_wrong_owner_and_manifest_permissions(inputs):
    root, fd, m, a, b = inputs
    layer = root / "project" / m["layers"][0]["path"]
    alias = root / "project" / "hardlink"
    os.link(layer, alias)
    with pytest.raises(PreflightDenied): run(inputs)
    alias.unlink()
    with pytest.raises(PreflightDenied):
        preflight(fd, "/manifest.json", owner_uid=os.getuid() + 1, expected_layer_order=tuple(x["path"] for x in m["layers"]), original=a, candidate=b)
    (root / "manifest.json").chmod(0o644)
    with pytest.raises(PreflightDenied): run(inputs)


def test_missing_layer_and_unapproved_reference_types(inputs):
    root, _, m, _, candidate = inputs
    bad = deepcopy(candidate)
    bad["services"]["control"]["volumes"][0]["type"] = "volume"
    with pytest.raises(PreflightDenied): run(inputs, candidate=bad)
    bad = deepcopy(candidate); bad["secrets"]["test"] = {"external": True}
    with pytest.raises(PreflightDenied): run(inputs, candidate=bad)
    (root / "project" / m["layers"][2]["path"]).unlink()
    with pytest.raises(PreflightDenied): run(inputs)
