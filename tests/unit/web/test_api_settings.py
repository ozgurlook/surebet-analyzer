"""Tests for /api/v1/settings: the personal/system split and cross-user isolation.

Uses two authenticated clients backed by two real registered users sharing
one database, so each assertion can prove a write actually stayed scoped to
its owner (or, for system settings, was genuinely shared) rather than merely
matching a response shape.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.web.app import create_app

FAKE_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def two_user_clients(tmp_path: Path) -> tuple[TestClient, str, TestClient, str]:
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

    return client_a, session_a.csrf_token, client_b, session_b.csrf_token


def test_get_settings_requires_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    assert client.get("/api/v1/settings").status_code == 401


def test_get_settings_returns_personal_and_system_sections(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"personal", "system"}

    personal = body["personal"]
    assert personal["default_budget_cents"] == 100_000
    assert personal["include_source_a_match_sides"] is False
    assert personal["tracked_competitions_only"] is True

    system = body["system"]
    assert system["refresh_interval_seconds"] == 30
    assert system["staleness_limit_seconds"] == 120


def test_put_personal_requires_csrf(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/personal",
        json={"default_budget_cents": 500_000},
    )
    assert resp.status_code == 403


def test_put_system_requires_csrf(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, _csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/system",
        json={"refresh_interval_seconds": 60},
    )
    assert resp.status_code == 403


def test_put_personal_scopes_to_session_user_only(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": csrf_a},
        json={
            "default_budget_cents": 500_000,
            "include_source_a_match_sides": True,
            "tracked_competitions_only": False,
        },
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["default_budget_cents"] == 500_000
    assert updated["include_source_a_match_sides"] is True
    assert updated["tracked_competitions_only"] is False

    # User A sees the change reflected on a subsequent GET.
    recheck_a = client_a.get("/api/v1/settings").json()["personal"]
    assert recheck_a["default_budget_cents"] == 500_000
    assert recheck_a["include_source_a_match_sides"] is True
    assert recheck_a["tracked_competitions_only"] is False

    # User B's own personal preferences must remain untouched -- proves the
    # write was scoped by session.user_id, not something globally shared.
    recheck_b = client_b.get("/api/v1/settings").json()["personal"]
    assert recheck_b["default_budget_cents"] == 100_000
    assert recheck_b["include_source_a_match_sides"] is False
    assert recheck_b["tracked_competitions_only"] is True


def test_put_personal_ignores_a_client_supplied_user_id(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": csrf_a},
        json={"default_budget_cents": 750_000, "user_id": 999999},
    )
    assert resp.status_code == 200
    assert "user_id" not in resp.json()

    # The write landed on user A (the session owner), never on the
    # fabricated user_id, and never leaked to user B either.
    assert client_a.get("/api/v1/settings").json()["personal"]["default_budget_cents"] == 750_000
    assert client_b.get("/api/v1/settings").json()["personal"]["default_budget_cents"] == 100_000


def test_put_system_updates_shared_singleton_visible_to_other_user(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/system",
        headers={"X-CSRF-Token": csrf_a},
        json={"refresh_interval_seconds": 60, "staleness_limit_seconds": 120},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["refresh_interval_seconds"] == 60
    assert updated["staleness_limit_seconds"] == 120

    # User B -- a completely different session/user -- observes the exact
    # same shared value. System settings carry no per-user scoping at all.
    system_for_b = client_b.get("/api/v1/settings").json()["system"]
    assert system_for_b["refresh_interval_seconds"] == 60
    assert system_for_b["staleness_limit_seconds"] == 120


def test_put_system_ignores_a_client_supplied_user_id(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/system",
        headers={"X-CSRF-Token": csrf_a},
        json={"staleness_limit_seconds": 150, "user_id": 12345},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "user_id" not in body
    assert body["staleness_limit_seconds"] == 150


def test_put_system_does_not_change_any_users_personal_preferences(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, client_b, _csrf_b = two_user_clients

    client_a.put(
        "/api/v1/settings/system",
        headers={"X-CSRF-Token": csrf_a},
        json={"refresh_interval_seconds": 45},
    )

    personal_a = client_a.get("/api/v1/settings").json()["personal"]
    personal_b = client_b.get("/api/v1/settings").json()["personal"]
    assert personal_a["default_budget_cents"] == 100_000
    assert personal_b["default_budget_cents"] == 100_000


def test_put_personal_rejects_budget_above_upper_bound(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": csrf_a},
        json={"default_budget_cents": 1_000_000_001},
    )
    assert resp.status_code == 422
    # Rejected validation must not have mutated the stored row.
    assert client_a.get("/api/v1/settings").json()["personal"]["default_budget_cents"] == 100_000


def test_put_system_rejects_refresh_interval_out_of_range(
    two_user_clients: tuple[TestClient, str, TestClient, str],
) -> None:
    client_a, csrf_a, _client_b, _csrf_b = two_user_clients

    resp = client_a.put(
        "/api/v1/settings/system",
        headers={"X-CSRF-Token": csrf_a},
        json={"refresh_interval_seconds": 1000},
    )
    assert resp.status_code == 422
    assert client_a.get("/api/v1/settings").json()["system"]["refresh_interval_seconds"] == 30
