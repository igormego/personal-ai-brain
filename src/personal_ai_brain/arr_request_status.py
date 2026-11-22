"""Read-only request and acquisition facts with explicit disclosure boundaries."""
from datetime import datetime, timezone

from .sonarr_diagnostics import (MAX_QUEUE_ITEMS, _QUEUE_STATUS, _TRACKED_STATE, _TRACKED_STATUS,
                                 _enum, _number, _optional, queue_reasons, valid_queue_reasons)

MAX_REQUEST_ITEMS = 100
MAX_DIAGNOSTIC_REQUESTS = 2000
REQUEST_STATUS_VIEWS = {
    "arr/radarr": frozenset({"movie", "queue"}),
    "arr/seerr": frozenset({"request", "requests"}),
}
MEDIA_DIAGNOSTIC_VIEWS = {
    "arr/radarr": REQUEST_STATUS_VIEWS["arr/radarr"] | {"items", "profiles", "health"},
    "arr/seerr": REQUEST_STATUS_VIEWS["arr/seerr"] | {"health"},
}
PRIVATE_DIAGNOSTIC_FIELDS = {
    ("arr/seerr", "request"): frozenset({"title", "profile_id", "profile_name", "service_id"}),
    ("arr/seerr", "requests"): frozenset({"title", "profile_id", "profile_name", "service_id"}),
    ("arr/radarr", "queue"): frozenset({"reasons"}),
}
EXACT_VIEWS = frozenset({"series", "movie", "request"})
_REQUEST_STATES = dict(enumerate(("pending", "approved", "declined", "failed", "completed"), 1))
SEERR_REQUEST_STATUSES = frozenset(_REQUEST_STATES.values()) | {"unknown"}
_MEDIA_STATES = dict(enumerate(("unknown", "pending", "processing", "partially_available", "available",
                               "blocklisted", "deleted"), 1))
_MOVIE_STATES = frozenset({"announced", "inCinemas", "released", "deleted", "unknown"})


def _time(value):
    if type(value) is not str or len(value) > 40:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if date.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _status(mapping, code):
    return mapping.get(code, "unknown") if type(code) is int else "unknown"


def _page(raw, key, count, maximum):
    data = raw.get(key) if type(raw) is dict else None
    if type(data) is not list or len(data) > maximum or not _number(count) or count != len(data):
        raise ValueError("incomplete_request_status_page")
    return data


def valid_title(value):
    return (type(value) is str and 1 <= len(value) <= 200
            and not any(ord(ch) < 32 or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in value))


def project_request_status(resource, view, raw, *, diagnostics=False):
    if view not in REQUEST_STATUS_VIEWS.get(resource, ()):
        raise ValueError("unsupported_request_status_view")
    if resource == "arr/seerr":
        page_info = raw.get("pageInfo") if type(raw) is dict else None
        count = page_info.get("results") if type(page_info) is dict else None
        rows = _page(raw, "results", count, MAX_DIAGNOSTIC_REQUESTS if diagnostics else MAX_REQUEST_ITEMS)
    elif view == "queue":
        rows = _page(raw, "records", raw.get("totalRecords") if type(raw) is dict else None, MAX_QUEUE_ITEMS)
    else:
        rows = raw
    if type(rows) is not list or len(rows) > 4096 or any(type(row) is not dict for row in rows):
        raise ValueError("invalid_request_status_rows")

    items = []
    for row in rows:
        if resource == "arr/seerr":
            media = row.get("media")
            if type(media) is not dict or type(row.get("is4k")) is not bool:
                raise ValueError("invalid_seerr_request_identity")
            item = {"id": row.get("id"), "tmdb_id": media.get("tmdbId"), "media_type": row.get("type"),
                    "request_status": _status(_REQUEST_STATES, row.get("status")),
                    "media_status": _status(_MEDIA_STATES, media.get("status4k" if row["is4k"] else "status")),
                    "is_4k": row["is4k"], "created_at": _time(row.get("createdAt")),
                    "updated_at": _time(row.get("updatedAt"))}
            if diagnostics:
                profile_name = row.get("profileName")
                item.update(title=None, profile_id=_optional(row.get("profileId"), 1),
                            profile_name=profile_name if valid_title(profile_name) else None,
                            service_id=_optional(row.get("serverId")))
        elif view == "queue":
            movie_id = row.get("movieId")
            item = {"id": row.get("id"), "movie_id": movie_id if _number(movie_id, 1) else None,
                    "status": _enum(row.get("status"), _QUEUE_STATUS),
                    "download_status": _enum(row.get("trackedDownloadStatus"), _TRACKED_STATUS),
                    "download_state": _enum(row.get("trackedDownloadState"), _TRACKED_STATE)}
            if diagnostics:
                item["reasons"] = queue_reasons(row, movie=True)
        else:
            item = {"id": row.get("id"), "tmdb_id": row.get("tmdbId"), "monitored": row.get("monitored"),
                    "has_file": row.get("hasFile"), "quality_profile_id": row.get("qualityProfileId"),
                    "status": _enum(row.get("status"), _MOVIE_STATES)}
        if not valid_request_status_row(resource, view, item):
            raise ValueError("invalid_request_status_row")
        items.append(item)
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("duplicate_request_status_id")
    return sorted(items, key=lambda item: item["id"])


