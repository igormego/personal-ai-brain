"""Bounded GET-only Radarr/Sonarr resource adapter; not a production grant.

Only a trusted operator may construct this adapter with a fixed service origin and
an existing host-managed credential. Callers choose a registered view, never an
endpoint, host, URL, method or response field. No source or credential is persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BODY = 4_194_304
MAX_ITEMS = 4096
ENDPOINTS = {
    "radarr": {"items": "/api/v3/movie", "profiles": "/api/v3/qualityprofile"},
    "sonarr": {"items": "/api/v3/series", "profiles": "/api/v3/qualityprofile"},
}


class ARRSourceUnavailable(ValueError):
    """Fixed outward error; never contains an API response or credential."""


def _deny():
    raise ARRSourceUnavailable("arr_source_unavailable")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _positive(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        _deny()
    return value


def _title(value):
    if (type(value) is not str or not 1 <= len(value) <= 200
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _deny()
    return value


class ARRReadProvider:
    """One operator-pinned ARR instance, two allowlisted GET views.

    Supply one instance per service. The origin and key are trusted deployment
    configuration, not request arguments; no default credentials are invented.
    Transport never follows redirects and does not retry failed requests.
    """

    def __init__(self, service: str, origin: str, api_key: str, *, opener=None):
        if type(service) is not str or service not in ENDPOINTS:
            _deny()
        if type(origin) is not str or type(api_key) is not str:
            _deny()
        if (not api_key or len(api_key) > 1024
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in api_key)):
            _deny()
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                     and parsed.port is not None and parsed.port > 0
                     and parsed.username is None and parsed.password is None
                     and parsed.path in ("", "/") and not parsed.query
                     and not parsed.fragment and not parsed.netloc.endswith("."))
        except ValueError:
            valid = False
        if not valid or any(c in origin for c in ("\\", "\r", "\n", "@")):
            _deny()
        self.service = service
        self.origin = origin.rstrip("/")
        self._api_key = api_key
        self._open = opener if opener is not None else build_opener(_NoRedirect()).open
        if not callable(self._open):
            _deny()

    def __call__(self, view: str) -> dict:
        if type(view) is not str or view not in ENDPOINTS[self.service]:
            _deny()
        url = self.origin + ENDPOINTS[self.service][view]
        request = Request(url, headers={"X-Api-Key": self._api_key, "Accept": "application/json"}, method="GET")
        try:
            response = self._open(request, timeout=8)
            with response:
                if response.geturl() != url or response.status != 200:
                    _deny()
                raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                _deny()
            records = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(records) is not list or len(records) > MAX_ITEMS:
                _deny()
            items = []
            seen = set()
            for row in records:
                if type(row) is not dict:
                    _deny()
                rid = _positive(row.get("id"))
                if rid in seen:
                    _deny()
                seen.add(rid)
                if view == "profiles":
                    item = {"id": rid, "name": _title(row.get("name"))}
                else:
                    year = row.get("year")
                    if type(year) is not int or not 1880 <= year <= 2200:
                        _deny()
                    if type(row.get("monitored")) is not bool:
                        _deny()
                    external = row.get("tmdbId" if self.service == "radarr" else "tvdbId")
                    if external is not None:
                        _positive(external)
                    has_file = row.get("hasFile") if self.service == "radarr" else None
                    if self.service == "radarr" and type(has_file) is not bool:
                        _deny()
                    item = {
                        "id": rid, "title": _title(row.get("title")),
                        "year": year, "monitored": row["monitored"],
                        "quality_profile_id": _positive(row.get("qualityProfileId")),
                        "external_id": external, "has_file": has_file,
                    }
                items.append(item)
            items.sort(key=lambda r: r["id"])
            return {
                "schema_version": "0.1", "resource_id": "arr/" + self.service,
                "view": view, "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "complete": True, "source_status": {self.service: "ok"}, "items": items,
            }
        except Exception:
            _deny()
