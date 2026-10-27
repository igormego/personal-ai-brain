"""Read-only, opt-in bridge to the existing Homelab Seerr movie planner.

The Node program and its environment are trusted *deployment* configuration,
not user-supplied inputs. The only permitted CLI is Homelab's plan-cli.mjs,
which exclusively invokes createMovieRequestController.plan_movie_request().
No subprocess is launched unless a trusted operator installs/injects this
provider; the production control worker does not wire one by default.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import unicodedata
from typing import Callable, Mapping

from .assistant_media_selector import MAX_TMDB_ID, validate_movie_selector


MAX_PLAN_BYTES = 4096
MAX_TITLE_BYTES = 512
DECISIONS = frozenset({
    "requestable", "already_available", "already_requested", "already_managed",
    "ambiguous", "blocked", "unresolved",
})
REASONS = frozenset({
    "invalid_arguments", "invalid_input", "title_requires_year",
    "unsupported_version_or_visibility", "multiple_search_candidates",
    "no_unique_movie", "resolved_identity_conflict",
    "read_identity_or_source_unresolved", "source_identity_conflict",
    "seerr_blocklisted", "file_or_seerr_available",
    "existing_request_or_processing", "failed_or_declined_request_requires_review",
    "radarr_manages_movie", "media_policy_dependency",
    "stable_identity_and_clear_preflight", "source_or_configuration_unavailable",
    "source_unavailable",
})
_SOURCE_STATE = re.compile(r"[a-z][a-z0-9_]{0,39}\Z", re.ASCII)
_ENV_NAMES = frozenset({
    "SEERR_BASE_URL", "SEERR_API_KEY", "RADARR_BASE_URL", "RADARR_API_KEY",
    "MOVIE_DB_PATH", "HOME", "PATH",
})


class MediaPlanUnavailable(RuntimeError):
    """Fixed safe exception; do not forward upstream stdout/stderr/details."""


def _unavailable() -> None:
    raise MediaPlanUnavailable("media_plan_unavailable")


def _pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _unavailable()
        result[key] = value
    return result


def sanitize_media_plan(selector: object, raw: object) -> dict:
    """Validate the trusted planner output and expose only bounded read data.

    Never forward intended_action, HTTP payload, request bodies or arbitrary
    upstream keys. The wrapper remains the owner of movie identity resolution.
    """
    selected = validate_movie_selector(selector)
    if type(raw) is not dict or set(raw) != {
        "contract_version", "decision", "reason", "identity", "evidence",
        "mutation_attempted",
    } or raw["contract_version"] != "0.1":
        _unavailable()
    decision, reason = raw["decision"], raw["reason"]
    if type(decision) is not str or decision not in DECISIONS or (
        type(reason) is not str or reason not in REASONS
    ) or raw["mutation_attempted"] is not False:
        _unavailable()
    identity = raw["identity"]
    if identity is not None:
        if type(identity) is not dict or set(identity) != {"tmdb_id", "title", "year"}:
            _unavailable()
        tmdb_id, title, year = identity["tmdb_id"], identity["title"], identity["year"]
        if type(tmdb_id) is not int or not 1 <= tmdb_id <= MAX_TMDB_ID:
            _unavailable()
        if type(title) is not str or not title.strip() or any(unicodedata.category(c).startswith('C') for c in title):
            _unavailable()
        try:
            if len(title.encode("utf-8")) > MAX_TITLE_BYTES:
                _unavailable()
        except UnicodeEncodeError:
            _unavailable()
        if year is not None and (type(year) is not int or not 1888 <= year <= 2100):
            _unavailable()
        if "tmdb_id" in selected and selected["tmdb_id"] != tmdb_id:
            _unavailable()
        if "year" in selected and selected["year"] != year:
            _unavailable()
        identity = {"tmdb_id": tmdb_id, "title": title, "year": year}
    if decision == "requestable" and identity is None:
        _unavailable()

    evidence = raw["evidence"]
    if evidence is not None:
        if type(evidence) is not dict or set(evidence) != {
            "read_state", "movie_intelligence", "radarr", "seerr_status",
        } or type(evidence["seerr_status"]) is not int or not 1 <= evidence["seerr_status"] <= 7:
            _unavailable()
        if type(evidence["read_state"]) is not str or not _SOURCE_STATE.fullmatch(evidence["read_state"]):
            _unavailable()
        sources = {}
        for name in ("movie_intelligence", "radarr"):
            item = evidence[name]
            if type(item) is not dict or set(item) != {"status", "count"} or (
                type(item["status"]) is not str or not _SOURCE_STATE.fullmatch(item["status"])
            ) or type(item["count"]) is not int or not 0 <= item["count"] <= 1_000_000:
                _unavailable()
            sources[name] = {"status": item["status"], "count": item["count"]}
        evidence = {
            "read_state": evidence["read_state"],
            "movie_intelligence": sources["movie_intelligence"],
            "radarr": sources["radarr"], "seerr_status": evidence["seerr_status"],
        }

    return {
        "decision": decision,
        "reason": reason,
        "identity": identity,
        "evidence": evidence,
        "would_request": decision == "requestable",
        "mutation_attempted": False,
    }


class FixedNodeMoviePlanner:
    """Trusted fixed CLI path to Homelab's plan-only Node entry point.

    Trusted bootstrap must pin/review the Node/script code, provide only the
    required read-only Movie Intelligence DB and API resources, and restrict
    the filesystem and process identity. No user-supplied path is accepted.
    """

    def __init__(self, *, node_executable: Path, plan_script: Path,
                 trusted_env: Mapping[str, str], timeout_seconds: int = 40):
        if (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60
                or not node_executable.is_absolute() or not plan_script.is_absolute()
                or plan_script.name != "plan-cli.mjs"):
            raise ValueError("invalid_trusted_media_planner")
        if (not isinstance(trusted_env, Mapping) or set(trusted_env) - _ENV_NAMES
                or any(type(v) is not str or not v for v in trusted_env.values())):
            raise ValueError("invalid_trusted_media_planner")
        self.node_executable = str(node_executable)
        self.plan_script = str(plan_script)
        self.env = dict(trusted_env)
        self.timeout_seconds = timeout_seconds

    def __call__(self, selector: dict[str, int | str]) -> dict:
        selected = validate_movie_selector(selector)
        if "tmdb_id" in selected:
            arguments = ["--tmdb-id", str(selected["tmdb_id"])]
        else:
            arguments = ["--title", str(selected["title"]), "--year", str(selected["year"])]
        # No shell, command, --execute, arbitrary argument, inherited environment
        # or caller-provided path. The CLI itself does not expose a write path.
        try:
            proc = subprocess.run(
                [self.node_executable, self.plan_script, *arguments],
                input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=self.env, timeout=self.timeout_seconds, check=False,
            )
            if proc.returncode not in {0, 2} or len(proc.stdout) > MAX_PLAN_BYTES:
                _unavailable()
            raw = json.loads(
                proc.stdout.decode("utf-8"), object_pairs_hook=_pairs_unique,
                parse_constant=lambda _: _unavailable(),
            )
            sanitize_media_plan(selected, raw)
            return raw
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired,
                RecursionError):
            raise MediaPlanUnavailable("media_plan_unavailable") from None


def get_media_plan(selector: object, provider: Callable[[dict], dict]) -> dict:
    """Use an injected, trusted read-only provider; never silently call a fallback."""
    selected = validate_movie_selector(selector)
    try:
        return sanitize_media_plan(selected, provider(selected))
    except MediaPlanUnavailable:
        raise
    except Exception:
        raise MediaPlanUnavailable("media_plan_unavailable") from None
