"""Tests for /api/v1/notifications/telegram: session-owned Telegram settings.

Uses two authenticated clients backed by two real registered users sharing
one database, so isolation assertions prove a write/read actually stayed
scoped to `session.user_id` rather than merely matching a response shape.
"""

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository
from surebet.web.app import create_app

FAKE_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("SUREBET_TELEGRAM_BOT_TOKEN", "super-secret")
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


@pytest.fixture
def client_without_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.delenv("SUREBET_TELEGRAM_BOT_TOKEN", raising=False)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


@pytest.fixture
def client_with_fake_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, str, NotificationRepository, int]:
    monkeypatch.setenv("SUREBET_TELEGRAM_BOT_TOKEN", "super-secret")
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    user, session = auth.register_user("alice", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    repo: NotificationRepository = app.state.notification_repo
    from surebet.storage.notifications import TelegramSettingsUpdate

    repo.update_settings(user.user_id, TelegramSettingsUpdate(chat_id=555))
    return c, session.csrf_token, repo, user.user_id


@pytest.fixture
def two_user_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, str, int, TestClient, str, int, Path]:
    monkeypatch.setenv("SUREBET_TELEGRAM_BOT_TOKEN", "super-secret")
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    user_a, session_a = auth.register_user("alice", FAKE_PASSWORD)
    user_b, session_b = auth.register_user("bob", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    client_a = TestClient(app)
    client_a.cookies.set("session_token", session_a.token)
    client_b = TestClient(app)
    client_b.cookies.set("session_token", session_b.token)

    return (
        client_a,
        session_a.csrf_token,
        user_a.user_id,
        client_b,
        session_b.csrf_token,
        user_b.user_id,
        db_path,
    )


def test_get_config_requires_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    c = TestClient(app)

    assert c.get("/api/v1/notifications/telegram").status_code == 401


def test_get_config_is_token_free(client: tuple[TestClient, str]) -> None:
    c, _csrf = client
    response = c.get("/api/v1/notifications/telegram")
    assert response.status_code == 200
    body = response.json()
    assert body["token_configured"] is True
    assert body["increase_threshold_percentage_points"] == "0.20"
    assert body["notify_1x2"] is True
    assert body["notify_ou25"] is True
    assert "super-secret" not in response.text
    assert "token" not in {key for key in body if key != "token_configured"}


def test_put_requires_csrf_and_converts_exact_percentages(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    no_csrf = c.put("/api/v1/notifications/telegram", json={"enabled": False})
    assert no_csrf.status_code == 403

    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={
            "chat_id": "123",
            "minimum_profit_percent": "0.95",
            "decrease_threshold_percentage_points": "0.20",
            "increase_threshold_percentage_points": "0.30",
            "alert_cooldown_seconds": 300,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["minimum_profit_percent"] == "0.95"
    assert body["decrease_threshold_percentage_points"] == "0.20"
    assert body["increase_threshold_percentage_points"] == "0.30"
    assert body["chat_id"] == "123"


def test_config_response_exposes_the_alert_cooldown(client: tuple[TestClient, str]) -> None:
    c, _csrf = client
    body = c.get("/api/v1/notifications/telegram").json()
    assert "alert_cooldown_seconds" in body


def test_updating_the_alert_cooldown_persists(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"alert_cooldown_seconds": 600},
    )
    assert response.status_code == 200
    assert response.json()["alert_cooldown_seconds"] == 600


def test_updating_market_notifications_persists(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"notify_1x2": False, "notify_ou25": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["notify_1x2"] is False
    assert data["notify_ou25"] is True

    get_resp = c.get("/api/v1/notifications/telegram")
    assert get_resp.status_code == 200
    assert get_resp.json()["notify_1x2"] is False
    assert get_resp.json()["notify_ou25"] is True


def test_percentage_with_more_than_two_decimals_is_rejected(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"minimum_profit_percent": "0.951"},
    )
    assert response.status_code == 422


def test_chat_id_must_be_a_positive_decimal_string(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"chat_id": "-1"},
    )
    assert response.status_code == 422


def test_enabling_requires_token_and_validated_current_chat(
    client_without_token: tuple[TestClient, str],
) -> None:
    c, csrf = client_without_token
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True},
    )
    assert response.status_code == 409


def test_failed_enable_request_does_not_partially_update_other_fields(
    client: tuple[TestClient, str],
) -> None:
    c, csrf = client
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True, "chat_id": "999"},
    )
    assert response.status_code == 409

    current = c.get("/api/v1/notifications/telegram").json()
    assert current["enabled"] is False
    assert current["chat_id"] is None


