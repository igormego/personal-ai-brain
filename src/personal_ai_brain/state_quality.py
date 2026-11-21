from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import yaml


class TextReader(Protocol):
    def get_text(self, repository: str, path: str, ref: str = "main") -> str:
        ...


@dataclass(slots=True)
class QualityFinding:
    severity: str
    code: str
    subject: str
    message: str


@dataclass(slots=True)
class QualityReport:
    findings: list[QualityFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[QualityFinding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[QualityFinding]:
        return [finding for finding in self.findings if finding.severity == "warning"]


@dataclass(slots=True)
class FreshnessPolicy:
    """Attention-aware freshness thresholds for Project OS state."""

    default_days: int | None
    attention_days: dict[str, int | None]
    disabled_attention: set[str]

    @classmethod
    def default(cls) -> "FreshnessPolicy":
        return cls(
            default_days=14,
            attention_days={
                "urgent_time_bound": 2,
                "active": 7,
                "active_secondary": 14,
                "needs_refresh": 0,
                "background": 30,
            },
            disabled_attention={
                "deferred",
                "deferred_until_feasibility",
                "parked",
                "none",
                "future",
            },
        )

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "FreshnessPolicy":
        default = cls.default()

        default_days = raw.get("default_days", default.default_days)
        if default_days is not None:
            if not isinstance(default_days, int) or isinstance(default_days, bool) or default_days < 0:
                raise ValueError("freshness default_days must be a non-negative integer or null")

        attention_raw = raw.get("attention_days", {})
        if not isinstance(attention_raw, dict):
            raise ValueError("freshness attention_days must be a mapping")

        attention_days = dict(default.attention_days)
        for key, value in attention_raw.items():
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(
                        f"freshness threshold for {key!r} must be a non-negative integer or null"
                    )
            attention_days[str(key)] = value

        disabled_raw = raw.get("disabled_attention", list(default.disabled_attention))
        if not isinstance(disabled_raw, list) or not all(
            isinstance(item, str) and item for item in disabled_raw
        ):
            raise ValueError("freshness disabled_attention must be a list of strings")

        return cls(
            default_days=default_days,
            attention_days=attention_days,
            disabled_attention=set(disabled_raw),
        )

    @classmethod
    def from_yaml_text(cls, text: str) -> "FreshnessPolicy":
        parsed = yaml.safe_load(text)
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("freshness policy YAML must contain a mapping")
        schema_version = parsed.get("schema_version", "0.1")
        if str(schema_version) != "0.1":
            raise ValueError(
                f"unsupported freshness policy schema_version {schema_version!r}"
            )
        return cls.from_mapping(parsed)

    @classmethod
    def from_file(cls, path: Path) -> "FreshnessPolicy":
        return cls.from_yaml_text(path.read_text(encoding="utf-8"))

    def max_age_days(self, attention: str) -> int | None:
        if attention in self.disabled_attention:
            return None
        if attention in self.attention_days:
            return self.attention_days[attention]
        return self.default_days


class ProjectStateQualityChecker:
    """Validate Project OS state schema and attention-aware freshness.

    This layer is deliberately deterministic. It checks the machine contract and
    freshness policy; it does not decide project meaning or rewrite any state.
    """

    SUPPORTED_STATE_SCHEMA = "0.2"
    REQUIRED_TOP_LEVEL = (
        "project",
        "status",
        "phase",
        "current_gate",
        "current_state",
        "exact_next_action",
        "blockers",
        "dependencies",
        "important_open_loops",
        "portfolio",
        "verification",
        "sync",
    )
    STRING_TOP_LEVEL = (
        "status",
        "phase",
        "current_gate",
        "current_state",
        "exact_next_action",
    )
    LIST_TOP_LEVEL = (
        "blockers",
        "dependencies",
        "important_open_loops",
    )
    ALLOWED_SYNC_STATUSES = {"synced", "pending", "failed"}
    NON_FRESH_STATUSES = {
        "future_unassigned",
        "future_project_not_initialized",
        "not_active",
        "deferred_manual",
    }

    def __init__(
        self,
        reader: TextReader,
        *,
        policy: FreshnessPolicy | None = None,
        central_repository: str = "Az1mutt/personal-project-brain",
        project_map_path: str = "core/project-map.yaml",
        ref: str = "main",
        today: date | None = None,
    ) -> None:
        self.reader = reader
        self.policy = policy or FreshnessPolicy.default()
        self.central_repository = central_repository
        self.project_map_path = project_map_path
        self.ref = ref
        self.today = today or date.today()

    def check(self) -> QualityReport:
        report = QualityReport()

        try:
            project_map = self._load_yaml(self.central_repository, self.project_map_path)
        except Exception:
            # CoreAgent already reports an unreadable Project Map.
            return report

        projects = project_map.get("projects")
        if not isinstance(projects, list):
            return report

        for project in projects:
            if not isinstance(project, dict):
                continue
            self._check_project(project, report)

        return report

    def _check_project(
        self,
        project: dict[str, Any],
        report: QualityReport,
    ) -> None:
        project_id = str(project.get("id", "unknown"))
        project_name = str(project.get("name", project_id))
        repository = self._repository_for(project)
        root_path = project.get("state_path")
        if not repository or not root_path:
            return

        workstreams = project.get("workstreams")
        has_workstreams = isinstance(workstreams, list) and bool(workstreams)
        root_scope = "project_rollup" if has_workstreams else "project"
        root_attention = str(project.get("core_attention", "unspecified"))

        self._check_one_state(
            repository=repository,
            path=str(root_path),
            subject=project_name,
            expected_scope=root_scope,
            attention=root_attention,
            map_entry=project,
            report=report,
        )

        if not has_workstreams:
            return

        for workstream in workstreams:
            if not isinstance(workstream, dict) or not workstream.get("state_path"):
                continue
            workstream_id = str(workstream.get("id", "unknown"))
            attention = str(workstream.get("core_attention", root_attention))
            self._check_one_state(
                repository=repository,
                path=str(workstream["state_path"]),
                subject=f"{project_name} / {workstream_id}",
                expected_scope="workstream",
                attention=attention,
                map_entry=workstream,
                report=report,
            )

    def _check_one_state(
        self,
        *,
        repository: str,
        path: str,
        subject: str,
        expected_scope: str,
        attention: str,
        map_entry: dict[str, Any],
        report: QualityReport,
    ) -> None:
        try:
            raw = self._load_yaml(repository, path)
        except Exception:
            # CoreAgent already reports unreadable state files.
            return

        self._check_schema(raw, path, subject, expected_scope, report)
        self._check_freshness(raw, subject, attention, map_entry, report)

    def _check_schema(
        self,
        raw: dict[str, Any],
        expected_path: str,
        subject: str,
        expected_scope: str,
        report: QualityReport,
    ) -> None:
        schema_version = raw.get("schema_version")
        if str(schema_version) != self.SUPPORTED_STATE_SCHEMA:
            self._error(
                report,
                "state_schema_version_unsupported",
                subject,
                (
                    f"Expected schema_version {self.SUPPORTED_STATE_SCHEMA!r}, "
                    f"got {schema_version!r}."
                ),
            )

        missing = [key for key in self.REQUIRED_TOP_LEVEL if key not in raw]
        if missing:
            self._error(
                report,
                "state_required_field_missing",
                subject,
                "Missing required top-level field(s): " + ", ".join(missing),
            )

        for key in self.STRING_TOP_LEVEL:
            if key in raw and not isinstance(raw[key], str):
                self._type_error(report, subject, key, "string", raw[key])

        for key in self.LIST_TOP_LEVEL:
            if key in raw and not isinstance(raw[key], list):
                self._type_error(report, subject, key, "list", raw[key])

        project = raw.get("project")
        if isinstance(project, dict):
            self._require_non_empty_string(project, "name", "project.name", subject, report)
            self._require_non_empty_string(project, "owner", "project.owner", subject, report)
            if expected_scope == "workstream":
                self._require_non_empty_string(
                    project,
                    "workstream",
                    "project.workstream",
                    subject,
                    report,
                )
        elif "project" in raw:
            self._type_error(report, subject, "project", "mapping", project)

        state_scope = raw.get("state_scope")
        if expected_scope in {"project_rollup", "workstream"}:
            if state_scope != expected_scope:
                self._error(
                    report,
                    "state_scope_mismatch",
                    subject,
                    f"Expected state_scope {expected_scope!r}, got {state_scope!r}.",
                )
        elif state_scope is not None and state_scope not in {"project", "project_state"}:
            self._warning(
                report,
                "state_scope_unexpected",
                subject,
                f"Single-project state has unexpected state_scope {state_scope!r}.",
            )

        portfolio = raw.get("portfolio")
        if isinstance(portfolio, dict):
            if "milestone" not in portfolio:
                self._error(report, "state_nested_field_missing", subject, "Missing portfolio.milestone.")
            elif not isinstance(portfolio["milestone"], bool):
                self._type_error(
                    report,
                    subject,
                    "portfolio.milestone",
                    "boolean",
                    portfolio["milestone"],
                )
            if "signal" not in portfolio:
                self._error(report, "state_nested_field_missing", subject, "Missing portfolio.signal.")
            elif not isinstance(portfolio["signal"], str):
                self._type_error(
                    report,
                    subject,
                    "portfolio.signal",
                    "string",
                    portfolio["signal"],
                )
        elif "portfolio" in raw:
            self._type_error(report, subject, "portfolio", "mapping", portfolio)

        verification = raw.get("verification")
        if isinstance(verification, dict):
            if "last_verified" not in verification:
                self._error(
                    report,
                    "state_nested_field_missing",
                    subject,
                    "Missing verification.last_verified.",
                )
            else:
                value = verification["last_verified"]
                if not isinstance(value, str):
                    self._type_error(
                        report,
                        subject,
                        "verification.last_verified",
                        "string",
                        value,
                    )
                elif value and self._parse_date(value) is None:
                    self._error(
                        report,
                        "verification_date_invalid",
                        subject,
                        f"verification.last_verified is not ISO date YYYY-MM-DD: {value!r}.",
                    )
            if "sources" not in verification:
                self._error(
                    report,
                    "state_nested_field_missing",
                    subject,
                    "Missing verification.sources.",
                )
            elif not isinstance(verification["sources"], list):
                self._type_error(
                    report,
                    subject,
                    "verification.sources",
                    "list",
                    verification["sources"],
                )
        elif "verification" in raw:
            self._type_error(report, subject, "verification", "mapping", verification)

        sync = raw.get("sync")
        if isinstance(sync, dict):
            if "status" not in sync:
                self._error(report, "state_nested_field_missing", subject, "Missing sync.status.")
            elif not isinstance(sync["status"], str):
                self._type_error(report, subject, "sync.status", "string", sync["status"])
            elif sync["status"] not in self.ALLOWED_SYNC_STATUSES:
                self._warning(
                    report,
                    "sync_status_unknown",
                    subject,
                    f"Unrecognized sync.status {sync['status']!r}.",
                )

            if "storage" not in sync:
                self._error(report, "state_nested_field_missing", subject, "Missing sync.storage.")
            elif not isinstance(sync["storage"], str):
                self._type_error(report, subject, "sync.storage", "string", sync["storage"])
            elif sync["storage"] != expected_path:
                self._error(
                    report,
                    "sync_storage_mismatch",
                    subject,
                    (
                        f"sync.storage is {sync['storage']!r}, but Project Map resolved "
                        f"this state from {expected_path!r}."
                    ),
                )
        elif "sync" in raw:
            self._type_error(report, subject, "sync", "mapping", sync)

    def _check_freshness(
        self,
        raw: dict[str, Any],
        subject: str,
        attention: str,
        map_entry: dict[str, Any],
        report: QualityReport,
    ) -> None:
        status = str(raw.get("status", "unknown"))
        if status in self.NON_FRESH_STATUSES:
            return

        threshold = self._threshold_for(attention, map_entry, subject, report)
        if threshold is None:
            return

        verification = raw.get("verification")
        if not isinstance(verification, dict):
            return
        raw_date = verification.get("last_verified")
        if not isinstance(raw_date, str) or not raw_date:
            # CoreAgent already reports a missing verification date for active states.
            return

        verified = self._parse_date(raw_date)
        if verified is None:
            return

        age_days = (self.today - verified).days
        if age_days < 0:
            self._warning(
                report,
                "verification_date_in_future",
                subject,
                (
                    f"verification.last_verified {raw_date} is {-age_days} day(s) "
                    f"ahead of checker date {self.today.isoformat()}."
                ),
            )
            return

        if age_days > threshold:
            self._warning(
                report,
                "state_stale_by_policy",
                subject,
                (
                    f"State is {age_days} day(s) old; attention {attention!r} allows "
                    f"at most {threshold} day(s) without refresh."
                ),
            )

    def _threshold_for(
        self,
        attention: str,
        map_entry: dict[str, Any],
        subject: str,
        report: QualityReport,
    ) -> int | None:
        if "freshness_days" not in map_entry:
            return self.policy.max_age_days(attention)

        override = map_entry.get("freshness_days")
        if override is None:
            return None
        if not isinstance(override, int) or isinstance(override, bool) or override < 0:
            self._warning(
                report,
                "freshness_override_invalid",
                subject,
                f"Ignoring invalid freshness_days override {override!r}.",
            )
            return self.policy.max_age_days(attention)
        return override

    def _repository_for(self, project: dict[str, Any]) -> str | None:
        if project.get("storage") == "project_repo":
            repository = project.get("repository")
            return str(repository) if repository else None
        return self.central_repository

    def _load_yaml(self, repository: str, path: str) -> dict[str, Any]:
        parsed = yaml.safe_load(self.reader.get_text(repository, path, self.ref))
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected YAML mapping at {repository}:{path}")
        return parsed

    @staticmethod
    def _parse_date(value: str) -> date | None:
        try:
            parsed = date.fromisoformat(value)
            return parsed if parsed.isoformat() == value else None
        except ValueError:
            return None

    @staticmethod
    def _require_non_empty_string(
        mapping: dict[str, Any],
        key: str,
        display_key: str,
        subject: str,
        report: QualityReport,
    ) -> None:
        value = mapping.get(key)
        if not isinstance(value, str) or not value.strip():
            report.findings.append(
                QualityFinding(
                    "error",
                    "state_nested_field_invalid",
                    subject,
                    f"{display_key} must be a non-empty string.",
                )
            )

    @staticmethod
    def _type_error(
        report: QualityReport,
        subject: str,
        key: str,
        expected: str,
        value: Any,
    ) -> None:
        report.findings.append(
            QualityFinding(
                "error",
                "state_field_type_invalid",
                subject,
                f"{key} must be {expected}; got {type(value).__name__}.",
            )
        )

    @staticmethod
    def _error(
        report: QualityReport,
        code: str,
        subject: str,
        message: str,
    ) -> None:
        report.findings.append(QualityFinding("error", code, subject, message))

    @staticmethod
    def _warning(
        report: QualityReport,
        code: str,
        subject: str,
        message: str,
    ) -> None:
        report.findings.append(QualityFinding("warning", code, subject, message))
