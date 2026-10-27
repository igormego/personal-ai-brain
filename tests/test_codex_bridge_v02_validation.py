"""Run trusted v0.2 validator against disposable Git repositories, never Homelab."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from personal_ai_brain.codex_bridge_v02_validation import (
    WSL_ROOT, MAX_RESULT_BYTES, validator_script, validate_completion,
)


def _git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def disposable(tmp_path):
    task = str(uuid.uuid4())
    folder = tmp_path / "tasks" / task
    folder.mkdir(parents=True)
    _git(folder, "init", "-q")
    _git(folder, "config", "user.name", "Test")
    _git(folder, "config", "user.email", "test@example.invalid")
    (folder/"tests").mkdir()
    for name in ('state_quality','assistant_media_selector','assistant_contract','assistant_result','rollup_proposal'):
        (folder/'tests'/('test_'+name+'.py')).write_text('def test_smoke(): assert 2+2==4\n')
    (folder/"README.md").write_text("# Test\n")
    _git(folder, "add", ".")
    _git(folder, "commit", "-qm", "base")
    base = _git(folder, "rev-parse", "HEAD")
    # Only this disposable local test adapts the source's fixed WSL path and
    # trusted Python binary. The installed code NEVER accepts these overrides.
    script = validator_script()
    script = script.replace(
        "'/home/pabbridge/bridge-v02/tasks/'",
        repr(str(folder.parent) + "/"),
    )
    script = script.replace("'/usr/bin/python3'", repr(sys.executable))
    return folder, base, script


def _validate_local(fixture):
    folder, base, source = fixture
    return subprocess.run(
        [sys.executable, "-c", source, str(folder), base, "personal-ai-brain"],
        capture_output=True, text=True, timeout=90,
    )


def test_script_is_trusted_compilable_policy_included():
    script = validator_script()
    assert "def allowed_change(" in script
    assert "def approved_changes(" in script
    assert "sandboxed_tests_failed" in script
    assert "git('diff','HEAD','--check'" in script
    assert "subprocess.run(command" in script
    assert "/usr/local/bin/pab-v02-node-v24.22.1" in script
    assert "requests" not in script


def test_real_disposable_repository_accepts_new_module(disposable):
    folder, _, _ = disposable
    (folder/"src"/"personal_ai_brain").mkdir(parents=True)
    (folder/"src"/"personal_ai_brain"/"state_quality.py").write_text("answer = 42\n")
    r = _validate_local(disposable)
    assert r.returncode == 0, r.stderr[-1500:]
    p = json.loads(r.stdout)
    assert p["files_changed"] == ["src/personal_ai_brain/state_quality.py"]
    assert p["additions"] == 1 and p["deletions"] == 0
    assert p["test_summary"] == "10 tests passed in restricted Linux sandbox"


def test_disposable_repo_accepts_edits_in_more_than_two_files(disposable):
    folder, _, _ = disposable
    (folder/"src"/"personal_ai_brain").mkdir(parents=True)
    for name in ("state_quality.py", "assistant_result.py", "rollup_proposal.py"):
        (folder/"src"/"personal_ai_brain"/name).write_text("a = 3\n")
    r = _validate_local(disposable)
    assert r.returncode == 0, r.stderr[-1500:]
    assert len(json.loads(r.stdout)["files_changed"]) == 3


@pytest.mark.parametrize("unsafe", [
    ".github/workflows/deploy.yml",
    "deploy/update.ps1",
    "src/personal_ai_brain/codex_bridge_core.py",
    "src/personal_ai_brain/key.pem",
])
def test_disposable_repo_denies_unsafe_new_files_before_tests(disposable, unsafe):
    folder, _, _ = disposable
    path = folder / unsafe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("should = 'block'\n")
    r = _validate_local(disposable)
    assert r.returncode != 0
    assert "change_scope_denied" in r.stderr


def test_changed_test_execution_mutating_source_fails(disposable):
    folder, _, _ = disposable
    (folder/"src"/"personal_ai_brain").mkdir(parents=True)
    target = folder/"src"/"personal_ai_brain"/"state_quality.py"
    target.write_text("answer = 1\n")
    (folder/"tests"/"test_state_quality.py").write_text(
        "from pathlib import Path\n"
        "def test_smoke():\n"
        "    Path('src/personal_ai_brain/state_quality.py').write_text('answer = 2\\n')\n"
        "    assert True\n"
    )
    r = _validate_local(disposable)
    assert r.returncode != 0
    assert "tests_changed_tree" in r.stderr


def test_disposable_repo_denies_symlink_new_file(disposable):
    folder, _, _ = disposable
    source = folder/"README.md"
    p = folder/"src"/"personal_ai_brain"/"state_quality.py"
    p.parent.mkdir(parents=True)
    p.symlink_to(source)
    r = _validate_local(disposable)
    assert r.returncode != 0
    assert "unsafe_changed_file" in r.stderr


def _workspace():
    task = str(uuid.uuid4())
    return {
        "cwd": WSL_ROOT + "/tasks/" + task,
        "branch": "codex/bridge-task-" + task,
        "base_sha": "a"*40,
    }


def test_trusted_win_validator_uses_fixed_sandbox_no_shell(monkeypatch):
    calls = []
    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        response = {
            "files_changed": ["src/personal_ai_brain/state_quality.py"],
            "additions": 2, "deletions": 0,
            "test_summary": "7 tests passed in restricted Linux sandbox",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode())
    monkeypatch.setattr(
        "personal_ai_brain.codex_bridge_v02_validation.subprocess.run", fake,
    )
    result = validate_completion(_workspace(), "personal-ai-brain")
    assert result["test_summary"].startswith("7 tests")
    args, kw = calls[0]
    assert "--permission-profile" in args
    assert args[args.index("--permission-profile") + 1] == "bridge-workspace-only"
    assert "permissions.bridge-workspace-only.network.enabled=false" in args
    assert "sandbox" in args and "--" in args
    assert "codex.submit" not in args
    assert kw["timeout"] == 320
    assert "stderr" in kw and "shell" not in kw


def test_reject_workspace_spoofing_before_running_any_code(monkeypatch):
    monkeypatch.setattr(
        "personal_ai_brain.codex_bridge_v02_validation.subprocess.run",
        lambda *a, **k: pytest.fail("no trusted subprocess should be run"),
    )
    valid = _workspace()
    for bad in [
        valid | {"cwd": "/tmp/attacker"},
        valid | {"base_sha": "Z"*40},
        valid | {"branch": "codex/attacker"},
        valid | {"extra": True},
    ]:
        with pytest.raises(ValueError, match="invalid_workspace"):
            validate_completion(bad, "personal-ai-brain")
    with pytest.raises(ValueError, match="unknown_project"):
        validate_completion(valid, "not-an-approved-repo")


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout=b"private-token"),
    SimpleNamespace(returncode=0, stdout=b"X"*(MAX_RESULT_BYTES+1)),
    SimpleNamespace(returncode=0, stdout=b'{"files_changed":[],"additions":0,"deletions":0,"test_summary":"7 tests passed in restricted Linux sandbox"}'),
])
def test_subprocess_failure_never_exposes_untrusted_output(monkeypatch, result):
    monkeypatch.setattr("personal_ai_brain.codex_bridge_v02_validation.subprocess.run",
                        lambda *a, **k: result)
    with pytest.raises(RuntimeError, match="^v02_completion_validation_failed$"):
        validate_completion(_workspace(), "personal-ai-brain")


def test_disabling_patched_tests_keeps_mandatory_base(disposable):
    folder,_,_=disposable
    (folder/'tests/test_state_quality.py').write_text('# task disabled tests\n')
    r=_validate_local(disposable)
    assert r.returncode==0,r.stderr[-1000:]
    assert json.loads(r.stdout)['test_summary']=='9 tests passed in restricted Linux sandbox'
