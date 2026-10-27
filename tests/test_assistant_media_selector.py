"""Offline selector tests: no Seerr credentials, network or movie requests."""

import pytest

from personal_ai_brain.assistant_media_selector import (
    MAX_TITLE_BYTES,
    MAX_TMDB_ID,
    MovieSelectorRejected,
    validate_movie_selector,
)


def test_tmdb_selector_is_exact_and_does_not_mutate_caller():
    original = {"tmdb_id": 244786}
    selected = validate_movie_selector(original)
    assert selected == {"tmdb_id": 244786}
    assert selected is not original
    assert original == {"tmdb_id": 244786}


def test_title_year_selector_preserves_unicode_and_movie_identity():
    original = {"year": 2024, "title": "Čarodejník: L'été d'Ágnes"}
    assert validate_movie_selector(original) == {
        "title": "Čarodejník: L'été d'Ágnes", "year": 2024,
    }
    assert validate_movie_selector({"title": "Whiplash", "year": 2014}) == {
        "title": "Whiplash", "year": 2014,
    }


@pytest.mark.parametrize("value", [
    None, [], (), "Whiplash", 244786, True,
    {}, {"tmdb_id": 0}, {"tmdb_id": -1},
    {"tmdb_id": MAX_TMDB_ID + 1}, {"tmdb_id": 4.0},
    {"tmdb_id": "244786"}, {"tmdb_id": True},
    {"tmdb_id": None}, {"tmdb_id": 1, "year": 2014},
    {"tmdb_id": 244786, "execute": True},
    {"tmdb_id": 244786, "server": "homelab"},
    {"tmdb_id": 244786, "url": "https://example.com"},
    {"tmdb_id": 244786, "quality_profile": 9},
    {"title": "Whiplash"}, {"year": 2014},
    {"title": "", "year": 2014},
    {"title": " Whiplash", "year": 2014},
    {"title": "Whiplash ", "year": 2014},
    {"title": "Whiplash\nexec", "year": 2014},
    {"title": "Whiplash\x00", "year": 2014},
    {"title": "Whiplash\u202e", "year": 2014},
    {"title": "Whiplash", "year": 2014.0},
    {"title": "Whiplash", "year": "2014"},
    {"title": "Whiplash", "year": True},
    {"title": "Whiplash", "year": 1800},
    {"title": "Whiplash", "year": 2101},
    {"title": "Whiplash", "year": 2014, "execute": True},
    {"title": "Whiplash", "year": 2014, "media_type": "tv"},
    {"title": "Whiplash", "year": 2014, "command": "id"},
    {"title": "Whiplash", "year": 2014, "radarr_root": "/movies"},
    {"title": "Whiplash", "year": 2014, "api_key": "NEVER_ECHO_THIS"},
    {"title": "X" * 201, "year": 2014},
    {"title": "\u00e9" * (MAX_TITLE_BYTES // 2 + 1), "year": 2014},
    {"title": "\ud800", "year": 2014},
])
def test_malformed_and_side_effect_fields_fail_closed(value):
    with pytest.raises(MovieSelectorRejected) as raised:
        validate_movie_selector(value)
    assert str(raised.value) == "invalid_movie_selector"


@pytest.mark.parametrize("value", [
    {"tmdb_id": 1},
    {"tmdb_id": MAX_TMDB_ID},
    {"title": "A", "year": 1888},
    {"title": "A", "year": 2100},
    {"title": "À" * 200, "year": 2026},
])
def test_valid_boundaries(value):
    assert validate_movie_selector(value) == value