def valid_request_status_row(resource, view, item):
    if type(item) is not dict or not _number(item.get("id"), 1):
        return False
    if resource == "arr/seerr":
        fields = {"id", "tmdb_id", "media_type", "request_status", "media_status", "is_4k",
                  "created_at", "updated_at"}
        diagnostics = PRIVATE_DIAGNOSTIC_FIELDS[(resource, view)]
        richer = set(item) == fields | diagnostics
        return ((set(item) == fields or richer)
                and _number(item["tmdb_id"], 1) and item["media_type"] in ("movie", "tv")
                and item["request_status"] in SEERR_REQUEST_STATUSES
                and item["media_status"] in _MEDIA_STATES.values() and type(item["is_4k"]) is bool
                and all(item[key] is None or _time(item[key]) == item[key] for key in ("created_at", "updated_at"))
                and (not richer or (
                    all(item[key] is None or valid_title(item[key]) for key in ("title", "profile_name"))
                    and (item["profile_id"] is None or _number(item["profile_id"], 1))
                    and (item["service_id"] is None or _number(item["service_id"])))))
    if view == "queue":
        fields = {"id", "movie_id", "status", "download_status", "download_state"}
        return ((set(item) == fields or (set(item) == fields | {"reasons"}
                                        and valid_queue_reasons(item["reasons"], movie=True)))
                and (item["movie_id"] is None or _number(item["movie_id"], 1))
                and _enum(item["status"], _QUEUE_STATUS) == item["status"]
                and _enum(item["download_status"], _TRACKED_STATUS) == item["download_status"]
                and _enum(item["download_state"], _TRACKED_STATE) == item["download_state"])
    return (view == "movie" and set(item) == {"id", "tmdb_id", "monitored", "has_file", "quality_profile_id", "status"}
            and _number(item["tmdb_id"], 1) and _number(item["quality_profile_id"], 1)
            and type(item["monitored"]) is bool and type(item["has_file"]) is bool
            and _enum(item["status"], _MOVIE_STATES) == item["status"])


def project_seerr_health(raw):
    errors = raw.get("serviceErrors") if type(raw) is dict else None
    if type(errors) is not dict or set(errors) != {"radarr", "sonarr"}:
        raise ValueError("invalid_seerr_service_errors")
    services = []
    for service, rows in sorted(errors.items()):
        if type(rows) is not list or len(rows) > 64:
            raise ValueError("invalid_seerr_service_errors")
        for row in rows:
            if type(row) is not dict or not _number(row.get("id")):
                raise ValueError("invalid_seerr_service_error")
            services.append((service, row["id"]))
    return [{"id": index, "service": service, "service_id": service_id,
             "check": "profile_lookup_failed", "severity": "error"}
            for index, (service, service_id) in enumerate(sorted(set(services)), 1)]


def valid_seerr_health_row(row):
    return (type(row) is dict and set(row) == {"id", "service", "service_id", "check", "severity"}
            and _number(row["id"], 1) and _number(row["service_id"])
            and row["service"] in ("radarr", "sonarr")
            and row["check"] == "profile_lookup_failed" and row["severity"] == "error")
