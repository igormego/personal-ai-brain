"""Pure, non-executing movie selector for a future Assistant media capability.

This module neither performs identity resolution nor authorizes a Seerr request.
Authentication, side-effect approval, durable at-most-once execution and access
to the existing Homelab Movie Intelligence wrapper belong to trusted adapters.
In particular, an external caller cannot supply an execute flag, endpoint,
profile, server, HTTP method, token, filesystem path or command.
"""

from __future__ import annotations

import unicodedata


MAX_TITLE_BYTES = 512
MAX_TITLE_CHARS = 200
MAX_TMDB_ID = 2**53 - 1  # Preserve exact JSON number identity across JS/Python.
MIN_RELEASE_YEAR = 1888
MAX_RELEASE_YEAR = 2100


class MovieSelectorRejected(ValueError):
    """Always contains a fixed public rejection code, not user input."""


def validate_movie_selector(value: object) -> dict[str, int | str]:
    """Validate exactly one movie selector for read-only planning.

    Accepted forms: {"tmdb_id": positive integer}, or
    {"title": nonempty Unicode text, "year": integer}. The caller must route
    execution to a separate authenticated/authorized capability; these fields
    alone grant no write permission. No fuzzy matching is performed here.
    """
    if type(value) is not dict:
        raise MovieSelectorRejected("invalid_movie_selector")

    if set(value) == {"tmdb_id"}:
        tmdb_id = value["tmdb_id"]
        if type(tmdb_id) is not int or not 1 <= tmdb_id <= MAX_TMDB_ID:
            raise MovieSelectorRejected("invalid_movie_selector")
        return {"tmdb_id": tmdb_id}

    if set(value) == {"title", "year"}:
        title, year = value["title"], value["year"]
        if not isinstance(title, str) or not title or title != title.strip():
            raise MovieSelectorRejected("invalid_movie_selector")
        if len(title) > MAX_TITLE_CHARS or any(unicodedata.category(c).startswith("C") for c in title):
            raise MovieSelectorRejected("invalid_movie_selector")
        try:
            encoded = title.encode("utf-8")
        except UnicodeEncodeError:
            raise MovieSelectorRejected("invalid_movie_selector") from None
        if len(encoded) > MAX_TITLE_BYTES:
            raise MovieSelectorRejected("invalid_movie_selector")
        if type(year) is not int or not MIN_RELEASE_YEAR <= year <= MAX_RELEASE_YEAR:
            raise MovieSelectorRejected("invalid_movie_selector")
        return {"title": title, "year": year}

    raise MovieSelectorRejected("invalid_movie_selector")
