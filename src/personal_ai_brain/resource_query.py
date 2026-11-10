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

RESOURCE_SCHEMAS = {"movies/watchlist": {"views": frozenset({"items"}), "max_page": 10}}


class QueryUnavailable(ValueError):
    pass


def _deny():
    raise QueryUnavailable("resource_query_unavailable")


def validate_query(value):
    if type(value) is not dict or set(value) not in (
        {"resource_id", "view", "limit"}, {"resource_id", "view", "limit", "cursor"}
    ):
        _deny()
    if type(value["resource_id"]) is not str or value["resource_id"] not in RESOURCE_SCHEMAS:
        _deny()
    spec = RESOURCE_SCHEMAS[value["resource_id"]]
    if type(value["view"]) is not str or value["view"] not in spec["views"]:
        _deny()
    if type(value["limit"]) is not int or not 1 <= value["limit"] <= spec["max_page"]:
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


# New resources require a reviewed, registered validator and bounded views.
RESOURCE_SCHEMAS["movies/watchlist"]["validate_snapshot"] = _snapshot


class ResourceQueryBroker:
    """Fixed schema registry with independently granted item disclosure."""
    def __init__(self, providers, grants, cursor_key, *, disclosure_approved=False):
        if type(providers) is not dict or set(providers) - RESOURCE_SCHEMAS.keys():
            _deny()
        if type(grants) is not frozenset or grants - RESOURCE_SCHEMAS.keys():
            _deny()
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 32 or type(disclosure_approved) is not bool:
            _deny()
        self.providers, self.grants, self.key = dict(providers), grants, cursor_key
        self.disclosure_approved = disclosure_approved

    def read(self, query, *, principal, now=None):
        selected = validate_query(query)
        if principal != "owner" or selected["resource_id"] not in self.grants or not self.disclosure_approved:
            _deny()
        provider = self.providers.get(selected["resource_id"])
        if not callable(provider):
            _deny()
        try:
            source = provider()
            validate_snapshot = RESOURCE_SCHEMAS[selected["resource_id"]]["validate_snapshot"]
            generation = validate_snapshot(source, now or datetime.now(timezone.utc))
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
                    or state["generation"] != generation or state["principal"] != principal
                    or type(state["offset"]) is not int or state["offset"] <= 0
                    or state["limit"] != selected["limit"] or state["offset"] >= len(source["items"])):
                    _deny()
                offset = state["offset"]
            end = min(offset + selected["limit"], len(source["items"]))
            next_cursor = None
            if end < len(source["items"]):
                state = {"resource_id": selected["resource_id"], "view": selected["view"], "generation": generation,
                         "offset": end, "principal": principal, "limit": selected["limit"]}
                raw = _canonical(state)
                next_cursor = _encode(raw + hmac.new(self.key, raw, hashlib.sha256).digest())
            result = {"resource_id": selected["resource_id"], "view": selected["view"], "collected_at": source["collected_at"],
                      "generation": generation, "items": source["items"][offset:end], "next_cursor": next_cursor,
                      "effect_performed": False}
            if len(_canonical(result)) > 4096:
                _deny()
            return result
        except Exception:
            _deny()
