"""Allowlisted diagnostic facts. Raw ARR messages and paths stay on the host."""

import re

SONARR_DIAGNOSTIC_VIEWS = frozenset({"health", "queue", "series"})
MAX_QUEUE_ITEMS = 200

_CHECKS = {
    "UpdateCheck": "update", "IndexerStatusCheck": "indexer",
    "IndexerRssCheck": "indexer", "IndexerSearchCheck": "indexer",
    "DownloadClientStatusCheck": "download_client",
    "DownloadClientCheck": "download_client",
    "RemotePathMappingCheck": "path_mapping", "MountCheck": "path_mapping",
    "RootFolderCheck": "root_folder", "DiskSpaceCheck": "disk_space",
    "ImportListStatusCheck": "import_list",
}
_HEALTH = frozenset({"ok", "notice", "warning", "error", "unknown"})
_QUEUE_STATUS = frozenset({"queued", "downloading", "paused", "completed", "failed", "delay", "unknown"})
_TRACKED_STATUS = frozenset({"ok", "warning", "error", "unknown"})
_TRACKED_STATE = frozenset({"downloading", "importPending", "importing", "imported", "failed", "ignored", "unknown"})
_SERIES_STATUS = frozenset({"continuing", "ended", "upcoming", "deleted", "unknown"})
_REASONS = {
    "permission": ("permission denied", "access denied", "not writable", "access to the path"),
    "path_missing_or_unmapped": ("path does not exist", "directory does not exist", "remote path mapping", "not accessible"),
    "no_eligible_files": ("no files found are eligible", "no video files", "no files eligible"),
    "already_imported": ("already imported", "existing file on disk"),
    "episode_match": ("unable to identify", "unable to parse", "unable to find an episode", "match an existing episode"),
    "quality": ("not an upgrade", "not a custom format upgrade", "quality cutoff", "lower quality"),
}
_RELEASE_EPISODE_MISSING = re.compile(r"\bepisode\b.{0,120}\bnot found\b.{0,120}\brelease\b")
_MOVIE_REASONS = {
    "movie_match": ("unable to identify", "unable to parse", "unable to find the movie", "expected movie"),
    "disk_space": ("not enough free space", "disk full", "no space left"),
    "download_client": ("unable to connect to", "download client unavailable", "failed to download"),
}
_REASON_CODES = frozenset(_REASONS) | {"release_episode_missing", "other"}


def _number(value, minimum=0):
    return type(value) is int and minimum <= value <= 2147483647


def _optional(value, minimum=0):
    return value if _number(value, minimum) else None


def _enum(value, choices):
    return value if type(value) is str and value in choices else "unknown"


def _messages(row):
    """Collect only enough private text to classify an error, never emit it."""
    error = row.get("errorMessage")
    groups = row.get("statusMessages")
    if type(error) is str and error.strip():
        yield error[:768].casefold()
    if type(groups) is list:
        for group in groups[:12]:
            messages = group.get("messages") if type(group) is dict else None
            if type(messages) is list:
                for message in messages[:8]:
                    if type(message) is str and message.strip():
                        yield message[:768].casefold()


def queue_reasons(row, *, movie=False):
    messages = tuple(_messages(row))
    reasons = [key for key, terms in _REASONS.items()
               if any(term in message for term in terms for message in messages)]
    if movie:
        reasons = [reason for reason in reasons if reason != "episode_match"]
        reasons.extend(key for key, terms in _MOVIE_REASONS.items()
                       if any(term in message for term in terms for message in messages))
    elif any(_RELEASE_EPISODE_MISSING.search(message) for message in messages):
        reasons.append("release_episode_missing")
    return sorted(reasons) or (["other"] if messages else [])


def valid_queue_reasons(reasons, *, movie=False):
    codes = _REASON_CODES
    if movie:
        codes = (_REASON_CODES - {"episode_match", "release_episode_missing"}) | _MOVIE_REASONS.keys()
    return (type(reasons) is list
            and all(type(reason) is str and reason in codes for reason in reasons)
            and reasons == sorted(set(reasons)))


