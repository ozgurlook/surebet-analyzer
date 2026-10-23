from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.web.app import create_app
from surebet.web.collector_runner import CollectorRunner


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, str, CollectorRunner]:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("sources-tester", "correct-horse-battery-staple")

    mock_runner = AsyncMock(spec=CollectorRunner)
    mock_runner.trigger_refresh = AsyncMock(return_value=True)

    app = create_app(db_path=db_path, collector_runner=mock_runner)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token, mock_runner


def test_sources_health_and_refresh(client: tuple[TestClient, str, CollectorRunner]) -> None:
    c, csrf, runner = client

    # GET sources health
    resp = c.get("/api/v1/sources/health")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert data["source_c"]["status"] == "disabled"
    assert "Devre dışı" in data["source_c"]["error_message"]
    assert data["source_b"]["status"] == "unavailable"

    # POST sources refresh without CSRF
    bad_refresh = c.post("/api/v1/sources/refresh")
    assert bad_refresh.status_code == 403

    # POST sources refresh with CSRF
    good_refresh = c.post(
        "/api/v1/sources/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert good_refresh.status_code == 200
    assert good_refresh.json()["status"] == "triggered"
    runner.trigger_refresh.assert_called_once()
