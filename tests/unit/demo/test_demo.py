import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.demo import (
    DEMO_SOURCES,
    DEMO_USERNAME,
    DemoCollector,
    demo_collector_funcs,
    seed_demo_account,
)
from surebet.domain.enums import Source, Sport
from surebet.storage.auth import AuthRepository
from surebet.web.app import create_app

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _collect(source: Source, now: datetime = NOW) -> object:
    return asyncio.run(DemoCollector(source, clock=lambda: now).collect())


@pytest.mark.parametrize("source", DEMO_SOURCES)
def test_every_demo_source_yields_a_valid_future_batch(source: Source) -> None:
    batch = _collect(source)

    assert batch.source is source
    assert batch.events
    assert all(event.kickoff > NOW for event in batch.events)
    assert all(event.source_competition is not None for event in batch.events)


def test_prices_are_stable_across_cycles_while_quotes_stay_fresh() -> None:
    first = _collect(Source.SOURCE_B)
    later = asyncio.run(
        DemoCollector(
            Source.SOURCE_B, clock=lambda: NOW + timedelta(minutes=5), anchor=NOW
        ).collect()
    )

    assert len(first.events) == len(later.events)
    for before, after in zip(first.events, later.events, strict=True):
        assert before.kickoff == after.kickoff
        assert {k: q.odds for k, q in before.quotes.items()} == {
            k: q.odds for k, q in after.quotes.items()
        }
        assert all(q.observed_at == NOW + timedelta(minutes=5) for q in after.quotes.values())


def test_odds_stay_above_the_minimum_price() -> None:
    for source in DEMO_SOURCES:
        for event in _collect(source).events:
            prices = [q.odds for q in event.quotes.values()]
            prices += [q.odds for q in (event.bask_ml_quotes or {}).values()]
            assert all(price >= Decimal("1.01") for price in prices)


def test_basketball_fixtures_carry_moneyline_only() -> None:
    events = [e for e in _collect(Source.SOURCE_A).events if e.sport is Sport.BASKETBALL]

    assert events
    assert all(not e.quotes and e.bask_ml_quotes for e in events)


def test_seed_rotates_the_demo_password_on_every_start(tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    auth = AuthRepository(db)

    first = seed_demo_account(db)
    second = seed_demo_account(db)

    assert first != second
    assert auth.verify_credentials(DEMO_USERNAME, first) is None
    assert auth.verify_credentials(DEMO_USERNAME, second) is not None


def test_demo_app_shows_positive_and_negative_margins(tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    password = seed_demo_account(db)
    app = create_app(db_path=db, collector_funcs=demo_collector_funcs())

    with TestClient(app) as client:
        asyncio.run(app.state.collector_runner.run_cycle())
        login = client.post(
            "/api/v1/auth/login", json={"username": DEMO_USERNAME, "password": password}
        )
        assert login.status_code == 200

        positive = client.get("/api/v1/opportunities", params={"budget": 10000}).json()
        everything = client.get(
            "/api/v1/opportunities", params={"budget": 10000, "positive_only": False}
        ).json()

    assert len(positive) >= 3
    assert all(Decimal(str(o["min_adjusted_profit_percent"])) > 0 for o in positive)
    assert any(Decimal(str(o["min_adjusted_profit_percent"])) < 0 for o in everything)
