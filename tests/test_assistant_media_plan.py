"""Offline Assistant/Homelab movie plan bridge regression checks."""

import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from personal_ai_brain.assistant_media_plan import (
    FixedNodeMoviePlanner, MediaPlanUnavailable, get_media_plan, sanitize_media_plan,
)
from personal_ai_brain.control import (
    CapabilityFailed, Redactor, Rejected, build_registry,
)


def wire(**changes):
    original = {
        "contract_version": "0.1",
        "decision": "requestable",
        "reason": "stable_identity_and_clear_preflight",
        "identity": {"tmdb_id": 244786, "title": "Whiplash", "year": 2014},
        "evidence": {
            "read_state": "not_found",
            "movie_intelligence": {"status": "ok", "count": 0},
            "radarr": {"status": "ok", "count": 0},
            "seerr_status": 1,
        },
        "mutation_attempted": False,
    }
    return {**original, **changes}


def test_plan_read_result_is_bounded_and_no_write_requested():
    result = get_media_plan(
        {"title": "Whiplash", "year": 2014}, lambda _: wire()
    )
    assert result == {
        "decision": "requestable",
        "reason": "stable_identity_and_clear_preflight",
        "identity": {"tmdb_id": 244786, "title": "Whiplash", "year": 2014},
        "evidence": {
            "read_state": "not_found",
            "movie_intelligence": {"status": "ok", "count": 0},
            "radarr": {"status": "ok", "count": 0},
            "seerr_status": 1,
        },
        "would_request": True, "mutation_attempted": False,
    }


@pytest.mark.parametrize("decision,reason", [
    ("already_available", "file_or_seerr_available"),
    ("already_requested", "existing_request_or_processing"),
    ("already_managed", "radarr_manages_movie"),
    ("blocked", "media_policy_dependency"),
    ("unresolved", "source_or_configuration_unavailable"),
])
def test_known_non_requestable_decisions_are_not_misreported(decision, reason):
    result = get_media_plan(
        {"tmdb_id": 244786},
        lambda _: wire(decision=decision, reason=reason),
    )
    assert result["decision"] == decision
    assert result["would_request"] is False


@pytest.mark.parametrize("bad", [
    {"identity": {"tmdb_id": 244787, "title": "Whiplash", "year": 2014}},
    {"identity": {"tmdb_id": 244786, "title": "Whiplash", "year": True}},
    {"decision": "requested"},
    {"decision": "download_in_progress"},
    {"decision": "requestable", "identity": None},
    {"decision": "requestable", "evidence": None},
    {"decision": "requestable", "reason": "media_policy_dependency"},
    {"mutation_attempted": True},
    {"evidence": {"private_path": "/secret"}},
    {"evidence": {
        "read_state": "ok", "movie_intelligence": {"status": "ok", "count": True},
        "radarr": {"status": "ok", "count": 0}, "seerr_status": 1,
    }},
    {"identity": {"tmdb_id": 244786, "title": "private-secret\npath", "year": 2014}},
    {"intended_action": {"method": "POST", "url": "https://example.com"}},
    {"api_key": "private-secret"},
    {"source": {"raw": "private-secret"}},
])
def test_provider_malformed_or_mutation_result_fails_closed(bad):
    result = wire()
    result.update(bad)
    with pytest.raises(MediaPlanUnavailable, match="^media_plan_unavailable$"):
        get_media_plan({"tmdb_id": 244786}, lambda _: result)


def test_title_and_year_lookup_requires_matching_resolved_year():
    with pytest.raises(MediaPlanUnavailable, match="^media_plan_unavailable$"):
        get_media_plan(
            {"title": "Whiplash", "year": 2014},
            lambda _: wire(identity={"tmdb_id": 244786, "title": "Whiplash", "year": 2015}),
        )


def test_provider_exception_never_exposes_secret():
    def error(_):
        raise RuntimeError("SECRET token failed")
    with pytest.raises(MediaPlanUnavailable, match="^media_plan_unavailable$"):
        get_media_plan({"tmdb_id": 1}, error)


def test_registry_is_opt_in_and_cannot_submit_or_download(tmp_path):
    regular = build_registry(tmp_path, None, Redactor())
    for name in ("media.plan_movie", "media.request_movie", "media.download", "media.reacquire_movie"):
        with pytest.raises(Rejected, match="^unknown_capability$"):
            regular.resolve(name, {"tmdb_id": 244786})

    received = []
    def provider(selector):
        received.append(selector)
        return wire()

    registry = build_registry(
        tmp_path, None, Redactor(), media_plan_provider=provider,
    )
    cap, args = registry.resolve("media.plan_movie", {"tmdb_id": 244786})
    assert cap.risk_level == "read_only"
    assert cap.side_effect_class == "none"
    assert cap.handler(args)["would_request"] is True
    assert received == [{"tmdb_id": 244786}]
    for name in ("media.request_movie", "media.download", "media.reacquire_movie"):
        with pytest.raises(Rejected, match="^unknown_capability$"):
            registry.resolve(name, {"tmdb_id": 244786})

    for invalid in (
        {"tmdb_id": 244786, "execute": True},
        {"tmdb_id": 244786, "endpoint": "/api/v1/request"},
        {"tmdb_id": 244786, "server": "attacker"},
        {"title": "Whiplash"}, {"tmdb_id": 244786, "command": "id"},
    ):
        with pytest.raises(Rejected, match="^invalid_movie_selector$"):
            registry.resolve("media.plan_movie", invalid)
    assert received == [{"tmdb_id": 244786}]


