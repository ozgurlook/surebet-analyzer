import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.web.app import create_app

FAKE_REGISTRATION_CODE = "test-registration-code"
FAKE_PASSWORD = "correct-horse-battery-staple"


def _make_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    registration_code: str | None = FAKE_REGISTRATION_CODE,
    cookie_secure: bool | None = None,
) -> TestClient:
    if registration_code is None:
        monkeypatch.delenv("SUREBET_REGISTRATION_CODE", raising=False)
    else:
        monkeypatch.setenv("SUREBET_REGISTRATION_CODE", registration_code)
    if cookie_secure is None:
        monkeypatch.delenv("SUREBET_COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("SUREBET_COOKIE_SECURE", "true" if cookie_secure else "false")

    db_path = tmp_path / "test.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    return TestClient(app)


def _register(
    client: TestClient,
    username: str = "alice",
    password: str = FAKE_PASSWORD,
    code: str = FAKE_REGISTRATION_CODE,
) -> httpx.Response:
    return client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "registration_code": code},
    )


def _set_cookie_header(response: httpx.Response) -> str:
    header = response.headers.get("set-cookie", "")
    assert header, "expected a Set-Cookie header"
    return header


# --- empty database / /auth/me shape ----------------------------------------


def test_empty_database_is_unauthenticated_and_never_open_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert "open_access" not in resp.text
    assert "has_password" not in body


def test_auth_me_response_shape_unauthenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    body = client.get("/api/v1/auth/me").json()
    assert set(body.keys()) == {
        "authenticated",
        "registration_enabled",
        "user_id",
        "username",
        "csrf_token",
    }
    assert body["authenticated"] is False
    assert body["registration_enabled"] is True
    assert body["user_id"] is None
    assert body["username"] is None
    assert body["csrf_token"] is None


def test_auth_me_response_shape_authenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    reg = _register(client, username="bob")
    assert reg.status_code == 200

    me = client.get("/api/v1/auth/me").json()
    assert me["authenticated"] is True
    assert me["registration_enabled"] is True
    assert me["user_id"] == reg.json()["user_id"]
    assert me["username"] == "bob"
    assert isinstance(me["csrf_token"], str)
    assert len(me["csrf_token"]) > 10


# --- registration disabled without env var ----------------------------------


def test_registration_closed_without_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch, registration_code=None)

    me = client.get("/api/v1/auth/me").json()
    assert me["registration_enabled"] is False

    resp = _register(client, code=FAKE_REGISTRATION_CODE)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Yeni kullanıcı kaydı kapalı"
    assert "session_token" not in client.cookies


def test_existing_users_can_still_log_in_when_registration_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    AuthRepository(db_path).register_user("carol", FAKE_PASSWORD)

    monkeypatch.delenv("SUREBET_REGISTRATION_CODE", raising=False)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    resp = client.post("/api/v1/auth/login", json={"username": "carol", "password": FAKE_PASSWORD})
    assert resp.status_code == 200


# --- successful registration -------------------------------------------------


def test_valid_registration_logs_in_immediately_and_sets_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = _register(client, username="dave")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "dave"
    assert isinstance(body["user_id"], int)
    assert isinstance(body["csrf_token"], str)

    cookie_header = _set_cookie_header(resp)
    assert "session_token=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "samesite=lax" in cookie_header.lower()
    assert "Path=/" in cookie_header
    assert "Max-Age=604800" in cookie_header
    assert "secure" not in cookie_header.lower()

    me = client.get("/api/v1/auth/me").json()
    assert me["authenticated"] is True
    assert me["username"] == "dave"


def test_cookie_secure_flag_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secure_dir = tmp_path / "secure"
    secure_dir.mkdir()
    secure_client = _make_client(secure_dir, monkeypatch, cookie_secure=True)
    resp = _register(secure_client, username="erin")
    cookie_header = _set_cookie_header(resp)
    assert "secure" in cookie_header.lower()


def test_cookie_not_secure_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch, cookie_secure=None)
    resp = _register(client, username="frank")
    cookie_header = _set_cookie_header(resp)
    assert "secure" not in cookie_header.lower()


# --- invalid registration input / code --------------------------------------


def test_invalid_registration_code_generic_message_no_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = _register(client, username="gina", code="totally-wrong-code")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Kayıt bilgileri geçersiz"
    assert "totally-wrong-code" not in resp.text
    assert FAKE_REGISTRATION_CODE not in resp.text
    assert "session_token" not in client.cookies


def test_invalid_username_generic_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = _register(client, username="ab")  # too short
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Kayıt bilgileri geçersiz"


def test_invalid_password_generic_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = _register(client, username="helen", password="short")  # too short
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Kayıt bilgileri geçersiz"
    assert "short" not in resp.text


# --- duplicate username -------------------------------------------------


def test_duplicate_username_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    first = _register(client, username="Ivy")
    assert first.status_code == 200

    dup = _register(client, username="ivy")  # case-insensitive collision
    assert dup.status_code == 409
    assert dup.json()["detail"] == "Bu kullanıcı adı kullanılıyor"


# --- login ----------------------------------------------------------------


def test_login_generic_message_missing_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/api/v1/auth/login", json={"username": "nobody", "password": FAKE_PASSWORD})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Geçersiz kullanıcı adı veya parola"


