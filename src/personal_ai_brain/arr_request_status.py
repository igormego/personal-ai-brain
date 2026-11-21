"""Read-only request and acquisition facts without titles or requester data."""
from datetime import datetime, timezone

from .sonarr_diagnostics import MAX_QUEUE_ITEMS, _QUEUE_STATUS, _TRACKED_STATE, _TRACKED_STATUS, _enum, _number

MAX_REQUEST_ITEMS = 100
REQUEST_STATUS_VIEWS = {
    "arr/radarr": frozenset({"movie", "queue"}),
    "arr/seerr": frozenset({"request", "requests"}),
}
EXACT_VIEWS = frozenset({"series", "movie", "request"})
_REQUEST_STATES = dict(enumerate(("pending", "approved", "declined", "failed", "completed"), 1))
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


def project_request_status(resource, view, raw):
    if view not in REQUEST_STATUS_VIEWS.get(resource, ()):
        raise ValueError("unsupported_request_status_view")
    if resource == "arr/seerr":
        page_info = raw.get("pageInfo") if type(raw) is dict else None
        count = page_info.get("results") if type(page_info) is dict else None
        rows = _page(raw, "results", count, MAX_REQUEST_ITEMS)
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
        elif view == "queue":
            movie_id = row.get("movieId")
            item = {"id": row.get("id"), "movie_id": movie_id if _number(movie_id, 1) else None,
                    "status": _enum(row.get("status"), _QUEUE_STATUS),
                    "download_status": _enum(row.get("trackedDownloadStatus"), _TRACKED_STATUS),
                    "download_state": _enum(row.get("trackedDownloadState"), _TRACKED_STATE)}
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
        return (set(item) == {"id", "tmdb_id", "media_type", "request_status", "media_status", "is_4k",
                              "created_at", "updated_at"}
                and _number(item["tmdb_id"], 1) and item["media_type"] in ("movie", "tv")
                and item["request_status"] in (*_REQUEST_STATES.values(), "unknown")
                and item["media_status"] in _MEDIA_STATES.values() and type(item["is_4k"]) is bool
                and all(item[key] is None or _time(item[key]) == item[key] for key in ("created_at", "updated_at")))
    if view == "queue":
        return (set(item) == {"id", "movie_id", "status", "download_status", "download_state"}
                and (item["movie_id"] is None or _number(item["movie_id"], 1))
                and _enum(item["status"], _QUEUE_STATUS) == item["status"]
                and _enum(item["download_status"], _TRACKED_STATUS) == item["download_status"]
                and _enum(item["download_state"], _TRACKED_STATE) == item["download_state"])
    return (view == "movie" and set(item) == {"id", "tmdb_id", "monitored", "has_file", "quality_profile_id", "status"}
            and _number(item["tmdb_id"], 1) and _number(item["quality_profile_id"], 1)
            and type(item["monitored"]) is bool and type(item["has_file"]) is bool
            and _enum(item["status"], _MOVIE_STATES) == item["status"])
