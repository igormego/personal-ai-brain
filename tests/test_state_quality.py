from __future__ import annotations

from datetime import date

import pytest
import yaml

from personal_ai_brain.state_quality import FreshnessPolicy, ProjectStateQualityChecker


class FakeReader:
    def __init__(self, files: dict[tuple[str, str, str], str]) -> None:
        self.files = files

    def get_text(self, repository: str, path: str, ref: str = "main") -> str:
        key = (repository, path, ref)
        if key not in self.files:
            raise RuntimeError(f"missing fixture: {key}")
        return self.files[key]


def dump(value: dict) -> str:
    return yaml.safe_dump(value, sort_keys=False)


def valid_state(
    *,
    storage: str,
    verified: str = "2026-09-17",
    status: str = "active",
    scope: str | None = None,
    workstream: str | None = None,
) -> dict:
    project = {
        "name": "Example",
        "owner": "Example owner",
    }
    if workstream is not None:
        project["workstream"] = workstream

    state = {
        "schema_version": "0.2",
        "project": project,
        "status": status,
        "phase": "build",
        "current_gate": "Validate the next gate.",
        "current_state": "Current verified state.",
        "exact_next_action": "Run the next test.",
        "blockers": [],
        "dependencies": [],
        "important_open_loops": [],
        "portfolio": {"milestone": False, "signal": ""},
        "verification": {"last_verified": verified, "sources": ["fixture"]},
        "sync": {"status": "synced", "storage": storage},
    }
    if scope is not None:
        state["state_scope"] = scope
    return state


def test_valid_single_project_state_has_no_quality_findings() -> None:
    central = "Az1mutt/personal-project-brain"
    path = "projects/example/state.yaml"
    project_map = {
        "projects": [
            {
                "id": "example",
                "name": "Example",
                "storage": "central",
                "state_path": path,
                "core_attention": "active",
            }
        ]
    }
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, path, "main"): dump(valid_state(storage=path)),
        }
    )

    report = ProjectStateQualityChecker(
        reader,
        today=date(2026, 9, 17),
    ).check()

    assert report.findings == []


@pytest.mark.parametrize(
    ("verified", "expected_codes"),
    [
        ("2026-10-27", []),
        ("2024-02-29", ["state_stale_by_policy"]),
        ("2026-10-20", []),
        ("2026-10-19", ["state_stale_by_policy"]),
        ("2026-10-28", ["verification_date_in_future"]),
        *[
            (value, ["verification_date_invalid"])
            for value in (
                "2026-02-29",
                "2026-04-31",
                "2026-13-01",
                "20261027",
                "2026-W44-2",
                "20261019",
                "2026-W44-3",
                "2026-10-27T00:00:00",
                "2026-10-27T00:00:00Z",
                "2026-10-27T00:00:00+00:00",
                "2026-10-27 00:00:00",
                " 2026-10-27",
                "2026-10-27 ",
                "2026-10-27\n",
                "2026-1-2",
            )
        ],
    ],
)
def test_verification_date_requires_canonical_calendar_date(
    verified: str, expected_codes: list[str]
) -> None:
    central = "Az1mutt/personal-project-brain"
    path = "projects/example/state.yaml"
    project_map = {
        "projects": [
            {
                "id": "example",
                "name": "Example",
                "storage": "central",
                "state_path": path,
                "core_attention": "active",
            }
        ]
    }
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, path, "main"): dump(valid_state(storage=path, verified=verified)),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 10, 27)).check()

    assert [finding.code for finding in report.findings] == expected_codes


def test_sync_storage_mismatch_is_an_error() -> None:
    central = "Az1mutt/personal-project-brain"
    path = "projects/example/state.yaml"
    project_map = {
        "projects": [
            {
                "id": "example",
                "name": "Example",
                "storage": "central",
                "state_path": path,
                "core_attention": "active",
            }
        ]
    }
    state = valid_state(storage="projects/wrong/state.yaml")
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, path, "main"): dump(state),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 9, 17)).check()

    assert any(f.code == "sync_storage_mismatch" for f in report.errors)