def test_registry_sanitizes_provider_failure(tmp_path):
    def provider(_):
        raise ValueError("private-secret")
    registry = build_registry(
        tmp_path, None, Redactor(), media_plan_provider=provider,
    )
    cap, args = registry.resolve("media.plan_movie", {"tmdb_id": 244786})
    with pytest.raises(CapabilityFailed, match="^media_plan_unavailable$"):
        cap.handler(args)


def test_fixed_command_only_plan_arguments_and_isolated_environment(monkeypatch):
    seen = []
    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return CompletedProcess(argv, 0, json.dumps(wire()).encode(), b"")
    monkeypatch.setattr("personal_ai_brain.assistant_media_plan.subprocess.run", fake_run)
    adapter = FixedNodeMoviePlanner(
        node_executable=Path("/usr/bin/node"),
        plan_script=Path("/opt/media/plan-cli.mjs"),
        trusted_env={"SEERR_BASE_URL": "http://trusted", "SEERR_API_KEY": "private-secret"},
    )
    result = get_media_plan({"tmdb_id": 244786}, adapter)
    assert result["decision"] == "requestable"
    args, opts = seen[0]
    assert args == ["/usr/bin/node", "/opt/media/plan-cli.mjs", "--tmdb-id", "244786"]
    assert "--execute" not in args
    assert opts["env"] == {"SEERR_BASE_URL": "http://trusted", "SEERR_API_KEY": "private-secret"}
    assert opts["stderr"] is not None
    assert opts["timeout"] == 40
    assert "private-secret" not in repr(args)

    seen.clear()
    get_media_plan({"title": "Whiplash", "year": 2014}, adapter)
    assert seen[0][0] == [
        "/usr/bin/node", "/opt/media/plan-cli.mjs", "--title", "Whiplash", "--year", "2014",
    ]


@pytest.mark.parametrize("result", [
    CompletedProcess([], 9, b"", b"secret"),
    CompletedProcess([], 0, b"x" * 5000, b""),
    CompletedProcess([], 0, b"secret", b""),
    CompletedProcess([], 0, b'{"decision":"bad","decision":"bad"}', b""),
])
def test_fixed_command_invalid_result_fails_closed(monkeypatch, result):
    monkeypatch.setattr(
        "personal_ai_brain.assistant_media_plan.subprocess.run",
        lambda *a, **kw: result,
    )
    adapter = FixedNodeMoviePlanner(
        node_executable=Path("/usr/bin/node"),
        plan_script=Path("/opt/media/plan-cli.mjs"),
        trusted_env={},
    )
    with pytest.raises(MediaPlanUnavailable, match="^media_plan_unavailable$"):
        get_media_plan({"tmdb_id": 244786}, adapter)


def test_fixed_command_timeout_fails_closed(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutExpired(cmd="private-command", timeout=40)
    monkeypatch.setattr("personal_ai_brain.assistant_media_plan.subprocess.run", fail)
    adapter = FixedNodeMoviePlanner(
        node_executable=Path("/usr/bin/node"),
        plan_script=Path("/opt/media/plan-cli.mjs"),
        trusted_env={},
    )
    with pytest.raises(MediaPlanUnavailable, match="^media_plan_unavailable$"):
        get_media_plan({"tmdb_id": 244786}, adapter)


def test_reject_untrusted_program_and_environments():
    for opts in (
        {"node_executable": Path("node"), "plan_script": Path("/opt/media/plan-cli.mjs"), "trusted_env": {}},
        {"node_executable": Path("/usr/bin/node"), "plan_script": Path("/opt/media/request-cli.mjs"), "trusted_env": {}},
        {"node_executable": Path("/usr/bin/node"), "plan_script": Path("/opt/media/plan-cli.mjs"), "trusted_env": {"NODE_OPTIONS": "--require evil"}},
        {"node_executable": Path("/usr/bin/node"), "plan_script": Path("/opt/media/plan-cli.mjs"), "trusted_env": {}, "timeout_seconds": 0},
    ):
        with pytest.raises(ValueError, match="invalid_trusted_media_planner"):
            FixedNodeMoviePlanner(**opts)
