"""Untrusted v0.2 payloads cannot select hosts, repos, commands or permissions."""
import uuid
import pytest

from personal_ai_brain.codex_bridge_v02_policy import (
    TRUSTED_PROJECTS, PROTOCOL, EXECUTION_PROFILE, project_for,
    validate_request, allowed_change, approved_changes,
)


def request(project="personal-ai-brain", **overrides):
    return dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                project_id=project, execution_profile=EXECUTION_PROFILE,
                goal="Add a useful feature", acceptance_criteria="Fixed offline tests pass",
                base_sha="a"*40) | overrides


def test_opt_in_catalog_owns_repository_and_local_paths():
    assert set(TRUSTED_PROJECTS) == {"personal-ai-brain", "homelab-infrastructure"}
    assert project_for("personal-ai-brain").windows_checkout == r"C:\Projects\RemoteCodex-v02\sources\personal-ai-brain"
    assert project_for("homelab-infrastructure").test_profile == "movie-intelligence-node"
    assert project_for("personal-ai-brain").test_profile == "pab-pytest"
    assert PROTOCOL == "pab-codex/v0.2"
    for rogue in ("", "../repo", "https://github.com/other/repo", "homelab", None, True):
        with pytest.raises(ValueError, match="unknown_project"):
            project_for(rogue)


@pytest.mark.parametrize("project", ["personal-ai-brain", "homelab-infrastructure"])
def test_request_exact_stable_envelope(project):
    value = request(project)
    assert validate_request(value) == value
    assert validate_request(value) is not value


@pytest.mark.parametrize("rogue_key", [
    "repository", "repository_url", "cwd", "path", "branch", "checkout",
    "distro", "backend", "ssh", "host", "token", "CODEX_HOME", "environment",
    "approval", "approvalPolicy", "sandbox", "network", "command", "flags",
    "test_command", "test_profile", "model", "provider", "retry_count",
    "push", "deploy", "merge", "validate_completion",
])
def test_no_caller_selected_authority(rogue_key):
    with pytest.raises(ValueError, match="invalid_fields"):
        validate_request(request(**{rogue_key: "attacker"}))


@pytest.mark.parametrize("overrides", [
    {"project_id": "unknown"}, {"execution_profile": "local-homelab-admin"},
    {"execution_profile": "bridge-workspace-only"},
    {"goal": ""}, {"goal": "x"*8001},
    {"acceptance_criteria": ""}, {"acceptance_criteria": "x"*8001},
    {"base_sha": "A"*40}, {"base_sha": "a"*39}, {"base_sha": None},
    {"request_id": "bad"}, {"task_id": True},
    {"task_id": str(uuid.uuid1())}, {"goal": "hello\x00world"},
])
def test_reject_invalid_identity_and_text(overrides):
    with pytest.raises(ValueError):
        validate_request(request(**overrides))


@pytest.mark.parametrize("project,path", [
    ("personal-ai-brain", "src/personal_ai_brain/assistant_media_plan.py"),
    ("personal-ai-brain", "src/personal_ai_brain/new_service.py"),
    ("personal-ai-brain", "tests/test_assistant_media_plan.py"),
    ("personal-ai-brain", "docs/design/new-feature.md"),
    ("homelab-infrastructure", "tools/movie-intelligence/model.mjs"),
    ("homelab-infrastructure", "tools/movie-intelligence/test/status.test.mjs"),
    ("homelab-infrastructure", "tools/movie-intelligence/get_movie_status.mjs"),
    ("homelab-infrastructure", "docs/homelab/overview.md"),
    ("homelab-infrastructure", "README.md"),
])
def test_broad_real_development_scope(project, path):
    assert allowed_change(project, path)


@pytest.mark.parametrize("path", [
    "../secret", "/etc/passwd", "src/../../.ssh/id_rsa", ".git/config",
    ".github/workflows/ci.yml", ".codex/environments/environment.toml",
    "AGENTS.md", "deploy/install_bridge_transport.ps1",
    "src/personal_ai_brain/codex_bridge_client.py",
    "src/personal_ai_brain/codex_bridge_v02_policy.py",
    "src/personal_ai_brain/control.py",
    "src/personal_ai_brain/runtime.py",
    "src/personal_ai_brain/github_issues.py",
    "tests/test_codex_bridge_core.py", "tests/test_control.py",
    "pyproject.toml", "requirements.txt", "setup.py",
    "src/personal_ai_brain/.env", "src/personal_ai_brain/secret.pem",
    "src/personal_ai_brain/model.p12", "docs/dangerous.sh",
    "docs/credentials.txt", "src//invalid.py", "README.md:evil",
    "src/personal_ai_brain/symlink\nname.py",
])
def test_sensitive_host_or_security_scope_deny_pab(path):
    assert not allowed_change("personal-ai-brain", path)


@pytest.mark.parametrize("path", [
    "deploy/compose.control.yaml", "compose.yaml", "Dockerfile",
    ".project/state.yaml", ".github/workflows/deploy.yml",
    "tools/other/new-script.mjs", "tools/movie-intelligence/config/key.json",
    "tools/movie-intelligence/migrations/change.sql",
    "tools/movie-intelligence/.env", "tools/movie-intelligence/private.db",
    "tools/movie-intelligence/run.sh", "tools/movie-intelligence/run.ps1",
    "tools/movie-intelligence/package-lock.json",
    "tools/movie-intelligence/config/secret.json",
])
def test_homelab_guarded_scope(path):
    assert not allowed_change("homelab-infrastructure", path)


def test_bounded_change_list_rejects_duplicates_and_overlarge():
    files = ["src/personal_ai_brain/assistant_media_plan.py", "tests/test_assistant_media_plan.py"]
    assert approved_changes("personal-ai-brain", files, additions=100, deletions=20) == sorted(files)
    for value in ([], files+files, ["deploy/run.sh"], ["src/../etc/passwd"]):
        with pytest.raises(ValueError, match="change_scope_denied"):
            approved_changes("personal-ai-brain", value, additions=1, deletions=0)
    for n in (True, -1, 6001):
        with pytest.raises(ValueError, match="change_scope_denied"):
            approved_changes("personal-ai-brain", files, additions=n, deletions=0)
    with pytest.raises(ValueError, match="change_scope_denied"):
        approved_changes("personal-ai-brain", files*33, additions=1, deletions=0)


@pytest.mark.parametrize('path',['radarr.mjs','seerr-request.mjs','sqlite.mjs','reacquire.mjs','test/adapters.test.mjs','test/seerr-request.test.mjs','new-feature.mjs'])
def test_uncovered_homelab_security_scope_denied(path):
    assert not allowed_change('homelab-infrastructure','tools/movie-intelligence/'+path)
