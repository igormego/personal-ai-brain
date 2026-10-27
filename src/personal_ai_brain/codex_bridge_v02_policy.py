"""Trusted policy for a *future*, opt-in v0.2 sandboxed coding lane.

No listener/worker is activated by this module. All project identities, local
canonical checkouts and test profiles are owned by installed operator code,
never by the remote Issue body, Codex prompt or a repository's task code.
The production bridge v0.1 retains its original two-file policy.
"""

from dataclasses import dataclass
import re
import uuid

PROTOCOL = "pab-codex/v0.2"
EXECUTION_PROFILE = "sandboxed-repo-dev"
MAX_TEXT = 8000
MAX_BODY = 20000
MAX_CHANGED_FILES = 64
MAX_CHANGED_LINES = 6000
MAX_FILE_BYTES = 1_048_576

@dataclass(frozen=True)
class TrustedProject:
    project_id: str
    repository: str
    windows_checkout: str
    test_profile: str


# Operator-approved fixed mappings, not a dynamic GitHub repo/URL or path input.
TRUSTED_PROJECTS = {
    "personal-ai-brain": TrustedProject(
        "personal-ai-brain", "igormego/personal-ai-brain",
        r"C:\Projects\personal-ai-brain", "pab-pytest"),
    "homelab-infrastructure": TrustedProject(
        "homelab-infrastructure", "igormego/homelab-infrastructure",
        r"C:\Projects\homelab-infrastructure", "movie-intelligence-node"),
}

# Paths that control execution, credentials, releases or the safety boundary are
# never patchable from the unattended remote sandbox.
_BLOCKED_BASENAMES = frozenset({
    "AGENTS.md", ".env", ".env.local", ".npmrc", ".pypirc",
    "Dockerfile", "docker-compose.yml", "compose.yaml",
    "pyproject.toml", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "requirements.txt", "setup.py", "setup.cfg",
})
_BLOCKED_PARTS = frozenset({
    ".git", ".github", ".codex", ".project", "deploy", "deployment",
    "secrets", "credentials", "keys", "migrations", "__pycache__",
    "node_modules", ".venv", "venv", "dist", "build", "config",
})
_BLOCKED_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".env", ".sh",
    ".ps1", ".bat", ".cmd", ".exe", ".dll", ".so", ".zip", ".tar",
    ".gz", ".bin", ".ipynb", ".lock", ".yaml", ".yml", ".toml",
})
_PAB_SECURITY_PREFIXES = (
    "codex_bridge_", "coding_broker", "control", "github_issues",
    "work_runner", "work_handoff", "work_contract", "builder_auth",
    "inference_deployment", "runtime",
)
_ALLOWED_SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".html",
    ".json", ".sql", ".md", ".txt",
})
_SIMPLE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*\Z", re.ASCII)


def project_for(project_id: object) -> TrustedProject:
    if type(project_id) is not str or project_id not in TRUSTED_PROJECTS:
        raise ValueError("unknown_project")
    return TRUSTED_PROJECTS[project_id]


def validate_request(value: object) -> dict:
    """Validate caller fields; no payload-specified repo, checkout or command."""
    if type(value) is not dict or set(value) != {
        "request_id", "task_id", "project_id", "execution_profile",
        "goal", "acceptance_criteria", "base_sha",
    }:
        raise ValueError("invalid_fields")
    project_for(value["project_id"])
    if value["execution_profile"] != EXECUTION_PROFILE:
        raise ValueError("unknown_profile")
    for key in ("request_id", "task_id"):
        identifier = value[key]
        if type(identifier) is not str:
            raise ValueError("invalid_identifier")
        try:
            parsed = uuid.UUID(identifier)
        except (ValueError, AttributeError):
            raise ValueError("invalid_identifier") from None
        if parsed.version != 4 or str(parsed) != identifier:
            raise ValueError("invalid_identifier")
    for key in ("goal", "acceptance_criteria"):
        value_text = value[key]
        if type(value_text) is not str or not 1 <= len(value_text) <= MAX_TEXT or (
            any(ord(c) == 0 for c in value_text)
        ):
            raise ValueError("invalid_text")
    if type(value["base_sha"]) is not str or not re.fullmatch("[0-9a-f]{40}", value["base_sha"]):
        raise ValueError("invalid_base")
    return dict(value)


def allowed_change(project_id: object, path: object) -> bool:
    """Broad source/test/docs editing, but no security/policy/deploy changes.

    Homelab's first offline test profile validates Movie Intelligence code.
    New domains can be opted in with their own fixed, proven test profile.
    """
    project_for(project_id)
    if type(path) is not str or not 1 <= len(path) <= 256 or not _SIMPLE_PATH.fullmatch(path):
        return False
    parts = path.split("/")
    if any(part in _BLOCKED_PARTS or part.startswith(".") for part in parts):
        return False
    if parts[-1] in _BLOCKED_BASENAMES:
        return False
    suffix = "." + parts[-1].rsplit(".", 1)[1] if "." in parts[-1] else ""
    if suffix in _BLOCKED_SUFFIXES:
        return False
    if path in {"README.md", "CHANGELOG.md"}:
        return True
    if parts[0] == "docs":
        return suffix in {".md", ".txt"}
    if project_id == "personal-ai-brain":
        if parts[0] == "src" and len(parts) >= 3 and parts[1] == "personal_ai_brain":
            return suffix in _ALLOWED_SOURCE_EXTENSIONS and not parts[-1].startswith(_PAB_SECURITY_PREFIXES)
        if parts[0] == "tests" and len(parts) >= 2:
            return suffix == ".py" and not parts[-1].startswith(("test_codex_bridge_", "test_control", "test_builder_auth"))
        return False
    if project_id == "homelab-infrastructure":
        return (len(parts) >= 3 and parts[:2] == ["tools", "movie-intelligence"]
                and suffix in {".mjs", ".js", ".py", ".md", ".json", ".txt"})
    return False


def approved_changes(project_id: object, paths: object, *, additions: int, deletions: int) -> list[str]:
    project_for(project_id)
    if (type(paths) is not list or not 1 <= len(paths) <= MAX_CHANGED_FILES or
        len(paths) != len(set(map(str, paths))) or
        type(additions) is not int or type(deletions) is not int or
        min(additions, deletions) < 0 or additions + deletions > MAX_CHANGED_LINES or
        not all(allowed_change(project_id, p) for p in paths)):
        raise ValueError("change_scope_denied")
    return sorted(paths)
