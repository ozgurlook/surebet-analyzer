"""Shared fixtures.

The tracked-competition registry is the product's hard scope boundary rather
than a user preference, so tests that seed their own competitions declare a
matching registry instead of widening the catalog at request time.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.notifications.models import (
    CardState,
    DualPolicyEvaluation,
    LifecycleStatus,
    card_key,
)
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.sources.endpoints import SourceEndpointConfig, endpoint_env_prefix
from surebet.storage.notifications import TelegramNotificationSettings
from surebet.storage.snapshots import SourceHealthStatus
from surebet.taxonomy.tracked import TrackedCompetitionRegistry

TrackedSpec = tuple[str, str, str, Mapping[Source, str]]
RegistryFactory = Callable[[Sequence[TrackedSpec]], TrackedCompetitionRegistry]

EXAMPLE_OPERATOR_IDS: Mapping[Source, int] = MappingProxyType(
    {
        Source.SOURCE_A: 1000,
        Source.SOURCE_C: 1001,
        Source.SOURCE_B: 1002,
        Source.SOURCE_E: 1003,
        Source.SOURCE_F: 1004,
    }
)
_WAMP_SOURCES = frozenset({Source.SOURCE_B, Source.SOURCE_C, Source.SOURCE_E, Source.SOURCE_F})


def example_host(source: Source) -> str:
    """Hostname on the reserved `.test` TLD, so a misrouted test can never reach a real host."""

    return f"{source.value.replace('_', '-')}.example.test"


def example_endpoint(source: Source) -> SourceEndpointConfig:
    host = example_host(source)
    # Source D speaks plain HTTPS JSON; every other adapter is a WebSocket feed.
    endpoint = f"https://{host}" if source is Source.SOURCE_D else f"wss://{host}/v2"
    return SourceEndpointConfig(
        source=source,
        page_url=f"https://{host}/sports",
        websocket_url=endpoint,
        origin=f"https://{host}",
        realm=f"realm-{source.value}" if source in _WAMP_SOURCES else None,
        operator_id=EXAMPLE_OPERATOR_IDS.get(source),
    )


@pytest.fixture(autouse=True)
def _example_source_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every adapter at `.test` hosts so default construction works offline."""

    for source in Source:
        config = example_endpoint(source)
        prefix = endpoint_env_prefix(source)
        monkeypatch.setenv(prefix + "ENDPOINT", config.websocket_url)
        monkeypatch.setenv(prefix + "ORIGIN", config.origin)
        monkeypatch.setenv(prefix + "PAGE_URL", config.page_url)
        if config.realm is not None:
            monkeypatch.setenv(prefix + "REALM", config.realm)
        if config.operator_id is not None:
            monkeypatch.setenv(prefix + "OPERATOR_ID", str(config.operator_id))


@pytest.fixture
def tracked_registry_factory() -> RegistryFactory:
    """Build a registry from (key, region_id, display_name, sources) specs.

    `sources` maps each `Source` to the provider competition id that source
    publishes for that competition, mirroring `tracked.tr.json`. `display_name`
    becomes the canonical league name, exactly as it does for the packaged
    registry, so fixtures must give the name they expect to read back.
    """

    def build(specs: Sequence[TrackedSpec]) -> TrackedCompetitionRegistry:
        payload = {
            "version": "test",
            "competitions": [
                {
                    "key": key,
                    "region_id": region_id,
                    "display_name": display_name,
                    "sources": {source.value: identifier for source, identifier in sources.items()},
                }
                for key, region_id, display_name, sources in specs
            ],
        }
        return TrackedCompetitionRegistry.from_json(json.dumps(payload))

    return build