def test_login_generic_message_wrong_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="jane")
    resp = client.post(
        "/api/v1/auth/login", json={"username": "jane", "password": "wrong-password-here"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Geçersiz kullanıcı adı veya parola"


def test_login_generic_message_inactive_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    user, _ = auth.register_user("kyle", FAKE_PASSWORD)
    auth.set_user_active(user.user_id, False)

    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    resp = client.post("/api/v1/auth/login", json={"username": "kyle", "password": FAKE_PASSWORD})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Geçersiz kullanıcı adı veya parola"


def test_successful_login_returns_cookie_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="liam")
    client.cookies.clear()

    resp = client.post("/api/v1/auth/login", json={"username": "liam", "password": FAKE_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "liam"
    assert isinstance(body["csrf_token"], str)
    cookie_header = _set_cookie_header(resp)
    assert "session_token=" in cookie_header


# --- logout -----------------------------------------------------------------


def test_logout_requires_csrf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="mia")

    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 403

    me = client.get("/api/v1/auth/me").json()
    assert me["authenticated"] is True  # session still valid; logout was rejected


def test_logout_deletes_only_current_session_and_expires_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    reg = _register(client, username="noah")
    user_id = reg.json()["user_id"]

    # A second, independent session for the same user (e.g. another device).
    other_session = AuthRepository(db_path).create_session(user_id)

    logout_resp = client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": reg.json()["csrf_token"]}
    )
    assert logout_resp.status_code == 200

    cookie_header = _set_cookie_header(logout_resp)
    assert "Max-Age=0" in cookie_header or "max-age=0" in cookie_header.lower()

    # The logged-out session is gone.
    me = client.get("/api/v1/auth/me")
    assert me.json()["authenticated"] is False

    # But the other device's session is untouched.
    other_client = TestClient(app)
    other_client.cookies.set("session_token", other_session.token)
    other_me = other_client.get("/api/v1/auth/me")
    assert other_me.json()["authenticated"] is True


# --- rate limiting ------------------------------------------------------------


def test_login_rate_limited_after_five_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="olivia")
    client.cookies.clear()

    for _ in range(5):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "olivia", "password": "wrong-one"}
        )
        assert resp.status_code == 401

    sixth = client.post("/api/v1/auth/login", json={"username": "olivia", "password": "wrong-one"})
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "Çok fazla deneme, daha sonra tekrar deneyin"
    retry_after = int(sixth.headers["retry-after"])
    assert 0 < retry_after <= 15 * 60

    # Even the *correct* password is blocked once rate-limited.
    blocked_correct = client.post(
        "/api/v1/auth/login", json={"username": "olivia", "password": FAKE_PASSWORD}
    )
    assert blocked_correct.status_code == 429


def test_register_rate_limited_after_five_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)

    for _ in range(5):
        resp = _register(client, username="peter", code="wrong-code")
        assert resp.status_code == 400

    sixth = _register(client, username="peter", code=FAKE_REGISTRATION_CODE)
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "Çok fazla deneme, daha sonra tekrar deneyin"
    retry_after = int(sixth.headers["retry-after"])
    assert 0 < retry_after <= 15 * 60


def test_successful_login_clears_only_login_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="quinn")
    client.cookies.clear()

    for _ in range(4):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "quinn", "password": "wrong-one"}
        )
        assert resp.status_code == 401

    good = client.post("/api/v1/auth/login", json={"username": "quinn", "password": FAKE_PASSWORD})
    assert good.status_code == 200
    client.cookies.clear()

    # If the bucket had *not* been cleared, this 4th consecutive failure
    # after the success would be the 8th failure overall and already
    # blocked. It should still be allowed, proving the bucket was reset.
    for _ in range(4):
        resp = client.post(
            "/api/v1/auth/login", json={"username": "quinn", "password": "wrong-one"}
        )
        assert resp.status_code == 401


def test_successful_registration_does_not_clear_login_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _register(client, username="rosa")
    client.cookies.clear()

    for _ in range(5):
        resp = client.post("/api/v1/auth/login", json={"username": "rosa", "password": "wrong-one"})
        assert resp.status_code == 401

    # A successful *registration* (different action, different user) must
    # not clear rosa's login bucket.
    reg2 = _register(client, username="sam")
    assert reg2.status_code == 200
    client.cookies.clear()

    still_blocked = client.post(
        "/api/v1/auth/login", json={"username": "rosa", "password": FAKE_PASSWORD}
    )
    assert still_blocked.status_code == 429


# --- secrecy: response bodies, logs, and the database -----------------------


def test_no_secret_leak_in_registration_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _make_client(tmp_path, monkeypatch)

    # invalid code
    bad_code_resp = _register(client, username="tina", code="another-wrong-code")
    # invalid input
    bad_input_resp = _register(client, username="u", password=FAKE_PASSWORD)
    # duplicate
    good_resp = _register(client, username="uma")
    dup_resp = _register(client, username="uma")
    # login
    login_resp = client.post(
        "/api/v1/auth/login", json={"username": "uma", "password": "not-the-password"}
    )

    all_text = "\n".join(
        r.text for r in (bad_code_resp, bad_input_resp, good_resp, dup_resp, login_resp)
    )
    assert FAKE_REGISTRATION_CODE not in all_text
    assert "another-wrong-code" not in all_text
    assert FAKE_PASSWORD not in all_text
    assert "not-the-password" not in all_text

    assert FAKE_REGISTRATION_CODE not in caplog.text
    assert FAKE_PASSWORD not in caplog.text
    assert "another-wrong-code" not in caplog.text
    assert "not-the-password" not in caplog.text


def test_no_secret_leak_in_database_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    init_db(db_path)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    _register(client, username="vera")
    client.post("/api/v1/auth/login", json={"username": "vera", "password": "wrong-guess-xyz"})
    _register(client, username="wendy", code="bad-code-abc")

    blobs = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            blobs += candidate.read_bytes()

    assert FAKE_REGISTRATION_CODE.encode() not in blobs
    assert b"bad-code-abc" not in blobs
    assert FAKE_PASSWORD.encode() not in blobs
    assert b"wrong-guess-xyz" not in blobs
