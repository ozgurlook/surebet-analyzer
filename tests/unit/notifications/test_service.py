"""Multi-user Telegram coordinator tests.

Task 6 re-keyed all Telegram storage by `user_id`; this file proves the
coordinator (`TelegramNotificationService.evaluate_cycle`) actually iterates
every deliverable user off ONE shared catalog snapshot, applies each user's
own budget/tracked-only/taxonomy-filter/threshold, and reconciles lifecycle
state independently per user -- including two users who share one Chat ID.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from surebet.domain.enums import MarketType, Outcome, Source, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.notifications.models import LifecycleStatus, OutboxActionType
from surebet.notifications.render.card import CardRenderer
from surebet.notifications.service import TelegramNotificationService
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository, TelegramSettingsUpdate
from surebet.storage.settings import SystemSettings, UserPreferences
from surebet.storage.snapshots import SourceHealthStatus
from surebet.storage.user_filters import FilterScope, SavedTaxonomyFilters
from surebet.taxonomy.models import TaxonomyStatus

_FAKE_PASSWORD = "correct-horse-battery-staple"
_usernames = (f"telegram-user-{n}" for n in count())

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)

USER_ID = 1

TRACKED_LEAGUE = "league:00000000-0000-4000-8000-000000000001"
UNTRACKED_LEAGUE = "league:00000000-0000-4000-8000-000000000002"
TRACKED_LEAGUE_GB = "league:00000000-0000-4000-8000-000000000003"
COUNTRY_TR = "country:TR"
COUNTRY_GB = "country:GB"


def _matched_event(
    matched_id: str,
    *,
    league_id: str = TRACKED_LEAGUE,
    country_id: str = COUNTRY_TR,
    kickoff: datetime = KICKOFF,
) -> MatchedEvent:
    return MatchedEvent(
        matched_id=matched_id,
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Super League",
        country_id=country_id,
        country_name=country_id,
        country_iso_code="TR",
        league_id=league_id,
        league_name="Super League",
        home="Team A",
        away="Team B",
        kickoff=kickoff,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes={},
    )


def _selection(outcome: Outcome | TwoWayOutcome, source: Source) -> StrategySelection:
    return StrategySelection(
        outcome=outcome,
        source=source,
        odds=Decimal("2.10"),
        stake_cents=1_000,
        cashback_eligible=source in (Source.SOURCE_C, Source.SOURCE_B),
    )


def _opportunity(
    matched_id: str,
    profit_bps: int,
    budget_cents: int,
    *,
    market_type: MarketType = MarketType.FULL_TIME_RESULT,
) -> CalculatedOpportunity:
    profit_cents = profit_bps * budget_cents // 10_000
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
        opportunity_id=f"{matched_id}:{market_type.value}:{profit_bps}:{budget_cents}",
        matched_id=matched_id,
        market_type=market_type,
        competition="Super League",
        country_id=COUNTRY_TR,
        country_name=COUNTRY_TR,
        country_iso_code="TR",
        league_id=TRACKED_LEAGUE,
        league_name="Super League",
        home="Team A",
        away="Team B",
        kickoff=KICKOFF,
        budget_cents=budget_cents,
        include_source_a_match_sides=False,
        selections=selections,
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_cents,
        min_adjusted_profit_percent=round(profit_bps / 100, 2),
        min_standard_profit_cents=profit_cents,
        min_standard_profit_percent=round(profit_bps / 100, 2),
        is_positive=profit_cents > 0,
        oldest_quote_age_seconds=5,
        calculated_at=NOW,
    )


def _healthy() -> dict[Source, Any]:
    return {source: SimpleNamespace(status=SourceHealthStatus.HEALTHY) for source in Source}


class _FakeCatalog:
    """Spies on `build()` so tests can assert it runs exactly once per cycle."""

    def __init__(
        self,
        events: tuple[MatchedEvent, ...] = (),
        health: dict[Source, Any] | None = None,
    ) -> None:
        self.events = events
        self.health = health if health is not None else _healthy()
        self.build_calls: list[bool] = []

    def build(self, now: datetime, *, tracked_only: bool = False) -> Any:
        self.build_calls.append(tracked_only)
        return SimpleNamespace(matched_events=self.events, source_health=self.health, built_at=now)


class _FakeSystemSettingsRepo:
    def __init__(self, staleness_limit_seconds: int = 75) -> None:
        self._settings = SystemSettings(staleness_limit_seconds=staleness_limit_seconds)

    def get(self) -> SystemSettings:
        return self._settings


class _FakeUserPreferencesRepo:
    def __init__(self, preferences: dict[int, UserPreferences] | None = None) -> None:
        self._preferences = dict(preferences or {})
        self.raise_for_user_id: int | None = None

    def get(self, user_id: int) -> UserPreferences:
        if user_id == self.raise_for_user_id:
            raise RuntimeError(f"boom for user {user_id}")
        return self._preferences.get(user_id, UserPreferences())


class _FakeUserFilterRepo:
    def __init__(
        self,
        filters: dict[tuple[int, FilterScope], SavedTaxonomyFilters] | None = None,
    ) -> None:
        self._filters = dict(filters or {})

    def get(self, user_id: int, scope: FilterScope) -> SavedTaxonomyFilters:
        return self._filters.get((user_id, scope), SavedTaxonomyFilters())


class _FakeTaxonomyRepo:
    def __init__(self, tracked_league_ids: frozenset[str] = frozenset({TRACKED_LEAGUE})) -> None:
        self._tracked_league_ids = tracked_league_ids

    def tracked_league_ids(self) -> frozenset[str]:
        return self._tracked_league_ids


class _Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        events: tuple[MatchedEvent, ...] = (),
        candidates: dict[str, int] | None = None,
        candidates_by_budget: dict[tuple[str, int], int] | None = None,
        token_configured: bool = True,
        tracked_league_ids: frozenset[str] = frozenset({TRACKED_LEAGUE}),
        user_preferences: dict[int, UserPreferences] | None = None,
        user_filters: dict[tuple[int, FilterScope], SavedTaxonomyFilters] | None = None,
        use_real_calculator: bool = False,
    ) -> None:
        db_path = tmp_path / "service.db"
        init_db(db_path)
        self.auth = AuthRepository(db_path)
        self.repo = NotificationRepository(db_path)
        self.catalog = _FakeCatalog(events=events)
        self.settings_repo = _FakeSystemSettingsRepo()
        self.user_preferences_repo = _FakeUserPreferencesRepo(user_preferences)
        self.user_filter_repo = _FakeUserFilterRepo(user_filters)
        self.taxonomy_repo = _FakeTaxonomyRepo(tracked_league_ids)
        self._candidates = candidates or {}
        self._candidates_by_budget = candidates_by_budget or {}
        self.service = TelegramNotificationService(
            # Indirection through the harness (not `self.catalog` directly) so a
            # test can swap `harness.catalog` mid-cycle and have the service pick
            # up the new fake on its next `evaluate_cycle()` call.
            catalog_service=self,  # type: ignore[arg-type]
            settings_repo=self.settings_repo,
            notification_repo=self.repo,
            token_configured=lambda: token_configured,
            user_preferences_repo=self.user_preferences_repo,
            user_filter_repo=self.user_filter_repo,
            taxonomy_repo=self.taxonomy_repo,
            calculator=None if use_real_calculator else self._calculator,
        )

    def build(self, now: datetime, *, tracked_only: bool = False) -> Any:
        return self.catalog.build(now, tracked_only=tracked_only)

    def create_user(self) -> int:
        """Register a real user row so `telegram_notification_settings`'s
        `FOREIGN KEY (user_id) REFERENCES users` is satisfiable."""
        user, _session = self.auth.register_user(next(_usernames), _FAKE_PASSWORD)
        return user.user_id

    def enable_user(
        self,
        user_id: int,
        *,
        chat_id: int,
        minimum_profit_bps: int = 95,
        enabled: bool = True,
        validated: bool = True,
    ) -> None:
        self.repo.update_settings(
            user_id,
            TelegramSettingsUpdate(
                enabled=enabled, chat_id=chat_id, minimum_profit_bps=minimum_profit_bps
            ),
        )
        if validated:
            self.repo.mark_chat_validated(user_id, chat_id, NOW)

    def set_preferences(self, user_id: int, preferences: UserPreferences) -> None:
        self.user_preferences_repo._preferences[user_id] = preferences

    def set_filters(self, user_id: int, scope: FilterScope, filters: SavedTaxonomyFilters) -> None:
        self.user_filter_repo._filters[(user_id, scope)] = filters

    def _calculator(self, **kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        event: MatchedEvent = kwargs["event"]
        budget = kwargs["budget_cents"]
        profit_bps = self._candidates_by_budget.get((event.matched_id, budget))
        if profit_bps is None:
            profit_bps = self._candidates.get(event.matched_id)
        if profit_bps is None:
            return ()
        return (_opportunity(event.matched_id, profit_bps, budget),)

    def evaluate_cycle(self, now: datetime) -> None:
        self.service.evaluate_cycle(now)


_reply_message_ids = count(9000)


def _deliver_everything(
    repo: NotificationRepository,
    now: datetime,
    message_id: int | None = None,
) -> None:
    """Fake delivery that answers the way Telegram does.

    A reply (`SEND_ALERT` / `SEND_CLOSED` / `SEND_REVIVED`) creates a brand new
    message, so its response id is deliberately DIFFERENT from the card's -- a
    fake that echoed the card's own id back could never catch a card being
    stamped with its reply's id.
    """
    for record in repo.list_due_actions(now):
        if record.action in (OutboxActionType.SEND_CARD, OutboxActionType.EDIT_CARD):
            mid = message_id if message_id is not None else (record.message_id or 100)
        else:
            mid = next(_reply_message_ids)
        repo.mark_action_delivered(record.outbox_id, now, message_id=mid)
        if record.action in (OutboxActionType.SEND_CARD, "send_card") and record.card_key:
            cards = repo.list_current_cards(record.user_id)
            card = cards.get(record.card_key)
            if card is not None and card.status is LifecycleStatus.NEW:
                active_card = card.model_copy(update={"status": LifecycleStatus.ACTIVE})
                rendered = CardRenderer().render(active_card, now)
                final_card = active_card.model_copy(
                    update={"rendered_fingerprint": rendered.fingerprint}
                )
                repo.apply_transitions([(final_card, ())])


# ---------------------------------------------------------------------------
# Fixtures for the brief's 7 new tests
# ---------------------------------------------------------------------------


@pytest.fixture
def base_harness(tmp_path: Path) -> _Harness:
    harness = _Harness(
        tmp_path,
        events=(_matched_event("event-1"),),
        candidates={"event-1": 150},
    )
    user = harness.create_user()
    assert user == USER_ID
    harness.enable_user(user, chat_id=123)
    return harness


@pytest.fixture
def repo(base_harness: _Harness) -> NotificationRepository:
    return base_harness.repo


@pytest.fixture
def service(base_harness: _Harness) -> TelegramNotificationService:
    return base_harness.service


@pytest.fixture
def calculator_two_markets(base_harness: _Harness) -> None:
    def calc(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        event: MatchedEvent = kwargs["event"]
        budget: int = kwargs["budget_cents"]
        return (
            _opportunity(event.matched_id, 150, budget, market_type=MarketType.FULL_TIME_RESULT),
            _opportunity(event.matched_id, 140, budget, market_type=MarketType.TOTAL_GOALS_25),
        )

    base_harness.service._calculator = calc


@pytest.fixture
def drifting(base_harness: _Harness) -> None:
    def calc(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        event: MatchedEvent = kwargs["event"]
        budget: int = kwargs["budget_cents"]
        now: datetime = kwargs.get("now", NOW)
        profit_bps = 150 if now == NOW else 165
        return (_opportunity(event.matched_id, profit_bps, budget),)

    base_harness.service._calculator = calc


@pytest.fixture
def collapsing(base_harness: _Harness) -> None:
    def calc(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        event: MatchedEvent = kwargs["event"]
        budget: int = kwargs["budget_cents"]
        now: datetime = kwargs.get("now", NOW)
        profit_bps = 150 if now == NOW else 50
        return (_opportunity(event.matched_id, profit_bps, budget),)

    base_harness.service._calculator = calc


@pytest.fixture
def alerting(base_harness: _Harness) -> None:
    def calc(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        event: MatchedEvent = kwargs["event"]
        budget: int = kwargs["budget_cents"]
        now: datetime = kwargs.get("now", NOW)
        profit_bps = 150 if now == NOW else 120
        return (_opportunity(event.matched_id, profit_bps, budget),)

    base_harness.service._calculator = calc


@pytest.fixture
def untracked_event(base_harness: _Harness) -> None:
    base_harness.catalog.events = (_matched_event("event-1", league_id=UNTRACKED_LEAGUE),)


@pytest.fixture
def service_two_users(base_harness: _Harness) -> TelegramNotificationService:
    user_2 = base_harness.create_user()
    base_harness.enable_user(user_2, chat_id=456)
    base_harness.user_preferences_repo.raise_for_user_id = USER_ID
    return base_harness.service


# ---------------------------------------------------------------------------
# Single-user regression coverage (updated to card architecture)
# ---------------------------------------------------------------------------


def test_service_reconciles_current_and_missing_events_without_network_io(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, events=(_matched_event("match-1"),), candidates={"match-1": 134})
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)

    harness.catalog = _FakeCatalog(events=())
    harness.evaluate_cycle(NOW + timedelta(seconds=30))

    cards = harness.repo.list_current_cards(user)
    assert "match-1:1X2" in cards
    assert cards["match-1:1X2"].status is LifecycleStatus.WATCH


def test_service_skips_when_disabled_or_chat_not_validated(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    user = harness.create_user()
    harness.enable_user(user, chat_id=123, validated=False)
    harness.evaluate_cycle(NOW)
    assert harness.repo.list_current_cards(user) == {}


def test_service_skips_when_server_token_is_not_configured(tmp_path: Path) -> None:
    harness = _Harness(
        tmp_path,
        events=(_matched_event("match-1"),),
        candidates={"match-1": 134},
        token_configured=False,
    )
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)
    assert harness.repo.list_current_cards(user) == {}
    assert harness.catalog.build_calls == []


def test_service_creates_new_lifecycle_and_queues_full_send(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, events=(_matched_event("match-1"),), candidates={"match-1": 134})
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)

    cards = harness.repo.list_current_cards(user)
    assert "match-1:1X2" in cards
    assert cards["match-1:1X2"].status in (LifecycleStatus.NEW, LifecycleStatus.ACTIVE)
    due = harness.repo.list_due_actions(NOW)
    assert len(due) == 1
    assert due[0].action is OutboxActionType.SEND_CARD


def test_service_never_creates_a_lifecycle_for_a_sub_threshold_event(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, events=(_matched_event("match-1"),), candidates={"match-1": 50})
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)

    assert harness.repo.list_current_cards(user) == {}


def test_service_retires_a_state_whose_kickoff_has_passed_without_a_new_evaluation(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path, events=(_matched_event("match-1"),), candidates={"match-1": 134})
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)

    harness.catalog = _FakeCatalog(events=())
    past_kickoff = KICKOFF + timedelta(hours=3)
    harness.evaluate_cycle(past_kickoff)

    cards = harness.repo.list_current_cards(user)
    assert "match-1:1X2" in cards
    assert cards["match-1:1X2"].status is LifecycleStatus.CLOSED


# ---------------------------------------------------------------------------
# Multi-user coordinator behavior
# ---------------------------------------------------------------------------


def test_catalog_snapshot_is_built_exactly_once_per_cycle_for_multiple_users(
    tmp_path: Path,
) -> None:
    events = (
        _matched_event("match-tracked", league_id=TRACKED_LEAGUE, country_id=COUNTRY_TR),
        _matched_event("match-untracked", league_id=UNTRACKED_LEAGUE, country_id=COUNTRY_GB),
    )
    harness = _Harness(
        tmp_path,
        events=events,
        candidates={"match-tracked": 200, "match-untracked": 200},
    )
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=100)
    harness.enable_user(user_b, chat_id=200)
    harness.set_preferences(
        user_a, UserPreferences(tracked_competitions_only=True, default_budget_cents=500_000)
    )
    harness.set_preferences(
        user_b, UserPreferences(tracked_competitions_only=False, default_budget_cents=750_000)
    )

    harness.evaluate_cycle(NOW)

    assert harness.catalog.build_calls == [False]


def test_each_user_gets_only_their_own_taxonomy_filtered_candidate_set(tmp_path: Path) -> None:
    """Saved per-user filters narrow within the locked tracked scope, never past it."""
    event_tr = _matched_event("match-tr", league_id=TRACKED_LEAGUE, country_id=COUNTRY_TR)
    event_gb = _matched_event("match-gb", league_id=TRACKED_LEAGUE_GB, country_id=COUNTRY_GB)
    event_untracked = _matched_event(
        "match-untracked", league_id=UNTRACKED_LEAGUE, country_id=COUNTRY_GB
    )
    harness = _Harness(
        tmp_path,
        events=(event_tr, event_gb, event_untracked),
        candidates={"match-tr": 200, "match-gb": 200, "match-untracked": 200},
        tracked_league_ids=frozenset({TRACKED_LEAGUE, TRACKED_LEAGUE_GB}),
    )
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=100, minimum_profit_bps=95)
    harness.enable_user(user_b, chat_id=200, minimum_profit_bps=95)
    harness.set_preferences(user_a, UserPreferences(default_budget_cents=500_000))
    harness.set_preferences(user_b, UserPreferences(default_budget_cents=750_000))
    harness.set_filters(
        user_a, FilterScope.OPPORTUNITIES, SavedTaxonomyFilters(country_ids=frozenset({COUNTRY_TR}))
    )
    harness.set_filters(
        user_b, FilterScope.OPPORTUNITIES, SavedTaxonomyFilters(country_ids=frozenset({COUNTRY_GB}))
    )

    harness.evaluate_cycle(NOW)

    cards_a = harness.repo.list_current_cards(user_a)
    cards_b = harness.repo.list_current_cards(user_b)
    assert "match-tr:1X2" in cards_a
    assert "match-gb:1X2" not in cards_a
    assert "match-gb:1X2" in cards_b
    assert "match-tr:1X2" not in cards_b
    # The untracked-league event matches user B's GB filter, yet the locked
    # scope keeps it out of both users' candidate sets.
    assert "match-untracked:1X2" not in cards_a
    assert "match-untracked:1X2" not in cards_b


def test_stake_distributions_reflect_each_users_own_budget(tmp_path: Path) -> None:
    event = _matched_event("match-1")
    harness = _Harness(
        tmp_path,
        events=(event,),
        candidates={"match-1": 200},
    )
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=100)
    harness.enable_user(user_b, chat_id=200)
    harness.set_preferences(
        user_a, UserPreferences(tracked_competitions_only=False, default_budget_cents=300_000)
    )
    harness.set_preferences(
        user_b, UserPreferences(tracked_competitions_only=False, default_budget_cents=900_000)
    )

    harness.evaluate_cycle(NOW)

    cards_a = harness.repo.list_current_cards(user_a)
    cards_b = harness.repo.list_current_cards(user_b)
    assert "match-1:1X2" in cards_a and "match-1:1X2" in cards_b
    assert cards_a["match-1:1X2"].evaluation.selected.budget_cents == 300_000
    assert cards_b["match-1:1X2"].evaluation.selected.budget_cents == 900_000


def test_each_users_minimum_profit_threshold_is_applied_independently(tmp_path: Path) -> None:
    event = _matched_event("match-1")
    harness = _Harness(tmp_path, events=(event,), candidates={"match-1": 100})
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=100, minimum_profit_bps=50)  # qualifies
    harness.enable_user(user_b, chat_id=200, minimum_profit_bps=150)  # does not qualify
    harness.set_preferences(user_a, UserPreferences(tracked_competitions_only=False))
    harness.set_preferences(user_b, UserPreferences(tracked_competitions_only=False))

    harness.evaluate_cycle(NOW)

    cards_a = harness.repo.list_current_cards(user_a)
    cards_b = harness.repo.list_current_cards(user_b)
    assert "match-1:1X2" in cards_a
    assert "match-1:1X2" not in cards_b


def test_disabled_or_unvalidated_user_is_skipped_without_affecting_others(tmp_path: Path) -> None:
    event = _matched_event("match-1")
    harness = _Harness(tmp_path, events=(event,), candidates={"match-1": 200})
    user_a = harness.create_user()
    user_b = harness.create_user()
    user_c = harness.create_user()
    harness.enable_user(user_a, chat_id=100)
    harness.enable_user(user_b, chat_id=200, enabled=False)  # never deliverable
    harness.enable_user(user_c, chat_id=300, validated=False)  # not yet validated
    harness.set_preferences(user_a, UserPreferences(tracked_competitions_only=False))
    harness.set_preferences(user_b, UserPreferences(tracked_competitions_only=False))
    harness.set_preferences(user_c, UserPreferences(tracked_competitions_only=False))

    harness.evaluate_cycle(NOW)

    assert "match-1:1X2" in harness.repo.list_current_cards(user_a)
    assert harness.repo.list_current_cards(user_b) == {}
    assert harness.repo.list_current_cards(user_c) == {}


def test_two_users_sharing_one_chat_id_are_evaluated_and_reconciled_independently(
    tmp_path: Path,
) -> None:
    event = _matched_event("match-1")
    shared_chat_id = 999
    harness = _Harness(tmp_path, events=(event,), candidates={"match-1": 200})
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=shared_chat_id, minimum_profit_bps=50)
    harness.enable_user(user_b, chat_id=shared_chat_id, minimum_profit_bps=50)
    harness.set_preferences(
        user_a, UserPreferences(tracked_competitions_only=False, default_budget_cents=250_000)
    )
    harness.set_preferences(
        user_b, UserPreferences(tracked_competitions_only=False, default_budget_cents=800_000)
    )

    harness.evaluate_cycle(NOW)

    cards_a = harness.repo.list_current_cards(user_a)
    cards_b = harness.repo.list_current_cards(user_b)
    assert "match-1:1X2" in cards_a and "match-1:1X2" in cards_b
    assert cards_a["match-1:1X2"].user_id == user_a
    assert cards_b["match-1:1X2"].user_id == user_b
    assert cards_a["match-1:1X2"].evaluation.selected.budget_cents == 250_000
    assert cards_b["match-1:1X2"].evaluation.selected.budget_cents == 800_000

    due = harness.repo.list_due_actions(NOW)
    assert len(due) == 2
    assert {action.user_id for action in due} == {user_a, user_b}


def test_one_users_evaluation_exception_does_not_prevent_other_users_from_being_evaluated(
    tmp_path: Path,
) -> None:
    event = _matched_event("match-1")
    harness = _Harness(tmp_path, events=(event,), candidates={"match-1": 200})
    user_a = harness.create_user()
    user_b = harness.create_user()
    harness.enable_user(user_a, chat_id=100)
    harness.enable_user(user_b, chat_id=200)
    harness.set_preferences(user_a, UserPreferences(tracked_competitions_only=False))
    harness.set_preferences(user_b, UserPreferences(tracked_competitions_only=False))
    harness.user_preferences_repo.raise_for_user_id = user_a

    harness.evaluate_cycle(NOW)

    assert harness.repo.list_current_cards(user_a) == {}
    assert "match-1:1X2" in harness.repo.list_current_cards(user_b)


def test_both_source_a_policies_evaluate_correctly_through_the_real_evaluator(
    tmp_path: Path,
) -> None:
    """Uses the real `evaluate_event_by_market`/`calculate_event_opportunities` (no faked
    calculator) to prove the coordinator's default wiring still exercises the
    genuine dual-policy evaluator, not just a test double."""
    quotes = {
        (Source.SOURCE_C, Outcome.HOME): Quote(
            source=Source.SOURCE_C,
            event_id="m1",
            outcome=Outcome.HOME,
            odds=Decimal("2.10"),
            observed_at=NOW,
        ),
        (Source.SOURCE_A, Outcome.DRAW): Quote(
            source=Source.SOURCE_A,
            event_id="s1",
            outcome=Outcome.DRAW,
            odds=Decimal("4.20"),
            observed_at=NOW,
        ),
        (Source.SOURCE_B, Outcome.AWAY): Quote(
            source=Source.SOURCE_B,
            event_id="j1",
            outcome=Outcome.AWAY,
            odds=Decimal("4.20"),
            observed_at=NOW,
        ),
    }
    event = MatchedEvent(
        matched_id="match-real",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Super League",
        country_id=COUNTRY_TR,
        country_name=COUNTRY_TR,
        country_iso_code="TR",
        league_id=TRACKED_LEAGUE,
        league_name="Super League",
        home="Team A",
        away="Team B",
        kickoff=KICKOFF,
        source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
        quotes=quotes,
    )
    harness = _Harness(tmp_path, events=(event,), use_real_calculator=True)
    user = harness.create_user()
    harness.enable_user(user, chat_id=100, minimum_profit_bps=1)
    harness.set_preferences(
        user, UserPreferences(tracked_competitions_only=False, default_budget_cents=1_000_000)
    )

    harness.evaluate_cycle(NOW)

    cards = harness.repo.list_current_cards(user)
    assert "match-real:1X2" in cards
    state = cards["match-real:1X2"]
    assert state.evaluation.selected is not None
    assert state.evaluation.draw_only is not None
    assert isinstance(state.evaluation.uses_source_a_match_sides, bool)


# ---------------------------------------------------------------------------
# Task 11 tests from brief
# ---------------------------------------------------------------------------


def test_two_markets_on_one_match_produce_two_cards(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    calculator_two_markets: None,
) -> None:
    service.evaluate_cycle(NOW)

    cards = repo.list_current_cards(USER_ID)
    assert set(cards) == {"event-1:1X2", "event-1:OU25"}


def test_user_can_disable_market_notifications(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    calculator_two_markets: None,
) -> None:
    # First cycle: both markets enabled
    service.evaluate_cycle(NOW)
    cards = repo.list_current_cards(USER_ID)
    assert set(cards) == {"event-1:1X2", "event-1:OU25"}
    _deliver_everything(repo, NOW)

    # Disable OU25 notifications
    repo.update_settings(USER_ID, TelegramSettingsUpdate(notify_ou25=False))

    # Next cycle: must NOT emit any outbox actions (no edit_card, no send_closed) for OU25!
    service.evaluate_cycle(NOW + timedelta(seconds=60))
    due = repo.list_due_actions(NOW + timedelta(days=1))
    ou25_actions = [a for a in due if "OU25" in a.card_key]
    assert ou25_actions == []


def test_an_unchanged_card_enqueues_no_edit(
    service: TelegramNotificationService,
    repo: NotificationRepository,
) -> None:
    service.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)

    service.evaluate_cycle(NOW + timedelta(seconds=90))

    assert repo.list_due_actions(NOW + timedelta(seconds=90)) == ()


def test_undelivered_card_without_message_id_re_emits_send_card_even_if_fingerprint_unchanged(
    service: TelegramNotificationService,
    repo: NotificationRepository,
) -> None:
    service.evaluate_cycle(NOW)
    [initial_action] = repo.list_due_actions(NOW)
    assert initial_action.action is OutboxActionType.SEND_CARD

    repo.fail_action(initial_action.outbox_id, "exhausted attempts")
    assert repo.list_due_actions(NOW) == ()

    card = repo.list_current_cards(USER_ID)["event-1:1X2"]
    assert card.message_id is None

    service.evaluate_cycle(NOW + timedelta(seconds=30))
    due = repo.list_due_actions(NOW + timedelta(seconds=30))
    assert len(due) == 1
    assert due[0].action is OutboxActionType.SEND_CARD


def test_a_changed_card_is_deferred_to_the_minimum_edit_interval(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    drifting: None,
) -> None:
    service.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)

    service.evaluate_cycle(NOW + timedelta(seconds=30))

    due_now = repo.list_due_actions(NOW + timedelta(seconds=30))
    assert due_now == ()
    due_later = repo.list_due_actions(NOW + timedelta(seconds=60))
    assert [record.action for record in due_later] == [OutboxActionType.EDIT_CARD]


def test_a_status_change_bypasses_the_edit_interval(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    collapsing: None,
) -> None:
    service.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)

    service.evaluate_cycle(NOW + timedelta(seconds=30))

    assert [r.action for r in repo.list_due_actions(NOW + timedelta(seconds=30))] == [
        OutboxActionType.EDIT_CARD
    ]


def test_alerts_are_addressed_to_the_card_message(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    alerting: None,
) -> None:
    service.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW, message_id=4242)

    service.evaluate_cycle(NOW + timedelta(minutes=6))

    alert = next(
        r
        for r in repo.list_due_actions(NOW + timedelta(minutes=6))
        if r.action is OutboxActionType.SEND_ALERT
    )
    assert alert.message_id == 4242


def test_untracked_leagues_are_never_evaluated(
    service: TelegramNotificationService,
    repo: NotificationRepository,
    untracked_event: None,
) -> None:
    service.evaluate_cycle(NOW)
    assert repo.list_current_cards(USER_ID) == {}


def test_one_user_failing_does_not_stop_the_others(
    service_two_users: TelegramNotificationService,
    repo: NotificationRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service_two_users.evaluate_cycle(NOW)
    assert repo.list_current_cards(2) != {}


def test_a_delivered_alert_does_not_hijack_the_cards_message_id(base_harness, alerting) -> None:
    """C1: the card must keep pointing at the card, not at its latest reply."""
    repo = base_harness.repo
    base_harness.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)
    assert repo.list_current_cards(USER_ID)["event-1:1X2"].message_id == 100

    later = NOW + timedelta(minutes=6)
    base_harness.evaluate_cycle(later)

    # Deliver ONLY the alert reply. Its accompanying EDIT_CARD is routinely
    # deferred (chat budget, MIN_EDIT_INTERVAL_SECONDS), so nothing follows to
    # repair a card row that the reply corrupted.
    [alert] = [
        action
        for action in repo.list_due_actions(later)
        if action.action is OutboxActionType.SEND_ALERT
    ]
    repo.mark_action_delivered(alert.outbox_id, later, message_id=9999)

    card = repo.list_current_cards(USER_ID)["event-1:1X2"]
    assert card.message_id == 100


def test_a_long_dead_card_produces_no_write_on_a_later_cycle(base_harness, collapsing) -> None:
    """I5: closed cards must stop costing a render and an UPSERT every cycle."""
    repo = base_harness.repo
    base_harness.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)

    collapsed = NOW + timedelta(minutes=10)
    base_harness.evaluate_cycle(collapsed)
    base_harness.evaluate_cycle(collapsed + timedelta(minutes=5))  # grace elapsed
    assert repo.list_current_cards(USER_ID)["event-1:1X2"].status is LifecycleStatus.CLOSED

    written: list[list[Any]] = []
    real_apply = repo.apply_transitions

    def spy(transitions) -> None:
        written.append(list(transitions))
        real_apply(transitions)

    repo.apply_transitions = spy
    try:
        base_harness.evaluate_cycle(collapsed + timedelta(minutes=45))
    finally:
        repo.apply_transitions = real_apply

    assert written == [[]]


def test_cards_past_their_revival_window_leave_the_per_cycle_read(base_harness, collapsing) -> None:
    repo = base_harness.repo
    base_harness.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)
    collapsed = NOW + timedelta(minutes=10)
    base_harness.evaluate_cycle(collapsed)
    base_harness.evaluate_cycle(collapsed + timedelta(minutes=5))

    still_revivable = repo.list_current_cards(
        USER_ID, retired_before=collapsed + timedelta(minutes=5)
    )
    assert "event-1:1X2" in still_revivable

    retired = repo.list_current_cards(USER_ID, retired_before=collapsed + timedelta(minutes=45))
    assert retired == {}
    # ...but the row itself is still there until retention removes it.
    assert "event-1:1X2" in repo.list_current_cards(USER_ID)


def test_retention_deletes_closed_cards_and_spares_live_ones(base_harness, collapsing) -> None:
    repo = base_harness.repo
    base_harness.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW)
    collapsed = NOW + timedelta(minutes=10)
    base_harness.evaluate_cycle(collapsed)
    base_harness.evaluate_cycle(collapsed + timedelta(minutes=5))

    assert repo.purge_retired_cards(NOW - timedelta(days=1)) == 0
    assert repo.list_current_cards(USER_ID) != {}

    assert repo.purge_retired_cards(NOW + timedelta(days=8)) == 1
    assert repo.list_current_cards(USER_ID) == {}


def test_evaluate_cycle_purges_completed_outbox_past_retention(base_harness) -> None:
    from surebet.storage.db import get_connection

    repo = base_harness.repo
    service = base_harness.service

    # Seed delivered outbox row updated at NOW
    with get_connection(repo._db_path) as conn:
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'send_card', 'delivered', 0, 1, ?, '{}', ?, ?);",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )

    # Cycle at NOW + 6 days -> still retained
    service.evaluate_cycle(NOW + timedelta(days=6))
    with get_connection(repo._db_path) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM telegram_notification_outbox WHERE status = 'delivered';"
            ).fetchone()[0]
            == 1
        )

    # Cycle at NOW + 8 days -> purged
    service.evaluate_cycle(NOW + timedelta(days=8))
    with get_connection(repo._db_path) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM telegram_notification_outbox WHERE status = 'delivered';"
            ).fetchone()[0]
            == 0
        )


def test_market_evaluations_are_cached_by_event_and_budget_per_cycle(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from surebet.storage.settings import UserPreferences

    event = _matched_event("event-1")
    harness = _Harness(tmp_path, events=(event,), candidates={"event-1": 150})
    user1 = harness.create_user()
    harness.enable_user(user1, chat_id=101)
    user2 = harness.create_user()
    harness.enable_user(user2, chat_id=102)

    original_calc = harness._calculator
    calc_spy = MagicMock(side_effect=original_calc)
    harness.service._calculator = calc_spy

    # Cycle 1: user1 and user2 both have default budget 100_000 cents
    harness.service.evaluate_cycle(NOW)

    # evaluate_event_by_market calls calculator 2 times per evaluation (draw-only and wide).
    # Since evaluations are memoized per (event, budget), calculator must be called exactly 2 times, NOT 4.
    assert calc_spy.call_count == 2

    # Add user3 with a different budget: 200_000 cents
    user3 = harness.create_user()
    harness.enable_user(user3, chat_id=103)
    harness.set_preferences(
        user3,
        UserPreferences(user_id=user3, default_budget_cents=200_000),
    )

    calc_spy.reset_mock()

    # Cycle 2: user1 & user2 share (event-1, 100_000) -> 1 evaluation (2 calls)
    #          user3 has (event-1, 200_000) -> 1 evaluation (2 calls)
    # Total for cycle 2: 4 calls
    harness.service.evaluate_cycle(NOW + timedelta(seconds=30))
    assert calc_spy.call_count == 4


def test_muted_card_suppresses_edit_card_and_send_alert(
    base_harness: _Harness,
    repo: NotificationRepository,
    service: TelegramNotificationService,
    alerting: None,
) -> None:
    # 1. Initial cycle: card created and delivered
    service.evaluate_cycle(NOW)
    _deliver_everything(repo, NOW, message_id=4242)

    # 2. Mute the card for 2 hours
    repo.mute_card(
        chat_id=123,
        card_key="event-1:1X2",
        duration_seconds=7200,
        now=NOW,
    )
    assert repo.is_card_muted(123, "event-1:1X2", NOW + timedelta(minutes=6))

    # 3. Next cycle with alerting: without mute this would emit EDIT_CARD and SEND_ALERT
    service.evaluate_cycle(NOW + timedelta(minutes=6))

    # Both EDIT_CARD and SEND_ALERT must be suppressed
    due = repo.list_due_actions(NOW + timedelta(minutes=10))
    suppressed_actions = [
        r.action
        for r in due
        if r.action in (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_ALERT)
    ]
    assert suppressed_actions == []

    # Card state is still updated in the repo
    cards = repo.list_current_cards(USER_ID)
    assert "event-1:1X2" in cards
    assert cards["event-1:1X2"].message_id == 4242

    # 4. If card is unmuted, subsequent alert/edit cycle queues normally
    repo.unmute_card(chat_id=123, card_key="event-1:1X2")
    assert not repo.is_card_muted(123, "event-1:1X2", NOW + timedelta(minutes=7))


def test_service_filters_events_further_than_two_days_ahead(tmp_path: Path) -> None:
    near_event = _matched_event("match-near", kickoff=NOW + timedelta(days=1))
    far_event = _matched_event("match-far", kickoff=NOW + timedelta(days=2, hours=1))
    harness = _Harness(
        tmp_path,
        events=(near_event, far_event),
        candidates={"match-near": 150, "match-far": 150},
    )
    user = harness.create_user()
    harness.enable_user(user, chat_id=123)
    harness.evaluate_cycle(NOW)

    cards = harness.repo.list_current_cards(user)
    assert "match-near:1X2" in cards
    assert "match-far:1X2" not in cards

    due = harness.repo.list_due_actions(NOW + timedelta(minutes=1))
    keys = {action.card_key for action in due}
    assert "match-near:1X2" in keys
    assert "match-far:1X2" not in keys
