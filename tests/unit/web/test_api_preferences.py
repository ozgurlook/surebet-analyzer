"""Tests for /api/v1/preferences/filters/{scope}: saved taxonomy filters.

Uses two authenticated clients backed by two real registered users sharing
one database, so isolation assertions prove a write actually stayed scoped
to its owner and scope rather than merely matching a response shape.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import get_connection, init_db
from surebet.taxonomy.models import CompetitionKind
from surebet.taxonomy.normalizer import normalize_taxonomy_text
from surebet.web.app import create_app

FAKE_PASSWORD = "correct-horse-battery-staple"
NOW_ISO = "2026-08-28T12:00:00+00:00"


def _insert_league(db_path: Path, region_id: str, display_name: str) -> str:
    league_id = f"league:{uuid4()}"
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO canonical_leagues "
            "(league_id, region_id, display_name, normalized_key, kind, gender, age_group, "
            "is_reserve, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 1, ?, ?);",
            (
                league_id,
                region_id,
                display_name,
                normalize_taxonomy_text(display_name),
                CompetitionKind.UNKNOWN.value,
                NOW_ISO,
                NOW_ISO,
            ),
        )
    return league_id


@pytest.fixture
def two_user_clients(tmp_path: Path) -> tuple[TestClient, str, TestClient, str, Path]:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _, session_a = auth.register_user("alice", FAKE_PASSWORD)
    _, session_b = auth.register_user("bob", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    client_a = TestClient(app)
    client_a.cookies.set("session_token", session_a.token)
    client_b = TestClient(app)
    client_b.cookies.set("session_token", session_b.token)

    return client_a, session_a.csrf_token, client_b, session_b.csrf_token, db_path


def test_get_filters_requires_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    assert client.get("/api/v1/preferences/filters/events").status_code == 401


def test_get_filters_for_unknown_scope_is_rejected(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.get("/api/v1/preferences/filters/bogus-scope")
    assert resp.status_code == 422


def test_get_filters_defaults_to_empty(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.get("/api/v1/preferences/filters/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["country_ids"] == []
    assert body["league_ids"] == []


def test_put_filters_requires_csrf(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    assert resp.status_code == 403


def test_put_filters_for_unknown_scope_is_rejected(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/bogus-scope",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": [], "league_ids": []},
    )
    assert resp.status_code == 422


def test_put_filters_persists_and_scopes_to_session_user_only(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR", "country:DE"], "league_ids": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["country_ids"]) == {"country:TR", "country:DE"}

    recheck_a = client_a.get("/api/v1/preferences/filters/events").json()
    assert set(recheck_a["country_ids"]) == {"country:TR", "country:DE"}

    # User B's own filters for the same scope must remain untouched.
    recheck_b = client_b.get("/api/v1/preferences/filters/events").json()
    assert recheck_b["country_ids"] == []


def test_put_filters_ignores_a_client_supplied_user_id(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": [], "user_id": 999999},
    )
    assert resp.status_code == 200
    assert "user_id" not in resp.json()
    assert client_a.get("/api/v1/preferences/filters/events").json()["country_ids"] == [
        "country:TR"
    ]
    assert client_b.get("/api/v1/preferences/filters/events").json()["country_ids"] == []


def test_put_filters_scopes_are_independent(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    client_a.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:DE"], "league_ids": []},
    )

    events = client_a.get("/api/v1/preferences/filters/events").json()
    opportunities = client_a.get("/api/v1/preferences/filters/opportunities").json()
    assert events["country_ids"] == ["country:TR"]
    assert opportunities["country_ids"] == ["country:DE"]


def test_put_filters_duplicate_ids_are_normalized(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR", "country:TR"], "league_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["country_ids"] == ["country:TR"]


def test_put_filters_empty_body_clears_previous_selection(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": [], "league_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["country_ids"] == []
    assert client_a.get("/api/v1/preferences/filters/events").json()["country_ids"] == []


def test_put_filters_rejects_unknown_country_id_and_does_not_change_stored_state(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": ["country:not-real"], "league_ids": []},
    )
    assert resp.status_code == 422

    unchanged = client_a.get("/api/v1/preferences/filters/events").json()
    assert unchanged["country_ids"] == ["country:TR"]


def test_put_filters_rejects_unknown_league_id(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, _db_path = two_user_clients

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": [], "league_ids": ["league:not-real"]},
    )
    assert resp.status_code == 422


def test_put_filters_accepts_a_valid_league_id(
    two_user_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b, db_path = two_user_clients
    league_id = _insert_league(db_path, "country:TR", "Süper Lig")

    resp = client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf_a},
        json={"country_ids": [], "league_ids": [league_id]},
    )
    assert resp.status_code == 200
    assert resp.json()["league_ids"] == [league_id]
