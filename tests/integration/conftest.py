"""Integration test fixtures for Telegram notification lifecycle."""

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, NamedTuple

import pytest

from surebet.domain.enums import MarketType, Outcome, Source, TwoWayOutcome
from surebet.matching.models import MatchedEvent
from surebet.notifications.dispatcher import TelegramOutboxDispatcher
from surebet.notifications.models import TelegramResult
from surebet.notifications.service import TelegramNotificationService
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository, TelegramSettingsUpdate
from surebet.storage.settings import SystemSettings
from surebet.storage.snapshots import SourceHealthStatus
from surebet.storage.user_filters import FilterScope, SavedTaxonomyFilters
from surebet.taxonomy.models import TaxonomyStatus

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
CHAT_ID = 555
MATCHED_ID = "smoke-event-1"
BUDGET_CENTS = 1_000_000
MATCH_LEAGUE_ID = "league:00000000-0000-4000-8000-000000000002"


def _selection(outcome: Outcome | TwoWayOutcome, source: Source) -> StrategySelection:
    return StrategySelection(
        outcome=outcome,
        source=source,
        odds=Decimal("2.10"),
        stake_cents=1_000,
        cashback_eligible=source in (Source.SOURCE_C, Source.SOURCE_B),
    )


def _opportunity(
    profit_bps: int,
    market_type: MarketType,
    now: datetime,
) -> CalculatedOpportunity:
    profit_cents = profit_bps * BUDGET_CENTS // 10_000
    if market_type is MarketType.TOTAL_GOALS_25:
        selections: MappingProxyType[Outcome | TwoWayOutcome, StrategySelection] = MappingProxyType(
            {
                TwoWayOutcome.OVER: _selection(TwoWayOutcome.OVER, Source.SOURCE_C),
                TwoWayOutcome.UNDER: _selection(TwoWayOutcome.UNDER, Source.SOURCE_B),
            }
        )

    else:
        selections = MappingProxyType(
            {
                Outcome.HOME: _selection(Outcome.HOME, Source.SOURCE_C),
                Outcome.DRAW: _selection(Outcome.DRAW, Source.SOURCE_A),
                Outcome.AWAY: _selection(Outcome.AWAY, Source.SOURCE_B),
            }
        )
    return CalculatedOpportunity(
        opportunity_id=f"{MATCHED_ID}:{market_type.value}:{profit_bps}",
        matched_id=MATCHED_ID,
        market_type=market_type,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id=MATCH_LEAGUE_ID,
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        budget_cents=BUDGET_CENTS,
        include_source_a_match_sides=False,
        selections=selections,
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_cents,
        min_adjusted_profit_percent=round(profit_bps / 100, 2),
        min_standard_profit_cents=profit_cents,
        min_standard_profit_percent=round(profit_bps / 100, 2),
        is_positive=profit_cents > 0,
        oldest_quote_age_seconds=5,
        calculated_at=now,
    )


def _matched_event() -> MatchedEvent:
    return MatchedEvent(
        matched_id=MATCHED_ID,
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id=MATCH_LEAGUE_ID,
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes={},
    )


class _FakeCatalog:
    def __init__(self) -> None:
        self.health: dict[Source, Any] = {
            source: SimpleNamespace(status=SourceHealthStatus.HEALTHY) for source in Source
        }

    def build(self, now: datetime, *, tracked_only: bool = False) -> Any:
        return SimpleNamespace(
            matched_events=(_matched_event(),),
            source_health=self.health,
            built_at=now,
        )


class _FakeSystemSettingsRepo:
    def get(self) -> SystemSettings:
        return SystemSettings()


class _FakeUserPreferencesRepo:
    def get(self, user_id: int) -> Any:
        from surebet.storage.settings import UserPreferences

        return UserPreferences(default_budget_cents=BUDGET_CENTS)


class _FakeUserFilterRepo:
    def get(self, user_id: int, scope: FilterScope) -> SavedTaxonomyFilters:
        return SavedTaxonomyFilters()


class _FakeTaxonomyRepo:
    def tracked_league_ids(self) -> frozenset[str]:
        return frozenset({MATCH_LEAGUE_ID})


class _IntegrationTelegramClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        silent: bool = False,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramResult:
        msg_id = len(self.responses) + 1
        record = {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "silent": silent,
            "reply_markup": reply_markup,
        }
        self.sent.append(record)
        response = {"message_id": msg_id}
        self.responses.append(response)
        return TelegramResult(ok=True, message_id=msg_id)

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramResult:
        self.edited.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return TelegramResult(ok=True, message_id=message_id)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def query_counter(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Any]:
    """Records every SQL statement executed while the context is open.

    Wraps sqlite3.connect so it catches connections opened inside the code under
    test, not just ones the test made itself.
    """

    @contextmanager
    def counter():
        statements: list[str] = []
        real_connect = sqlite3.connect

        def traced_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        monkeypatch.setattr(sqlite3, "connect", traced_connect)
        try:
            yield statements
        finally:
            monkeypatch.setattr(sqlite3, "connect", real_connect)

    return counter


class AppContext(NamedTuple):
    """The wired service/dispatcher stack, plus the knobs a scenario needs.

    `catalog.health` is mutable so a test can take a source offline mid-run, and
    `profit` overrides the default decline curve per market.
    """

    service: TelegramNotificationService
    dispatcher: TelegramOutboxDispatcher
    repo: NotificationRepository
    client: "_IntegrationTelegramClient"
    catalog: "_FakeCatalog"
    profit: dict[MarketType, int]


@pytest.fixture
def app_context(tmp_path: Path) -> AppContext:
    db_path = tmp_path / "smoke_app.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    user, _ = auth.register_user("smoke-user", "correct-horse-battery-staple")
    user_id = user.user_id

    repo = NotificationRepository(db_path)
    repo.update_settings(user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_id, CHAT_ID, NOW)

    catalog = _FakeCatalog()
    clock_cell = {"now": NOW}

    profit_override: dict[MarketType, int] = {}

    def calculator(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        now = kwargs.get("now", clock_cell["now"])
        if now >= NOW + timedelta(minutes=5):
            curve = {MarketType.FULL_TIME_RESULT: 210, MarketType.TOTAL_GOALS_25: 150}
        else:
            curve = {MarketType.FULL_TIME_RESULT: 240, MarketType.TOTAL_GOALS_25: 180}

        return tuple(
            _opportunity(profit_override.get(market, default), market, now)
            for market, default in curve.items()
        )

    service = TelegramNotificationService(
        catalog_service=catalog,  # type: ignore[arg-type]
        settings_repo=_FakeSystemSettingsRepo(),  # type: ignore[arg-type]
        notification_repo=repo,
        token_configured=lambda: True,
        user_preferences_repo=_FakeUserPreferencesRepo(),  # type: ignore[arg-type]
        user_filter_repo=_FakeUserFilterRepo(),  # type: ignore[arg-type]
        taxonomy_repo=_FakeTaxonomyRepo(),  # type: ignore[arg-type]
        calculator=calculator,
    )

    orig_evaluate_cycle = service.evaluate_cycle

    def evaluate_cycle_tracking_clock(now: datetime) -> None:
        clock_cell["now"] = now
        orig_evaluate_cycle(now)

    service.evaluate_cycle = evaluate_cycle_tracking_clock  # type: ignore[method-assign]

    client = _IntegrationTelegramClient()
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: clock_cell["now"])  # type: ignore[arg-type]

    return AppContext(service, dispatcher, repo, client, catalog, profit_override)