def test_changing_chat_id_invalidates_validation(client: tuple[TestClient, str]) -> None:
    c, csrf = client
    c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"chat_id": "123"},
    )
    response = c.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf},
        json={"chat_id": "456"},
    )
    assert response.status_code == 200
    assert response.json()["chat_validated"] is False


def test_test_endpoint_requires_token_chat_and_csrf(
    client_without_token: tuple[TestClient, str],
) -> None:
    c, csrf = client_without_token
    response = c.post(
        "/api/v1/notifications/telegram/test",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Telegram bot tokeni yapılandırılmamış"


@respx.mock
def test_successful_test_validates_current_chat(
    client_with_fake_telegram: tuple[TestClient, str, NotificationRepository, int],
) -> None:
    c, csrf, repo, user_id = client_with_fake_telegram
    respx.post("https://api.telegram.org/botsuper-secret/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    response = c.post("/api/v1/notifications/telegram/test", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    settings = repo.get_settings(user_id)
    assert settings.validated_chat_id == settings.chat_id


@respx.mock
def test_failed_test_returns_sanitized_error_and_records_it(
    client_with_fake_telegram: tuple[TestClient, str, NotificationRepository, int],
) -> None:
    c, csrf, repo, user_id = client_with_fake_telegram
    respx.post("https://api.telegram.org/botsuper-secret/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "chat not found"})
    )

    response = c.post("/api/v1/notifications/telegram/test", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 502
    assert "super-secret" not in response.text
    settings = repo.get_settings(user_id)
    assert settings.validated_chat_id is None
    assert settings.last_error is not None


# ---------------------------------------------------------------------------
# Two-user isolation: every read/write is scoped to `session.user_id`.
# ---------------------------------------------------------------------------


def test_get_config_defaults_are_independent_per_user(
    two_user_clients: tuple[TestClient, str, int, TestClient, str, int, Path],
) -> None:
    client_a, _csrf_a, _user_a, client_b, _csrf_b, _user_b, _db_path = two_user_clients

    body_a = client_a.get("/api/v1/notifications/telegram").json()
    body_b = client_b.get("/api/v1/notifications/telegram").json()
    assert body_a["enabled"] is False
    assert body_b["enabled"] is False
    assert body_a["chat_id"] is None
    assert body_b["chat_id"] is None


def test_put_persists_and_scopes_to_session_user_only(
    two_user_clients: tuple[TestClient, str, int, TestClient, str, int, Path],
) -> None:
    client_a, csrf_a, _user_a, client_b, _csrf_b, _user_b, _db_path = two_user_clients

    response = client_a.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf_a},
        json={"chat_id": "123", "minimum_profit_percent": "1.50"},
    )
    assert response.status_code == 200
    assert response.json()["chat_id"] == "123"

    # User A's write must not leak to user B's own row.
    body_b = client_b.get("/api/v1/notifications/telegram").json()
    assert body_b["chat_id"] is None
    assert body_b["minimum_profit_percent"] != "1.50"

    # Rereading as user A must reflect the write.
    body_a = client_a.get("/api/v1/notifications/telegram").json()
    assert body_a["chat_id"] == "123"


def test_put_ignores_a_client_supplied_user_id(
    two_user_clients: tuple[TestClient, str, int, TestClient, str, int, Path],
) -> None:
    client_a, csrf_a, _user_a, client_b, _csrf_b, user_b, _db_path = two_user_clients

    response = client_a.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": csrf_a},
        json={"chat_id": "123", "user_id": user_b},
    )
    assert response.status_code == 200
    assert "user_id" not in response.json()

    # The supplied `user_id` must never redirect the write to user B.
    body_b = client_b.get("/api/v1/notifications/telegram").json()
    assert body_b["chat_id"] is None


@respx.mock
def test_test_endpoint_validates_only_the_calling_users_chat(
    two_user_clients: tuple[TestClient, str, int, TestClient, str, int, Path],
) -> None:
    client_a, csrf_a, user_a, _client_b, _csrf_b, user_b, db_path = two_user_clients
    repo = NotificationRepository(db_path)
    from surebet.storage.notifications import TelegramSettingsUpdate

    # Both users configure the SAME chat_id -- isolation must still hold.
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=777))
    repo.update_settings(user_b, TelegramSettingsUpdate(chat_id=777))

    respx.post("https://api.telegram.org/botsuper-secret/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    response = client_a.post(
        "/api/v1/notifications/telegram/test", headers={"X-CSRF-Token": csrf_a}
    )
    assert response.status_code == 200

    settings_a = repo.get_settings(user_a)
    settings_b = repo.get_settings(user_b)
    assert settings_a.validated_chat_id == 777
    # User B never called the test endpoint -- their own row must be untouched.
    assert settings_b.validated_chat_id is None
    assert settings_b.last_successful_delivery_at is None