def _queue_item(row):
    return {
        "id": row.get("id"), "series_id": _optional(row.get("seriesId"), 1),
        "episode_id": _optional(row.get("episodeId"), 1),
        "status": _enum(row.get("status"), _QUEUE_STATUS),
        "download_status": _enum(row.get("trackedDownloadStatus"), _TRACKED_STATUS),
        "download_state": _enum(row.get("trackedDownloadState"), _TRACKED_STATE),
        "reasons": queue_reasons(row),
    }


def project_sonarr_diagnostics(view, raw):
    """Map the fixed API response to the same shape the Control verifies."""
    rows = raw.get("records") if view == "queue" and type(raw) is dict else raw
    if (type(rows) is not list or len(rows) > (MAX_QUEUE_ITEMS if view == "queue" else 4096)
            or any(type(row) is not dict for row in rows)):
        raise ValueError("invalid_sonarr_diagnostic_rows")
    if view == "queue":
        if type(raw) is not dict or not _number(raw.get("totalRecords")) or raw["totalRecords"] != len(rows):
            raise ValueError("incomplete_sonarr_queue")
        items = [_queue_item(row) for row in rows]
    elif view == "health":
        checks = sorted((_CHECKS.get(row["source"], "other") if type(row.get("source")) is str else "other",
                         _enum(row.get("type"), _HEALTH)) for row in rows)
        items = [{"id": index, "check": check, "severity": severity}
                 for index, (check, severity) in enumerate(checks, 1)]
    elif view == "series":
        items = []
        for row in rows:
            counts = row.get("statistics")
            counts = counts if type(counts) is dict else {}
            items.append({"id": row.get("id"), "monitored": row.get("monitored"),
                          "quality_profile_id": row.get("qualityProfileId"),
                          "status": _enum(row.get("status"), _SERIES_STATUS),
                          "episode_count": _optional(counts.get("episodeCount")),
                          "episode_file_count": _optional(counts.get("episodeFileCount")),
                          "season_count": _optional(counts.get("seasonCount"))})
    else:
        raise ValueError("unsupported_sonarr_view")
    if any(not valid_sonarr_diagnostic_row(view, row) for row in items):
        raise ValueError("invalid_sonarr_diagnostic_item")
    if len({row["id"] for row in items}) != len(items):
        raise ValueError("duplicate_sonarr_diagnostic_id")
    return sorted(items, key=lambda row: row["id"])


def valid_sonarr_diagnostic_row(view, row):
    if type(row) is not dict or not _number(row.get("id"), 1):
        return False
    if view == "health":
        return (set(row) == {"id", "check", "severity"}
                and type(row["check"]) is str and row["check"] in (*_CHECKS.values(), "other")
                and _enum(row["severity"], _HEALTH) == row["severity"])
    if view == "queue":
        return (set(row) == {"id", "series_id", "episode_id", "status", "download_status", "download_state", "reasons"}
                and all(row[key] is None or _number(row[key], 1) for key in ("series_id", "episode_id"))
                and _enum(row["status"], _QUEUE_STATUS) == row["status"]
                and _enum(row["download_status"], _TRACKED_STATUS) == row["download_status"]
                and _enum(row["download_state"], _TRACKED_STATE) == row["download_state"]
                and valid_queue_reasons(row["reasons"]))
    return (view == "series"
            and set(row) == {"id", "monitored", "quality_profile_id", "status", "episode_count", "episode_file_count", "season_count"}
            and type(row["monitored"]) is bool and _number(row["quality_profile_id"], 1)
            and _enum(row["status"], _SERIES_STATUS) == row["status"]
            and all(row[key] is None or _number(row[key]) for key in ("episode_count", "episode_file_count", "season_count")))
