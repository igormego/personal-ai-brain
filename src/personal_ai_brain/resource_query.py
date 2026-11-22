"""Typed bounded item queries; transport disclosure must be explicitly granted.

GitHub Issues and the durable ledger retain replies. A private mailbox is not
ephemeral storage. No production provider or disclosure grant is wired here.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from unicodedata import normalize

from .arr_request_status import (EXACT_VIEWS, MAX_DIAGNOSTIC_REQUESTS, MEDIA_DIAGNOSTIC_VIEWS,
                                 PRIVATE_DIAGNOSTIC_FIELDS, REQUEST_STATUS_VIEWS, SEERR_REQUEST_STATUSES,
                                 valid_request_status_row, valid_seerr_health_row, valid_title)
from .sonarr_diagnostics import MAX_QUEUE_ITEMS, SONARR_DIAGNOSTIC_VIEWS, valid_sonarr_diagnostic_row

RESOURCE_SCHEMAS = {
    "movies/watchlist": {"views": frozenset({"items"}), "max_page": 10},
    "arr/radarr": {"views": MEDIA_DIAGNOSTIC_VIEWS["arr/radarr"], "max_page": 10},
    "arr/seerr": {"views": MEDIA_DIAGNOSTIC_VIEWS["arr/seerr"], "max_page": 10},
    "arr/sonarr": {"views": frozenset({"items", "profiles"}) | SONARR_DIAGNOSTIC_VIEWS, "max_page": 10},
}
QUERY_FILTERS = {
    ("arr/radarr", "items"): {"title"},
    ("arr/radarr", "queue"): {"related_id"},
    ("arr/seerr", "requests"): {"title", "related_id", "request_status", "media_type"},
}


class QueryUnavailable(ValueError):
    pass


def _deny():
    raise QueryUnavailable("resource_query_unavailable")


def validate_query(value):
    required = {"resource_id", "view", "limit"}
    if type(value) is not dict or not required.issubset(value):
        _deny()
    if type(value["resource_id"]) is not str or value["resource_id"] not in RESOURCE_SCHEMAS:
        _deny()
    spec = RESOURCE_SCHEMAS[value["resource_id"]]
    if type(value["view"]) is not str or value["view"] not in spec["views"]:
        _deny()
    if type(value["limit"]) is not int or not 1 <= value["limit"] <= spec["max_page"]:
        _deny()
    if value["view"] in EXACT_VIEWS:
        target = value.get("target_id")
        if (set(value) != required | {"target_id"} or value["limit"] != 1
                or type(target) is not int or not 1 <= target <= 2147483647):
            _deny()
    else:
        filters = QUERY_FILTERS.get((value["resource_id"], value["view"]), ())
        if set(value) - required - {"cursor"} - set(filters):
            _deny()
    if "title" in value and (not valid_title(value["title"]) or not value["title"].strip()):
        _deny()
    if "related_id" in value and (type(value["related_id"]) is not int or not 1 <= value["related_id"] <= 2147483647):
        _deny()
    if "request_status" in value and (type(value["request_status"]) is not str
                                      or value["request_status"] not in SEERR_REQUEST_STATUSES):
        _deny()
    if "media_type" in value and value["media_type"] not in ("movie", "tv"):
        _deny()
    if "cursor" in value and (type(value["cursor"]) is not str or
            not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value["cursor"])):
        _deny()
    return dict(value)


def _encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value):
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if _encode(raw) != value:
            _deny()
        return raw
    except (ValueError, TypeError):
        _deny()


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _snapshot(source, now):
    if type(source) is not dict or set(source) != {"schema_version", "resource_id", "collected_at", "complete", "source_status", "items"}:
        _deny()
    if source["schema_version"] != "0.1" or source["resource_id"] != "movies/watchlist" or source["complete"] is not True:
        _deny()
    if source["source_status"] != {"movie_db": "ok"}:
        _deny()
    timestamp = source["collected_at"]
    if type(timestamp) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        _deny()
    try:
        age = now - datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        _deny()
    if not -timedelta(minutes=2) <= age <= timedelta(minutes=30):
        _deny()
    items = source["items"]
    if type(items) is not list or len(items) > 10000:
        _deny()
    seen_ids, seen_csfd, seen_tmdb, seen_imdb = set(), set(), set(), set()
    for row in items:
        if type(row) is not dict or set(row) != {"id", "csfd_id", "title", "year", "tmdb_id", "imdb_id", "identity_status"}:
            _deny()
        if type(row["id"]) is not int or not 1 <= row["id"] <= 2147483647:
            _deny()
        if type(row["csfd_id"]) is not str or not re.fullmatch(r"[1-9][0-9]{0,11}", row["csfd_id"]):
            _deny()
        if type(row["title"]) is not str or not 1 <= len(row["title"]) <= 200 or any(ord(ch) < 32 or ord(ch) == 127 for ch in row["title"]):
            _deny()
        if row["year"] is not None and (type(row["year"]) is not int or not 1880 <= row["year"] <= 2200):
            _deny()
        if row["tmdb_id"] is not None and (type(row["tmdb_id"]) is not int or not 1 <= row["tmdb_id"] <= 2147483647):
            _deny()
        if row["imdb_id"] is not None and (type(row["imdb_id"]) is not str or not re.fullmatch(r"tt[0-9]{7,10}", row["imdb_id"])):
            _deny()
        if row["identity_status"] not in ("unresolved", "verified"):
            _deny()
        if row["identity_status"] == "verified" and row["tmdb_id"] is None and row["imdb_id"] is None:
            _deny()
        if row["id"] in seen_ids or row["csfd_id"] in seen_csfd:
            _deny()
        if ((row["tmdb_id"] is not None and row["tmdb_id"] in seen_tmdb)
                or (row["imdb_id"] is not None and row["imdb_id"] in seen_imdb)):
            _deny()
        seen_ids.add(row["id"]); seen_csfd.add(row["csfd_id"])
        if row["tmdb_id"] is not None:
            seen_tmdb.add(row["tmdb_id"])
        if row["imdb_id"] is not None:
            seen_imdb.add(row["imdb_id"])
    if [r["id"] for r in items] != sorted(seen_ids):
        _deny()
    raw = _canonical(source)
    if len(raw) > 2_000_000:
        _deny()
    return hashlib.sha256(raw).hexdigest()


def _arr_snapshot(source, now, *, view):
    """Fail closed on incomplete live ARR GET results; pin cursors to content.

    The live GET timestamp changes for each page. Its generation therefore hashes
    the resource, view and verified item data, NOT the collection timestamp.
    A changed library invalidates the old cursor rather than mixing pages.
    """
    if (type(source) is not dict or set(source) != {
            "schema_version", "resource_id", "view", "collected_at",
            "complete", "source_status", "items"}):
        _deny()
    rid = source["resource_id"]
    if (type(rid) is not str or rid not in ("arr/radarr", "arr/sonarr", "arr/seerr") or source["view"] != view
            or view not in RESOURCE_SCHEMAS[rid]["views"]
            or source["schema_version"] != "0.1" or source["complete"] is not True):
        _deny()
    service = rid.split("/")[1]
    if source["source_status"] != {service: "ok"}:
        _deny()
    timestamp = source["collected_at"]
    if type(timestamp) is not str or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        _deny()
    try:
        age = now - datetime.strptime(
            timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        _deny()
    if not -timedelta(minutes=2) <= age <= timedelta(minutes=2):
        _deny()
    items = source["items"]
    maximum = {"queue": MAX_QUEUE_ITEMS, "requests": MAX_DIAGNOSTIC_REQUESTS,
               "request": MAX_DIAGNOSTIC_REQUESTS}.get(view, 4096)
    if type(items) is not list or len(items) > maximum:
        _deny()
    seen = set()
    for row in items:
        if view in REQUEST_STATUS_VIEWS.get(rid, ()) or view == "health" or (service == "sonarr" and view in SONARR_DIAGNOSTIC_VIEWS):
            if view == "health" and service == "seerr":
                valid = valid_seerr_health_row(row)
            elif view == "health" or service == "sonarr":
                valid = valid_sonarr_diagnostic_row(view, row)
            else:
                valid = valid_request_status_row(rid, view, row)
            if not valid or row["id"] in seen:
                _deny()
            seen.add(row["id"])
            continue
        keys = ({"id", "name"} if view == "profiles"
                else {"id", "title", "year", "monitored", "quality_profile_id",
                      "external_id", "has_file"})
        if type(row) is not dict or set(row) != keys:
            _deny()
        rid_value = row["id"]
        if type(rid_value) is not int or not 1 <= rid_value <= 2147483647 or rid_value in seen:
            _deny()
        seen.add(rid_value)
        name = row["name"] if view == "profiles" else row["title"]
        if not valid_title(name):
            _deny()
        if view == "profiles":
            continue
        if (type(row["year"]) is not int or not 1880 <= row["year"] <= 2200
                or type(row["monitored"]) is not bool
                or type(row["quality_profile_id"]) is not int
                or not 1 <= row["quality_profile_id"] <= 2147483647):
            _deny()
        ext = row["external_id"]
        if ext is not None and (type(ext) is not int or not 1 <= ext <= 2147483647):
            _deny()
        if service == "radarr":
            if type(row["has_file"]) is not bool:
                _deny()
        elif row["has_file"] is not None:
            _deny()
    if [r["id"] for r in items] != sorted(seen):
        _deny()
    generation_input = {"resource_id": source["resource_id"], "view": view, "items": items}
    canonical = _canonical(generation_input)
    if len(canonical) > 2_000_000:
        _deny()
    return hashlib.sha256(canonical).hexdigest()


RESOURCE_SCHEMAS["arr/radarr"]["validate_snapshot"] = _arr_snapshot
RESOURCE_SCHEMAS["arr/sonarr"]["validate_snapshot"] = _arr_snapshot
RESOURCE_SCHEMAS["arr/seerr"]["validate_snapshot"] = _arr_snapshot


# New resources require a reviewed, registered validator and bounded views.
RESOURCE_SCHEMAS["movies/watchlist"]["validate_snapshot"] = _snapshot


class ResourceQueryBroker:
    """Fixed schema registry with independently granted item disclosure."""
    def __init__(self, providers, grants, cursor_key, *, disclosure_approved=False,
                 sonarr_diagnostics=False, view_grants=None, media_diagnostics=frozenset()):
        if type(providers) is not dict or set(providers) - RESOURCE_SCHEMAS.keys():
            _deny()
        if type(grants) is not frozenset or grants - RESOURCE_SCHEMAS.keys():
            _deny()
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 32 or type(disclosure_approved) is not bool:
            _deny()
        if type(sonarr_diagnostics) is not bool:
            _deny()
        if type(media_diagnostics) is not frozenset or media_diagnostics - grants or media_diagnostics - MEDIA_DIAGNOSTIC_VIEWS.keys():
            _deny()
        if view_grants is not None and (type(view_grants) is not dict or any(
                resource not in grants or type(views) is not frozenset or views - RESOURCE_SCHEMAS[resource]["views"]
                for resource, views in view_grants.items())):
            _deny()
        self.providers, self.grants, self.key = dict(providers), grants, cursor_key
        self.disclosure_approved = disclosure_approved
        self.sonarr_diagnostics = sonarr_diagnostics
        self.media_diagnostics = media_diagnostics
        self.view_grants = {} if view_grants is None else dict(view_grants)

    def available_views(self, resource_id):
        if not self.disclosure_approved or resource_id not in self.grants or not callable(self.providers.get(resource_id)):
            return frozenset()
        if resource_id in self.media_diagnostics:
            return MEDIA_DIAGNOSTIC_VIEWS[resource_id]
        defaults = (frozenset({"items", "profiles"}) if resource_id == "arr/radarr"
                    else frozenset() if resource_id == "arr/seerr" else RESOURCE_SCHEMAS[resource_id]["views"])
        views = self.view_grants.get(resource_id, defaults)
        if resource_id in MEDIA_DIAGNOSTIC_VIEWS:
            return views - {"health"}
        if resource_id == "arr/sonarr" and not self.sonarr_diagnostics:
            return views - SONARR_DIAGNOSTIC_VIEWS
        return views

    def read(self, query, *, principal, now=None):
        selected = validate_query(query)
        if principal != "owner" or selected["view"] not in self.available_views(selected["resource_id"]):
            _deny()
        provider = self.providers.get(selected["resource_id"])
        if not callable(provider):
            _deny()
        try:
            resource, view = selected["resource_id"], selected["view"]
            is_arr = resource in ("arr/radarr", "arr/sonarr", "arr/seerr")
            source = provider(view) if is_arr else provider()
            validate_snapshot = RESOURCE_SCHEMAS[resource]["validate_snapshot"]
            generation = (validate_snapshot(source, now or datetime.now(timezone.utc),
                                            view=view) if is_arr
                          else validate_snapshot(source, now or datetime.now(timezone.utc)))
            if source["resource_id"] != resource:
                _deny()
            items = source["items"]
            private_fields = PRIVATE_DIAGNOSTIC_FIELDS.get((resource, view))
            if private_fields and resource not in self.media_diagnostics:
                items = [{key: value for key, value in item.items() if key not in private_fields} for item in items]
            if view in EXACT_VIEWS:
                identity = "tmdb_id" if view == "movie" else "id"
                items = [item for item in items if item[identity] == selected["target_id"]]
                if len(items) > 1:
                    _deny()
            filters = {key: selected[key] for key in QUERY_FILTERS.get((resource, view), ()) if key in selected}
            coverage = {}
            if view == "requests" and resource in self.media_diagnostics:
                items = sorted(items, key=lambda item: (item["created_at"] or "", item["id"]), reverse=True)
            if "related_id" in filters:
                identity = "movie_id" if resource == "arr/radarr" else "tmdb_id"
                items = [item for item in items if item[identity] == filters["related_id"]]
            for key in ("request_status", "media_type"):
                if key in filters:
                    items = [item for item in items if item[key] == filters[key]]
            if "title" in filters:
                if resource == "arr/seerr":
                    if resource not in self.media_diagnostics:
                        _deny()
                    coverage["title_missing_count"] = sum(not item.get("title") for item in items)
                needle = normalize("NFKC", filters["title"]).strip().casefold()
                items = [item for item in items if needle in normalize("NFKC", item.get("title") or "").casefold()]
            if filters:
                coverage["matching_count"] = len(items)
            cursor_generation = generation
            if filters or resource in self.media_diagnostics:
                cursor_generation = hashlib.sha256(
                    _canonical([generation, filters, resource in self.media_diagnostics])).hexdigest()
            offset = 0
            if "cursor" in selected:
                token = _decode(selected["cursor"])
                if len(token) <= 32:
                    _deny()
                raw, signature = token[:-32], token[-32:]
                if not hmac.compare_digest(signature, hmac.new(self.key, raw, hashlib.sha256).digest()):
                    _deny()
                state = json.loads(raw)
                if type(state) is not dict or set(state) != {"resource_id", "view", "generation", "offset", "principal", "limit"}:
                    _deny()
                if (state["resource_id"] != selected["resource_id"] or state["view"] != selected["view"]
                    or state["generation"] != cursor_generation or state["principal"] != principal
                    or type(state["offset"]) is not int or state["offset"] <= 0
                    or state["limit"] != selected["limit"] or state["offset"] >= len(items)):
                    _deny()
                offset = state["offset"]
            end = min(offset + selected["limit"], len(items))
            result = {"resource_id": resource, "view": view, "collected_at": source["collected_at"],
                      "generation": generation, "effect_performed": False, **coverage}
            while True:
                next_cursor = None
                if end < len(items):
                    state = {"resource_id": resource, "view": view, "generation": cursor_generation,
                             "offset": end, "principal": principal, "limit": selected["limit"]}
                    raw = _canonical(state)
                    next_cursor = _encode(raw + hmac.new(self.key, raw, hashlib.sha256).digest())
                result.update(items=items[offset:end], next_cursor=next_cursor)
                if len(_canonical(result)) <= 4096:
                    return result
                if resource not in self.media_diagnostics or end <= offset + 1:
                    _deny()
                end -= 1
        except Exception:
            _deny()