def test_workstream_requires_workstream_scope_and_name() -> None:
    central = "Az1mutt/personal-project-brain"
    repo = "Az1mutt/example"
    root_path = ".project/state.yaml"
    child_path = ".project/workstreams/infra.yaml"
    project_map = {
        "projects": [
            {
                "id": "example",
                "name": "Example",
                "storage": "project_repo",
                "repository": repo,
                "state_path": root_path,
                "core_attention": "active",
                "workstreams": [
                    {
                        "id": "infra",
                        "state_path": child_path,
                        "core_attention": "active",
                    }
                ],
            }
        ]
    }
    root = valid_state(storage=root_path, scope="project_rollup")
    child = valid_state(storage=child_path, scope="project_rollup")
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (repo, root_path, "main"): dump(root),
            (repo, child_path, "main"): dump(child),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 9, 17)).check()

    codes = [f.code for f in report.errors]
    assert "state_scope_mismatch" in codes
    assert "state_nested_field_invalid" in codes


def test_attention_policy_warns_for_active_but_not_background_or_deferred() -> None:
    central = "Az1mutt/personal-project-brain"
    project_map = {
        "projects": [
            {
                "id": "active",
                "name": "Active",
                "storage": "central",
                "state_path": "projects/active/state.yaml",
                "core_attention": "active",
            },
            {
                "id": "background",
                "name": "Background",
                "storage": "central",
                "state_path": "projects/background/state.yaml",
                "core_attention": "background",
            },
            {
                "id": "deferred",
                "name": "Deferred",
                "storage": "central",
                "state_path": "projects/deferred/state.yaml",
                "core_attention": "deferred",
            },
        ]
    }
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, "projects/active/state.yaml", "main"): dump(
                valid_state(storage="projects/active/state.yaml", verified="2026-09-09")
            ),
            (central, "projects/background/state.yaml", "main"): dump(
                valid_state(storage="projects/background/state.yaml", verified="2026-08-28")
            ),
            (central, "projects/deferred/state.yaml", "main"): dump(
                valid_state(storage="projects/deferred/state.yaml", verified="2026-01-01")
            ),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 9, 17)).check()

    stale_subjects = {
        finding.subject
        for finding in report.warnings
        if finding.code == "state_stale_by_policy"
    }
    assert stale_subjects == {"Active"}


def test_needs_refresh_attention_is_stale_after_zero_days() -> None:
    central = "Az1mutt/personal-project-brain"
    path = "projects/career/state.yaml"
    project_map = {
        "projects": [
            {
                "id": "career",
                "name": "Career",
                "storage": "central",
                "state_path": path,
                "core_attention": "needs_refresh",
            }
        ]
    }
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, path, "main"): dump(
                valid_state(storage=path, verified="2026-09-16")
            ),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 9, 17)).check()

    assert any(f.code == "state_stale_by_policy" for f in report.warnings)


def test_project_map_freshness_override_can_relax_policy() -> None:
    central = "Az1mutt/personal-project-brain"
    path = "projects/example/state.yaml"
    project_map = {
        "projects": [
            {
                "id": "example",
                "name": "Example",
                "storage": "central",
                "state_path": path,
                "core_attention": "active",
                "freshness_days": 60,
            }
        ]
    }
    reader = FakeReader(
        {
            (central, "core/project-map.yaml", "main"): dump(project_map),
            (central, path, "main"): dump(
                valid_state(storage=path, verified="2026-08-10")
            ),
        }
    )

    report = ProjectStateQualityChecker(reader, today=date(2026, 9, 17)).check()

    assert not any(f.code == "state_stale_by_policy" for f in report.warnings)


def test_freshness_policy_can_be_overridden_from_yaml() -> None:
    policy = FreshnessPolicy.from_yaml_text(
        """
        schema_version: "0.1"
        default_days: 20
        attention_days:
          active: 10
        disabled_attention:
          - parked
        """
    )

    assert policy.max_age_days("active") == 10
    assert policy.max_age_days("parked") is None
    assert policy.max_age_days("unknown") == 20
