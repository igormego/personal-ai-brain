"""Bounded GET-only Radarr/Sonarr/Seerr resource adapter.

Only a trusted operator may construct this adapter with a fixed service origin and
an existing host-managed credential. Callers choose a registered view, never an
endpoint, host, URL, method or response field. No source or credential is persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .arr_request_status import (MAX_DIAGNOSTIC_REQUESTS, MAX_REQUEST_ITEMS, REQUEST_STATUS_VIEWS, project_request_status,
                                 project_seerr_health, valid_title)
from .sonarr_diagnostics import MAX_QUEUE_ITEMS, SONARR_DIAGNOSTIC_VIEWS, project_sonarr_diagnostics

MAX_BODY = 4_194_304
MAX_ITEMS = 4096
MAX_TITLE_LOOKUPS = 24
SEERR_REQUESTS = f"/api/v1/request?take={MAX_REQUEST_ITEMS}&skip=0&sort=added"
ENDPOINTS = {
    "radarr": {"items": "/api/v3/movie", "profiles": "/api/v3/qualityprofile", "movie": "/api/v3/movie",
               "health": "/api/v3/health",
               "queue": f"/api/v3/queue?page=1&pageSize={MAX_QUEUE_ITEMS}&includeUnknownMovieItems=true"},
    "sonarr": {"items": "/api/v3/series", "profiles": "/api/v3/qualityprofile",
               "series": "/api/v3/series", "health": "/api/v3/health",
               "queue": f"/api/v3/queue?page=1&pageSize={MAX_QUEUE_ITEMS}&includeUnknownSeriesItems=true"},
    "seerr": {view: SEERR_REQUESTS for view in ("request", "requests", "health")},
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
    if not valid_title(value):
        _deny()
    return value


class ARRReadProvider:
    """One operator-pinned ARR instance with fixed GET views.

    Supply one instance per service. The origin and key are trusted deployment
    configuration, not request arguments; no default credentials are invented.
    Transport never follows redirects and does not retry failed requests.
    """

    def __init__(self, service: str, origin: str, api_key: str, *, opener=None, media_diagnostics=False):
        if type(service) is not str or service not in ENDPOINTS:
            _deny()
        if type(origin) is not str or type(api_key) is not str or type(media_diagnostics) is not bool:
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
        self.media_diagnostics = media_diagnostics
        self.origin = origin.rstrip("/")
        self._api_key = api_key
        self._open = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect()).open
        if not callable(self._open):
            _deny()

    def _get(self, path, *, timeout=8):
        url = self.origin + path
        request = Request(url, headers={"X-Api-Key": self._api_key, "Accept": "application/json"}, method="GET")
        response = self._open(request, timeout=timeout)
        with response:
            if response.geturl() != url or response.status != 200:
                _deny()
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            _deny()
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))

    def _seerr_titles(self, rows):
        titles = {}
        deadline = monotonic() + 8
        for row in reversed(rows):
            key = (row["media_type"], row["tmdb_id"])
            if key in titles:
                continue
            remaining = deadline - monotonic()
            if len(titles) >= MAX_TITLE_LOOKUPS or remaining <= 0:
                break
            kind, tmdb_id = key
            # Both components have already passed the request-row schema.
            titles[key] = None
            try:
                details = self._get(f"/api/v1/{kind}/{tmdb_id}", timeout=min(remaining, 2))
            except (OSError, ValueError, TypeError):
                continue
            if type(details) is dict and details.get("id") == tmdb_id:
                title = details.get("title" if kind == "movie" else "name")
                if valid_title(title):
                    titles[key] = title
        for row in rows:
            row["title"] = titles.get((row["media_type"], row["tmdb_id"]))

    def _seerr_requests(self, first):
        """Follow Seerr's fixed server-side pages before publishing a complete view."""
        page_info = first.get("pageInfo") if type(first) is dict else None
        total = page_info.get("results") if type(page_info) is dict else None
        if type(total) is not int or not 0 <= total <= MAX_DIAGNOSTIC_REQUESTS:
            _deny()
        rows = []
        deadline = monotonic() + 16
        for skip in range(0, max(total, 1), MAX_REQUEST_ITEMS):
            remaining = deadline - monotonic()
            if remaining <= 0:
                _deny()
            data = first if skip == 0 else self._get(
                f"/api/v1/request?take={MAX_REQUEST_ITEMS}&skip={skip}&sort=added", timeout=min(remaining, 8))
            page = data.get("pageInfo") if type(data) is dict else None
            batch = data.get("results") if type(data) is dict else None
            if (type(page) is not dict or page.get("results") != total or type(batch) is not list
                    or len(batch) != min(MAX_REQUEST_ITEMS, total - skip)):
                _deny()
            rows.extend(batch)
        return {**first, "results": rows}

    def __call__(self, view: str) -> dict:
        if type(view) is not str or view not in ENDPOINTS[self.service]:
            _deny()
        if view == "health" and self.service != "sonarr" and not self.media_diagnostics:
            _deny()
        try:
            records = self._get(ENDPOINTS[self.service][view])
            resource = "arr/" + self.service
            if view in REQUEST_STATUS_VIEWS.get(resource, ()):
                if self.service == "seerr" and self.media_diagnostics:
                    records = self._seerr_requests(records)
                items = project_request_status(resource, view, records, diagnostics=self.media_diagnostics)
                if self.service == "seerr" and self.media_diagnostics:
                    self._seerr_titles(items)
                return self._snapshot(view, items)
            if view == "health" and self.service == "seerr":
                return self._snapshot(view, project_seerr_health(records))
            if view == "health" or (self.service == "sonarr" and view in SONARR_DIAGNOSTIC_VIEWS):
                return self._snapshot(view, project_sonarr_diagnostics(view, records))
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
            return self._snapshot(view, items)
        except Exception:
            _deny()

    def _snapshot(self, view, items):
        return {
            "schema_version": "0.1", "resource_id": "arr/" + self.service,
            "view": view, "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "complete": True, "source_status": {self.service: "ok"}, "items": items,
        }
