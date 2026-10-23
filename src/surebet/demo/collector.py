"""Deterministic fake collectors that stand in for the real source adapters.

Each demo source prices every fixture from the same fair probabilities with its
own margin and a small, stable per-outcome wobble, so most matches show the
negative yield a real market has. A handful of fixtures carry an ``Edge`` where
one source overprices an outcome; those are the only ones that clear zero and
appear under Opportunities.
"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from surebet.domain.enums import MarketType, Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind
from surebet.taxonomy.tracked import TrackedCompetitionRegistry

from .fixtures import FIXTURES, LEAGUES, SOURCE_FAMILY, UNTRACKED, DemoFixture

Clock = Callable[[], datetime]
CollectorFunc = Callable[[], Awaitable[CollectionBatch]]

ISTANBUL = ZoneInfo("Europe/Istanbul")

# Source C mirrors Source B's prices and stays off by default, as in production.
DEMO_SOURCES: tuple[Source, ...] = (
    Source.SOURCE_A,
    Source.SOURCE_B,
    Source.SOURCE_D,
    Source.SOURCE_E,
    Source.SOURCE_F,
)

_MARGINS: Mapping[Source, Decimal] = {
    Source.SOURCE_A: Decimal("0.050"),
    # Cashback brands price in their loss refund with a wider margin.
    Source.SOURCE_B: Decimal("0.095"),
    Source.SOURCE_C: Decimal("0.095"),
    Source.SOURCE_D: Decimal("0.055"),
    Source.SOURCE_E: Decimal("0.060"),
    Source.SOURCE_F: Decimal("0.100"),
}

# Leagues a source does not list, so coverage is uneven like it is live.
_NOT_LISTED: Mapping[Source, frozenset[str]] = {
    Source.SOURCE_D: frozenset({"uefa-nations-league", "bask-euroleague"}),
    Source.SOURCE_F: frozenset({"bask-euroleague"}),
}

_UNTRACKED_PROBABILITIES = (Decimal("0.45"), Decimal("0.27"), Decimal("0.28"))
_CENT = Decimal("0.01")
_MIN_ODDS = Decimal("1.01")


def _wobble(*parts: str) -> Decimal:
    """Stable pseudo-random factor in [0.98, 1.02], keyed by the given parts."""

    digest = hashlib.sha256(":".join(parts).encode("utf-8")).digest()
    steps = int.from_bytes(digest[:4], "big") % 41  # 0..40
    return Decimal(1) + (Decimal(steps) - Decimal(20)) / Decimal(1000)


def _price(
    probability: Decimal,
    margin: Decimal,
    wobble: Decimal,
    multiplier: Decimal = Decimal(1),
) -> Decimal:
    odds = Decimal(1) / (probability * (Decimal(1) + margin)) * wobble * multiplier
    return max(odds.quantize(_CENT, rounding=ROUND_HALF_UP), _MIN_ODDS)


def _kickoff(fixture: DemoFixture, now: datetime) -> datetime:
    local_now = now.astimezone(ISTANBUL)
    hour, minute = (int(part) for part in fixture.local_time.split(":"))
    day: date = local_now.date() + timedelta(days=fixture.day)
    kickoff = datetime.combine(day, time(hour, minute), tzinfo=ISTANBUL)
    while kickoff <= local_now + timedelta(minutes=45):
        kickoff += timedelta(days=1)
    return kickoff.astimezone(UTC)


def _event_id(source: Source, key: str) -> str:
    digest = hashlib.sha256(f"{source.value}:{key}".encode()).hexdigest()
    return str(int(digest[:10], 16))


class DemoCollector:
    """Produce one synthetic collection batch for a single source."""

    def __init__(
        self,
        source: Source,
        *,
        registry: TrackedCompetitionRegistry | None = None,
        clock: Clock = lambda: datetime.now(UTC),
        anchor: datetime | None = None,
    ) -> None:
        self._source = Source(source)
        self._registry = registry or TrackedCompetitionRegistry.load_packaged()
        self._clock = clock
        # Kickoffs are laid out once, relative to start-up, so they stay put across cycles.
        self._anchor = anchor or clock()
        self._competition_ids = {
            competition.key: competition.sources[self._source]
            for competition in self._registry.competitions
            if self._source in competition.sources
        }

    async def collect(self) -> CollectionBatch:
        started_at = self._clock()
        events = [
            event for fixture in FIXTURES if (event := self._event(fixture, started_at)) is not None
        ]
        events.extend(self._untracked_events(started_at))
        return CollectionBatch(
            source=self._source,
            started_at=started_at,
            finished_at=self._clock(),
            events=tuple(sorted(events, key=lambda e: (e.kickoff, e.source_event_id))),
        )

    def _evidence(self, league_key: str) -> SourceCompetitionEvidence | None:
        competition_id = self._competition_ids.get(league_key)
        if competition_id is None or league_key in _NOT_LISTED.get(self._source, frozenset()):
            return None
        league = LEAGUES[league_key]
        return SourceCompetitionEvidence(
            provider=self._source.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id=competition_id,
            competition_name=league.names[SOURCE_FAMILY[self._source]],
            country_name=league.country,
        )

    def _quote(
        self,
        event_id: str,
        outcome: Outcome | TwoWayOutcome,
        odds: Decimal,
        observed_at: datetime,
        market_type: MarketType,
    ) -> Quote:
        return Quote(
            source=self._source,
            event_id=event_id,
            outcome=outcome,
            odds=odds,
            observed_at=observed_at,
            market_type=market_type,
        )

    def _prices(
        self,
        fixture: DemoFixture,
        outcomes: tuple[Outcome | TwoWayOutcome, ...],
        probabilities: tuple[Decimal, ...],
    ) -> dict[Outcome | TwoWayOutcome, Decimal]:
        margin = _MARGINS[self._source]
        boosts = {
            edge.outcome: edge.multiplier for edge in fixture.edges if edge.source is self._source
        }
        return {
            outcome: _price(
                probability,
                margin,
                _wobble(self._source.value, fixture.key, outcome.value),
                boosts.get(outcome, Decimal(1)),
            )
            for outcome, probability in zip(outcomes, probabilities, strict=True)
        }

    def _event(self, fixture: DemoFixture, now: datetime) -> CollectedEvent | None:
        evidence = self._evidence(fixture.league)
        if evidence is None:
            return None
        event_id = _event_id(self._source, fixture.key)
        kickoff = _kickoff(fixture, self._anchor)
        if fixture.sport is Sport.BASKETBALL:
            prices = self._prices(fixture, (Outcome.HOME, Outcome.AWAY), fixture.probabilities)
            return CollectedEvent(
                source=self._source,
                source_event_id=event_id,
                competition=evidence.competition_name,
                home=fixture.home,
                away=fixture.away,
                kickoff=kickoff,
                source_competition=evidence,
                market_key="basketball.euroleague",
                sport=Sport.BASKETBALL,
                quotes={},
                bask_ml_quotes={
                    outcome: self._quote(
                        event_id, outcome, odds, now, MarketType.BASKETBALL_MONEYLINE
                    )
                    for outcome, odds in prices.items()
                    if isinstance(outcome, Outcome)
                },
            )

        prices = self._prices(fixture, tuple(Outcome), fixture.probabilities)
        quotes = {
            outcome: self._quote(event_id, outcome, odds, now, MarketType.FULL_TIME_RESULT)
            for outcome, odds in prices.items()
            if isinstance(outcome, Outcome)
        }
        ou25: dict[TwoWayOutcome, Quote] | None = None
        if fixture.over_probability is not None:
            over = fixture.over_probability
            two_way = self._prices(
                fixture, (TwoWayOutcome.OVER, TwoWayOutcome.UNDER), (over, Decimal(1) - over)
            )
            ou25 = {
                outcome: self._quote(event_id, outcome, odds, now, MarketType.TOTAL_GOALS_25)
                for outcome, odds in two_way.items()
                if isinstance(outcome, TwoWayOutcome)
            }
        return CollectedEvent(
            source=self._source,
            source_event_id=event_id,
            competition=evidence.competition_name,
            home=fixture.home,
            away=fixture.away,
            kickoff=kickoff,
            source_competition=evidence,
            quotes=quotes,
            ou25_quotes=ou25,
        )

    def _untracked_events(self, now: datetime) -> list[CollectedEvent]:
        events: list[CollectedEvent] = []
        margin = _MARGINS[self._source]
        for competition in UNTRACKED:
            if competition.source is not self._source:
                continue
            event_id = _event_id(self._source, f"untracked:{competition.competition_id}")
            evidence = SourceCompetitionEvidence(
                provider=self._source.value,
                identity_kind=(
                    SourceIdentityKind.DERIVED_TEXT
                    if competition.derived_from_text
                    else SourceIdentityKind.PROVIDER_ID
                ),
                competition_id=competition.competition_id,
                competition_name=competition.name,
                country_name=competition.country,
                kind=competition.kind,
            )
            quotes = {
                outcome: self._quote(
                    event_id,
                    outcome,
                    _price(
                        probability,
                        margin,
                        _wobble(self._source.value, competition.name, outcome.value),
                    ),
                    now,
                    MarketType.FULL_TIME_RESULT,
                )
                for outcome, probability in zip(
                    tuple(Outcome), _UNTRACKED_PROBABILITIES, strict=True
                )
            }
            events.append(
                CollectedEvent(
                    source=self._source,
                    source_event_id=event_id,
                    competition=competition.name,
                    home=competition.home,
                    away=competition.away,
                    kickoff=self._anchor + timedelta(days=1, hours=3),
                    quotes=quotes,
                    source_competition=evidence,
                )
            )
        return events


def demo_collector_funcs(
    *,
    registry: TrackedCompetitionRegistry | None = None,
    clock: Clock = lambda: datetime.now(UTC),
) -> dict[Source, CollectorFunc]:
    """One collector per demo source, sharing a single kickoff anchor."""

    anchor = clock()
    return {
        source: DemoCollector(source, registry=registry, clock=clock, anchor=anchor).collect
        for source in DEMO_SOURCES
    }