class NotificationFactories:
    """Builders for notification tests, exposed through the `notif` fixture.

    These live on an object rather than as module-level functions because `tests/`
    is not an importable package -- a root conftest fixture is the only way every
    test package can share them.
    """

    NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    KICKOFF = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    USER_ID = 1
    CHAT_ID = 123
    MATCHED_ID = "event-1"
    BUDGET_CENTS = 1_000_000

    def selection(self, outcome: Outcome, source: Source, odds: str) -> StrategySelection:
        return StrategySelection(
            outcome=outcome,
            source=source,
            odds=Decimal(odds),
            stake_cents=self.BUDGET_CENTS // 3,
            cashback_eligible=source in (Source.SOURCE_C, Source.SOURCE_B),
            event_id="12345",
        )

    def opportunity(
        self,
        profit_bps: int,
        *,
        market_type: MarketType = MarketType.FULL_TIME_RESULT,
        kickoff: datetime | None = None,
    ) -> CalculatedOpportunity:
        profit_cents = profit_bps * self.BUDGET_CENTS // 10_000
        return CalculatedOpportunity.model_construct(
            opportunity_id=f"{self.MATCHED_ID}:{market_type.value}:{profit_bps}",
            matched_id=self.MATCHED_ID,
            market_type=market_type,
            market_label="Maç Sonucu 1X2",
            competition="Süper Lig",
            country_id="country:TR",
            country_name="Türkiye",
            country_iso_code="TR",
            league_id="league:1",
            league_name="Süper Lig",
            home="Galatasaray",
            away="Fenerbahçe",
            kickoff=kickoff or self.KICKOFF,
            budget_cents=self.BUDGET_CENTS,
            include_source_a_match_sides=False,
            selections=MappingProxyType(
                {
                    Outcome.HOME: self.selection(Outcome.HOME, Source.SOURCE_A, "1.85"),
                    Outcome.DRAW: self.selection(Outcome.DRAW, Source.SOURCE_C, "3.60"),
                    Outcome.AWAY: self.selection(Outcome.AWAY, Source.SOURCE_B, "4.20"),
                }
            ),
            scenarios=MappingProxyType({}),
            min_adjusted_profit_cents=profit_cents,
            min_adjusted_profit_percent=profit_bps / 100,
            min_standard_profit_cents=profit_cents // 2,
            min_standard_profit_percent=profit_bps / 200,
            is_positive=profit_cents > 0,
            oldest_quote_age_seconds=12,
            calculated_at=self.NOW,
            alternatives=(),
        )

    def evaluation(
        self, profit_bps: int, *, market_type: MarketType = MarketType.FULL_TIME_RESULT
    ) -> DualPolicyEvaluation:
        selected = self.opportunity(profit_bps, market_type=market_type)
        return DualPolicyEvaluation(
            matched_id=self.MATCHED_ID,
            market_type=market_type,
            selected=selected,
            draw_only=selected,
            uses_source_a_match_sides=False,
            relevant_sources=frozenset({Source.SOURCE_A, Source.SOURCE_C, Source.SOURCE_B}),
        )

    def card_state(
        self,
        profit_bps: int = 240,
        status: LifecycleStatus = LifecycleStatus.ACTIVE,
        *,
        market_type: MarketType = MarketType.FULL_TIME_RESULT,
        **overrides,
    ) -> CardState:
        base = {
            "user_id": self.USER_ID,
            "chat_id": self.CHAT_ID,
            "card_key": card_key(self.MATCHED_ID, market_type),
            "matched_id": self.MATCHED_ID,
            "market_type": market_type,
            "generation": 1,
            "status": status,
            "message_id": None,
            "evaluation": self.evaluation(profit_bps, market_type=market_type),
            "first_profit_bps": profit_bps,
            "current_profit_bps": profit_bps,
            "peak_profit_bps": profit_bps,
            "anchor_bps": profit_bps,
            "last_alert_at": None,
            "below_since": None,
            "closed_at": None,
            "revival_count": 0,
            "first_seen_at": self.NOW,
            "last_seen_at": self.NOW,
            "last_edit_at": None,
            "rendered_fingerprint": None,
        }
        base.update(overrides)
        return CardState(**base)


@pytest.fixture
def notif() -> NotificationFactories:
    return NotificationFactories()


@pytest.fixture
def settings(notif: NotificationFactories) -> TelegramNotificationSettings:
    return TelegramNotificationSettings(
        user_id=notif.USER_ID,
        enabled=True,
        chat_id=notif.CHAT_ID,
        minimum_profit_bps=100,
        decrease_threshold_bps=20,
        increase_threshold_bps=20,
        alert_cooldown_seconds=300,
        validated_chat_id=notif.CHAT_ID,
    )


@pytest.fixture
def healthy() -> dict[Source, SourceHealthStatus]:
    return dict.fromkeys(Source, SourceHealthStatus.HEALTHY)
