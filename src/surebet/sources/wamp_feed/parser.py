"""Pure normalization of sanitized WAMP feed record envelopes."""

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from types import MappingProxyType
from typing import Final

from surebet.domain.enums import MarketType, Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind

from .models import WampFeedEnvelope, WampFeedV2Record
from .protocol import WampFeedProtocolError

_RECORD_TYPES: Final = frozenset({"tournament", "match", "market", "bettingOffer", "outcome"})
_OUTCOMES: Final = MappingProxyType({"1": Outcome.HOME, "x": Outcome.DRAW, "2": Outcome.AWAY})
_OPEN_STATUSES: Final = frozenset({"open", "prematch"})
_LIVE_ODDS_QUANTUM: Final = Decimal("0.0001")

Record = Mapping[str, object]

_V2_ENTITY_TYPES: Final = MappingProxyType(
    {
        "tournament": "tournament",
        "Tournament": "tournament",
        "TOURNAMENT": "tournament",
        "match": "match",
        "Match": "match",
        "MATCH": "match",
        "market": "market",
        "Market": "market",
        "MARKET": "market",
        "bettingOffer": "bettingOffer",
        "BettingOffer": "bettingOffer",
        "BETTINGOFFER": "bettingOffer",
        "outcome": "outcome",
        "Outcome": "outcome",
        "OUTCOME": "outcome",
    }
)
_V2_RELATION_ALIASES: Final = MappingProxyType(
    {
        "tournamentId": "tournamentID",
        "matchId": "matchID",
        "marketId": "marketID",
        "bettingOfferId": "bettingOfferID",
    }
)
_V2_REQUIRED_FIELDS: Final = MappingProxyType(
    {
        "tournament": frozenset(("id", "sport", "name")),
        "match": frozenset(("id", "tournamentId", "sport", "home", "away", "kickoff", "status")),
        "market": frozenset(("id", "matchId", "name", "status")),
        "bettingOffer": frozenset(("id", "marketId", "status")),
        "outcome": frozenset(("id", "bettingOfferId", "label", "odds", "status")),
    }
)


def parse_envelope(payload: object) -> WampFeedEnvelope:
    """Validate a four-field WAMP feed result envelope without exposing its values."""

    if not isinstance(payload, Mapping):
        raise WampFeedProtocolError("result envelope must be a mapping")
    try:
        envelope = WampFeedEnvelope.from_payload(payload)
    except (TypeError, ValueError):
        raise WampFeedProtocolError("invalid result envelope") from None
    if any(
        not value.strip() for value in (envelope.format, envelope.message_type, envelope.version)
    ):
        raise WampFeedProtocolError("result envelope fields must be non-empty strings")
    return envelope


def parse_records(
    envelopes: Iterable[WampFeedEnvelope], *, source: Source, observed_at: datetime
) -> tuple[CollectedEvent, ...]:
    """Join complete future football Full Time Result graphs into canonical events."""

    observed_utc = _observed_utc(observed_at)
    records_by_type = _group_records(envelopes)
    return _parse_grouped_records(records_by_type, source=source, observed_utc=observed_utc)


def parse_v2_records(
    records: Iterable[WampFeedV2Record], *, source: Source, observed_at: datetime
) -> tuple[CollectedEvent, ...]:
    """Adapt sanitized WAMP feed v2 state records to canonical events.

    Only the documented entity-type spellings and explicit relation-ID aliases
    are accepted. Unknown entities and records missing required fields are
    ignored so one incomplete graph cannot hide otherwise valid events.
    """

    observed_utc = _observed_utc(observed_at)
    records_by_type = _group_canonical_records(_normalize_v2_records(records))
    return _parse_grouped_records(records_by_type, source=source, observed_utc=observed_utc)


def parse_live_records(
    records: Iterable[WampFeedV2Record], *, source: Source, observed_at: datetime
) -> tuple[CollectedEvent, ...]:
    """Normalize the current WAMP feed aggregator record graph."""

    observed_utc = _observed_utc(observed_at)
    grouped: dict[str, list[Record]] = defaultdict(list)
    try:
        iterator = iter(records)
    except TypeError:
        raise WampFeedProtocolError("live records must be iterable") from None
    for candidate in iterator:
        if not isinstance(candidate, Mapping):
            raise WampFeedProtocolError("live records must contain mappings")
        record_type = candidate.get("_type")
        if not isinstance(record_type, str) or not record_type.strip():
            continue
        grouped[record_type.strip().upper()].append(dict(candidate))

    matches = {
        record_id: record
        for record in grouped.get("MATCH", ())
        if (record_id := _live_id(record.get("id"))) is not None
    }
    markets_by_match: defaultdict[str, list[Record]] = defaultdict(list)
    ou25_markets_by_match: defaultdict[str, list[Record]] = defaultdict(list)
    bask_ml_markets_by_match: defaultdict[str, list[Record]] = defaultdict(list)
    bask_ah_markets_by_match: defaultdict[str, list[Record]] = defaultdict(list)
    for market in grouped.get("MARKET", ()):
        event_id = _live_id(market.get("eventId"))
        name = market.get("name")
        if (
            event_id is not None
            and isinstance(name, str)
            and not _is_early_settlement_market(market)
            and market.get("isAvailable") is not False
            and market.get("isClosed") is not True
        ):
            if _is_live_ftr_market(name):
                markets_by_match[event_id].append(market)
            elif _is_live_ou25_market(market):
                ou25_markets_by_match[event_id].append(market)
            elif _is_live_bask_ml_market(name):
                bask_ml_markets_by_match[event_id].append(market)
            elif _is_live_bask_ah_market(name):
                bask_ah_markets_by_match[event_id].append(market)

    outcomes = {
        outcome_id: record
        for record in grouped.get("OUTCOME", ())
        if (outcome_id := _live_id(record.get("id"))) is not None
    }
    outcome_ids_by_market: defaultdict[str, list[str]] = defaultdict(list)
    for relation in grouped.get("MARKET_OUTCOME_RELATION", ()):
        market_id = _live_id(relation.get("marketId"))
        outcome_id = _live_id(relation.get("outcomeId"))
        if market_id is not None and outcome_id is not None:
            outcome_ids_by_market[market_id].append(outcome_id)

    offers_by_outcome: defaultdict[str, list[Record]] = defaultdict(list)
    for offer in grouped.get("BETTING_OFFER", ()):
        outcome_id = _live_id(offer.get("outcomeId"))
        if outcome_id is None or offer.get("isAvailable") is False or offer.get("isLive") is True:
            continue
        if _decimal_odds(offer) is not None:
            offers_by_outcome[outcome_id].append(offer)

    tournaments = {
        tournament_id: record
        for record in grouped.get("TOURNAMENT", ())
        if (tournament_id := _live_id(record.get("id"))) is not None
    }

    collected: list[CollectedEvent] = []
    for event_id, match in matches.items():
        if _is_live_basketball(match):
            event = _parse_live_basketball_match(
                event_id,
                match,
                bask_ml_markets_by_match.get(event_id, ()),
                bask_ah_markets_by_match.get(event_id, ()),
                outcome_ids_by_market,
                outcomes,
                offers_by_outcome,
                source,
                observed_utc,
                tournaments,
            )
        else:
            event = _parse_live_match(
                event_id,
                match,
                markets_by_match.get(event_id, ()),
                ou25_markets_by_match.get(event_id, ()),
                outcome_ids_by_market,
                outcomes,
                offers_by_outcome,
                source,
                observed_utc,
                tournaments,
            )
        if event is not None:
            collected.append(event)
    return tuple(sorted(collected, key=lambda item: (item.kickoff, item.source_event_id)))


def _parse_live_match(
    event_id: str,
    match: Record,
    markets: Iterable[Record],
    ou25_markets: Iterable[Record],
    outcome_ids_by_market: Mapping[str, list[str]],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    source: Source,
    observed_utc: datetime,
    tournaments: Mapping[str, Record] | None = None,
) -> CollectedEvent | None:
    if not _is_live_football(match) or _is_live_status_closed(match.get("statusName")):
        return None
    kickoff = _live_kickoff(match.get("startTime"))
    if kickoff is None or kickoff <= observed_utc:
        return None
    home_id = _live_id(match.get("homeParticipantId"))
    away_id = _live_id(match.get("awayParticipantId"))
    home = _live_text(match.get("homeParticipantName"))
    away = _live_text(match.get("awayParticipantName"))
    parent_id = _live_id(match.get("parentId"))
    parent_name = _live_text(match.get("parentName"))
    category_name = _live_text(match.get("categoryName"))

    # Resolve parent tournament if match belongs to a sub-tournament / group
    if parent_id is not None and tournaments and parent_id in tournaments:
        t_record = tournaments[parent_id]
        t_parent_id = _live_id(t_record.get("parentId"))
        t_parent_name = _live_text(t_record.get("parentName"))
        if t_parent_id is not None:
            parent_id = t_parent_id
        if t_parent_name is not None:
            parent_name = t_parent_name

    competition = parent_name or category_name
    if home_id is None or away_id is None or home is None or away is None or competition is None:
        return None

    candidates: list[Mapping[Outcome, Quote]] = []
    for market in markets:
        market_id = _live_id(market.get("id"))
        if market_id is None:
            continue
        quotes = _live_market_quotes(
            outcome_ids_by_market.get(market_id, ()),
            outcomes,
            offers_by_outcome,
            home_id,
            away_id,
            event_id,
            source,
            observed_utc,
        )
        if quotes is not None:
            candidates.append(quotes)
    if len(candidates) != 1:
        return None

    ou25_candidates: list[Mapping[TwoWayOutcome, Quote]] = []
    for market in ou25_markets:
        market_id = _live_id(market.get("id"))
        if market_id is None:
            continue
        ou_quotes = _live_ou25_market_quotes(
            outcome_ids_by_market.get(market_id, ()),
            outcomes,
            offers_by_outcome,
            event_id,
            source,
            observed_utc,
        )
        if ou_quotes is not None:
            ou25_candidates.append(ou_quotes)
    ou25_quotes = _resolve_2way_candidates(
        ou25_candidates, {TwoWayOutcome.OVER, TwoWayOutcome.UNDER}
    )

    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition=competition,
        home=home,
        away=away,
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        quotes=candidates[0],
        ou25_quotes=ou25_quotes,
        source_competition=_live_competition_evidence(
            source,
            match,
            competition,
            resolved_parent_id=parent_id,
            resolved_parent_name=parent_name,
        ),
    )


def _parse_live_basketball_match(
    event_id: str,
    match: Record,
    ml_markets: Iterable[Record],
    ah_markets: Iterable[Record],
    outcome_ids_by_market: Mapping[str, list[str]],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    source: Source,
    observed_utc: datetime,
    tournaments: Mapping[str, Record] | None = None,
) -> CollectedEvent | None:
    if _is_live_status_closed(match.get("statusName")):
        return None
    kickoff = _live_kickoff(match.get("startTime"))
    if kickoff is None or kickoff <= observed_utc:
        return None
    home_id = _live_id(match.get("homeParticipantId"))
    away_id = _live_id(match.get("awayParticipantId"))
    home = _live_text(match.get("homeParticipantName"))
    away = _live_text(match.get("awayParticipantName"))
    parent_id = _live_id(match.get("parentId"))
    parent_name = _live_text(match.get("parentName"))
    category_name = _live_text(match.get("categoryName"))

    if parent_id is not None and tournaments and parent_id in tournaments:
        t_record = tournaments[parent_id]
        t_parent_id = _live_id(t_record.get("parentId"))
        t_parent_name = _live_text(t_record.get("parentName"))
        if t_parent_id is not None:
            parent_id = t_parent_id
        if t_parent_name is not None:
            parent_name = t_parent_name

    competition = parent_name or category_name
    if home_id is None or away_id is None or home is None or away is None or competition is None:
        return None

    # Moneyline quotes
    bask_ml: Mapping[Outcome, Quote] | None = None
    for market in ml_markets:
        market_id = _live_id(market.get("id"))
        if market_id is None:
            continue
        quotes = _live_bask_ml_quotes(
            outcome_ids_by_market.get(market_id, ()),
            outcomes,
            offers_by_outcome,
            home_id,
            away_id,
            event_id,
            source,
            observed_utc,
        )
        if quotes is not None:
            bask_ml = quotes
            break

    # Asian Handicap quotes
    bask_ah_quotes: dict[tuple[Outcome, Decimal], Quote] = {}
    for market in ah_markets:
        market_id = _live_id(market.get("id"))
        name = market.get("name")
        if market_id is None or not isinstance(name, str):
            continue
        spread = _extract_handicap_spread(name)
        if spread is None:
            continue
        ah_quotes = _live_bask_ah_quotes(
            outcome_ids_by_market.get(market_id, ()),
            outcomes,
            offers_by_outcome,
            home_id,
            away_id,
            event_id,
            spread,
            source,
            observed_utc,
        )
        if ah_quotes:
            bask_ah_quotes.update(ah_quotes)

    if not bask_ml and not bask_ah_quotes:
        return None

    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition=competition,
        home=home,
        away=away,
        kickoff=kickoff,
        sport=Sport.BASKETBALL,
        market_key="basketball.euroleague",
        quotes={},
        bask_ml_quotes=bask_ml,
        bask_ah_quotes=bask_ah_quotes or None,
        source_competition=_live_competition_evidence(
            source,
            match,
            competition,
            resolved_parent_id=parent_id,
            resolved_parent_name=parent_name,
        ),
    )


def _live_bask_ml_quotes(
    outcome_ids: Iterable[str],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    home_id: str,
    away_id: str,
    event_id: str,
    source: Source,
    observed_utc: datetime,
) -> Mapping[Outcome, Quote] | None:
    quotes: dict[Outcome, Quote] = {}
    for outcome_id in outcome_ids:
        outcome_record = outcomes.get(outcome_id)
        if outcome_record is None:
            return None
        outcome = _live_bask_outcome(outcome_record, home_id, away_id)
        offers = offers_by_outcome.get(outcome_id, ())
        if outcome is None or not offers or outcome in quotes:
            return None
        raw_odds = _decimal_odds(offers[0])
        if raw_odds is None:
            return None
        if any(_decimal_odds(offer) != raw_odds for offer in offers[1:]):
            return None
        odds = _normalize_live_odds(raw_odds)
        try:
            quotes[outcome] = Quote(
                source=source,
                event_id=event_id,
                outcome=outcome,
                odds=odds,
                observed_at=observed_utc,
                market_type=MarketType.BASKETBALL_MONEYLINE,
            )
        except ValueError:
            return None
    return quotes if set(quotes) == {Outcome.HOME, Outcome.AWAY} else None


def _live_bask_ah_quotes(
    outcome_ids: Iterable[str],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    home_id: str,
    away_id: str,
    event_id: str,
    spread: Decimal,
    source: Source,
    observed_utc: datetime,
) -> dict[tuple[Outcome, Decimal], Quote] | None:
    quotes: dict[tuple[Outcome, Decimal], Quote] = {}
    home_line = spread
    away_line = -spread

    for outcome_id in outcome_ids:
        outcome_record = outcomes.get(outcome_id)
        if outcome_record is None:
            return None
        outcome = _live_bask_outcome(outcome_record, home_id, away_id)
        if outcome is None:
            continue
        line = home_line if outcome == Outcome.HOME else away_line
        offers = offers_by_outcome.get(outcome_id, ())
        if not offers or (outcome, line) in quotes:
            return None
        raw_odds = _decimal_odds(offers[0])
        if raw_odds is None:
            return None
        if any(_decimal_odds(offer) != raw_odds for offer in offers[1:]):
            return None
        odds = _normalize_live_odds(raw_odds)
        try:
            quotes[(outcome, line)] = Quote(
                source=source,
                event_id=event_id,
                outcome=outcome,
                odds=odds,
                observed_at=observed_utc,
                market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
                line=line,
            )
        except ValueError:
            return None

    has_home = any(k[0] == Outcome.HOME for k in quotes)
    has_away = any(k[0] == Outcome.AWAY for k in quotes)
    return quotes if (has_home and has_away) else None


def _live_bask_outcome(record: Record, home_id: str, away_id: str) -> Outcome | None:
    participant_id = _live_id(record.get("paramParticipantId1"))
    if participant_id == home_id:
        return Outcome.HOME
    if participant_id == away_id:
        return Outcome.AWAY
    header = record.get("headerName")
    if isinstance(header, str):
        h = _live_label(header)
        if h in {"ev sahibi", "home", "1"}:
            return Outcome.HOME
        if h in {"deplasman", "away", "2"}:
            return Outcome.AWAY
    name = record.get("translatedName") or record.get("name")
    if isinstance(name, str):
        n = _live_label(name)
        if "1" in n or "ev sahibi" in n:
            return Outcome.HOME
        if "2" in n or "deplasman" in n:
            return Outcome.AWAY
    return None


def _live_market_quotes(
    outcome_ids: Iterable[str],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    home_id: str,
    away_id: str,
    event_id: str,
    source: Source,
    observed_utc: datetime,
) -> Mapping[Outcome, Quote] | None:
    quotes: dict[Outcome, Quote] = {}
    for outcome_id in outcome_ids:
        outcome_record = outcomes.get(outcome_id)
        if outcome_record is None:
            return None
        outcome = _live_outcome(outcome_record, home_id, away_id)
        offers = offers_by_outcome.get(outcome_id, ())
        if outcome is None or not offers or outcome in quotes:
            return None
        raw_odds = _decimal_odds(offers[0])
        if raw_odds is None:
            return None
        # Merging the prematch topics can leave one outcome holding two offers. Identical
        # prices are unambiguous; disagreeing ones are not, and picking whichever reduced
        # first would publish a price the operator never saw as authoritative.
        if any(_decimal_odds(offer) != raw_odds for offer in offers[1:]):
            return None
        odds = _normalize_live_odds(raw_odds)
        try:
            quotes[outcome] = Quote(
                source=source,
                event_id=event_id,
                outcome=outcome,
                odds=odds,
                observed_at=observed_utc,
            )
        except ValueError:
            return None
    return quotes if set(quotes) == set(Outcome) else None


def _resolve_2way_candidates(
    candidates: list[Mapping[TwoWayOutcome, Quote]],
    expected_keys: set[TwoWayOutcome],
) -> Mapping[TwoWayOutcome, Quote] | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    for other in candidates[1:]:
        for k in expected_keys:
            if other.get(k) is None or other[k].odds != first[k].odds:
                return None
    return first


def _live_ou25_market_quotes(
    outcome_ids: Iterable[str],
    outcomes: Mapping[str, Record],
    offers_by_outcome: Mapping[str, list[Record]],
    event_id: str,
    source: Source,
    observed_utc: datetime,
) -> Mapping[TwoWayOutcome, Quote] | None:
    quotes: dict[TwoWayOutcome, Quote] = {}
    for outcome_id in outcome_ids:
        outcome_record = outcomes.get(outcome_id)
        if outcome_record is None:
            return None
        outcome = _live_ou25_outcome(outcome_record)
        offers = offers_by_outcome.get(outcome_id, ())
        if outcome is None or not offers or outcome in quotes:
            return None
        raw_odds = _decimal_odds(offers[0])
        if raw_odds is None:
            return None
        if any(_decimal_odds(offer) != raw_odds for offer in offers[1:]):
            return None
        odds = _normalize_live_odds(raw_odds)
        try:
            quotes[outcome] = Quote(
                source=source,
                event_id=event_id,
                outcome=outcome,
                odds=odds,
                observed_at=observed_utc,
                market_type=MarketType.TOTAL_GOALS_25,
            )
        except ValueError:
            return None
    return quotes if set(quotes) == {TwoWayOutcome.OVER, TwoWayOutcome.UNDER} else None


def _normalize_live_odds(odds: Decimal) -> Decimal:
    """Fit reciprocal-style provider odds into the domain precision conservatively."""

    exponent = odds.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -4:
        return odds.quantize(_LIVE_ODDS_QUANTUM, rounding=ROUND_DOWN)
    return odds


def _parse_grouped_records(
    records_by_type: Mapping[str, list[Record]], *, source: Source, observed_utc: datetime
) -> tuple[CollectedEvent, ...]:
    tournaments = _index_by_id(records_by_type["tournament"], "tournament")
    matches = _index_by_id(records_by_type["match"], "match")
    markets_by_match = _index_by_relation(records_by_type["market"], "market", "matchId")
    offers_by_market = _index_by_relation(
        records_by_type["bettingOffer"], "bettingOffer", "marketId"
    )
    outcomes_by_offer = _index_by_relation(records_by_type["outcome"], "outcome", "bettingOfferId")

    collected: list[CollectedEvent] = []
    for match_id, match in matches.items():
        event = _parse_match(
            match_id,
            match,
            tournaments,
            markets_by_match,
            offers_by_market,
            outcomes_by_offer,
            source,
            observed_utc,
        )
        if event is not None:
            collected.append(event)
    return tuple(sorted(collected, key=lambda item: (item.kickoff, item.source_event_id)))


def _normalize_v2_records(records: Iterable[WampFeedV2Record]) -> tuple[Record, ...]:
    try:
        iterator = iter(records)
    except TypeError:
        raise WampFeedProtocolError("v2 records must be iterable") from None

    normalized: list[Record] = []
    for record in iterator:
        if not isinstance(record, Mapping):
            raise WampFeedProtocolError("v2 records must contain mappings")
        entity_type = record.get("entityType")
        if not isinstance(entity_type, str):
            continue
        canonical_type = _V2_ENTITY_TYPES.get(entity_type)
        if canonical_type is None:
            continue
        canonical = dict(record)
        canonical["_type"] = canonical_type
        for relation, alias in _V2_RELATION_ALIASES.items():
            if (
                relation in canonical
                and alias in canonical
                and canonical[relation] != canonical[alias]
            ):
                raise WampFeedProtocolError("ambiguous v2 record relation")
            if relation not in canonical and alias in canonical:
                canonical[relation] = canonical[alias]
        if _is_valid_v2_record(canonical):
            normalized.append(canonical)
    return _without_duplicate_v2_ids(normalized)


def _is_valid_v2_record(record: Record) -> bool:
    record_type = record.get("_type")
    if not isinstance(record_type, str):
        return False
    required = _V2_REQUIRED_FIELDS.get(record_type)
    if required is None or not required.issubset(record) or not _is_v2_id(record.get("id")):
        return False
    if record_type == "tournament":
        return _is_v2_text(record.get("sport")) and _is_v2_text(record.get("name"))
    if record_type == "match":
        return (
            _is_v2_id(record.get("tournamentId"))
            and all(_is_v2_text(record.get(field)) for field in ("sport", "home", "away", "status"))
            and _is_v2_kickoff(record.get("kickoff"))
        )
    if record_type == "market":
        return (
            _is_v2_id(record.get("matchId"))
            and _is_v2_text(record.get("name"))
            and _is_v2_text(record.get("status"))
        )
    if record_type == "bettingOffer":
        return _is_v2_id(record.get("marketId")) and _is_v2_text(record.get("status"))
    if record_type == "outcome":
        return (
            _is_v2_id(record.get("bettingOfferId"))
            and _is_v2_text(record.get("label"))
            and _is_v2_text(record.get("status"))
            and _decimal_odds(record) is not None
        )
    return False


def _without_duplicate_v2_ids(records: Iterable[Record]) -> tuple[Record, ...]:
    seen: dict[str, set[str]] = defaultdict(set)
    unique: list[Record] = []
    for record in records:
        record_type = record["_type"]
        if not isinstance(record_type, str):
            continue
        record_id = _required_id(record, record_type, "id")
        if record_id in seen[record_type]:
            continue
        seen[record_type].add(record_id)
        unique.append(record)
    return tuple(unique)


def _is_v2_id(value: object) -> bool:
    return (
        not isinstance(value, bool) and isinstance(value, (str, int)) and bool(str(value).strip())
    )


def _is_v2_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_v2_kickoff(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        kickoff = datetime.fromisoformat(value)
    except ValueError:
        return False
    return kickoff.tzinfo is not None and kickoff.utcoffset() is not None


def _group_canonical_records(records: Iterable[Record]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = {record_type: [] for record_type in _RECORD_TYPES}
    for record in records:
        record_type = record["_type"]
        if record_type in grouped:
            grouped[record_type].append(record)
    return grouped


def _group_records(envelopes: Iterable[WampFeedEnvelope]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = {record_type: [] for record_type in _RECORD_TYPES}
    for envelope in envelopes:
        for record in envelope.records:
            record_type = _required_text(record, "record", "_type")
            if record_type in grouped:
                grouped[record_type].append(record)
    return grouped


def _index_by_id(records: Iterable[Record], kind: str) -> dict[str, Record]:
    indexed: dict[str, Record] = {}
    for record in records:
        record_id = _required_id(record, kind, "id")
        if record_id in indexed:
            raise WampFeedProtocolError(f"duplicate {kind} ID")
        indexed[record_id] = record
    return indexed


def _index_by_relation(
    records: Iterable[Record], kind: str, relation_field: str
) -> dict[str, tuple[Record, ...]]:
    grouped: defaultdict[str, list[Record]] = defaultdict(list)
    record_ids: set[str] = set()
    for record in records:
        record_id = _required_id(record, kind, "id")
        if record_id in record_ids:
            raise WampFeedProtocolError(f"duplicate {kind} ID")
        record_ids.add(record_id)
        relation_id = _required_id(record, kind, relation_field)
        grouped[relation_id].append(record)
    return {relation_id: tuple(items) for relation_id, items in grouped.items()}


def _parse_match(
    match_id: str,
    match: Record,
    tournaments: Mapping[str, Record],
    markets_by_match: Mapping[str, tuple[Record, ...]],
    offers_by_market: Mapping[str, tuple[Record, ...]],
    outcomes_by_offer: Mapping[str, tuple[Record, ...]],
    source: Source,
    observed_utc: datetime,
) -> CollectedEvent | None:
    tournament_id = _required_id(match, "match", "tournamentId")
    tournament = tournaments.get(tournament_id)
    if tournament is None:
        return None
    if _required_text(match, "match", "sport").casefold() != "football":
        return None
    if _required_text(tournament, "tournament", "sport").casefold() != "football":
        return None
    if _required_text(match, "match", "status").casefold() != "prematch":
        return None

    kickoff = _kickoff(match)
    if kickoff is None or kickoff <= observed_utc:
        return None
    competition = _required_text(tournament, "tournament", "name")
    home = _required_text(match, "match", "home")
    away = _required_text(match, "match", "away")

    candidates: list[Mapping[Outcome, Quote]] = []
    for market in markets_by_match.get(match_id, ()):
        quotes = _market_quotes(
            market, match_id, source, observed_utc, offers_by_market, outcomes_by_offer
        )
        if quotes is not None:
            candidates.append(quotes)
    if len(candidates) != 1:
        return None
    return CollectedEvent(
        source=source,
        source_event_id=match_id,
        competition=competition,
        home=home,
        away=away,
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        quotes=candidates[0],
        source_competition=_text_derived_competition(source, competition),
    )


def _market_quotes(
    market: Record,
    match_id: str,
    source: Source,
    observed_at: datetime,
    offers_by_market: Mapping[str, tuple[Record, ...]],
    outcomes_by_offer: Mapping[str, tuple[Record, ...]],
) -> Mapping[Outcome, Quote] | None:
    if _normalized_label(_required_text(market, "market", "name")) != "full time result":
        return None
    if _is_early_settlement_market(market):
        return None
    if _required_text(market, "market", "status").casefold() not in _OPEN_STATUSES:
        return None
    market_id = _required_id(market, "market", "id")

    candidates: list[Mapping[Outcome, Quote]] = []
    for offer in offers_by_market.get(market_id, ()):
        if _required_text(offer, "bettingOffer", "status").casefold() not in _OPEN_STATUSES:
            continue
        offer_id = _required_id(offer, "bettingOffer", "id")
        quotes = _outcome_quotes(outcomes_by_offer.get(offer_id, ()), match_id, source, observed_at)
        if quotes is not None:
            candidates.append(quotes)
    return candidates[0] if len(candidates) == 1 else None


def _outcome_quotes(
    outcomes: Iterable[Record], match_id: str, source: Source, observed_at: datetime
) -> Mapping[Outcome, Quote] | None:
    quotes: dict[Outcome, Quote] = {}
    for outcome_record in outcomes:
        _required_id(outcome_record, "outcome", "id")
        if _required_text(outcome_record, "outcome", "status").casefold() not in _OPEN_STATUSES:
            return None
        outcome = _OUTCOMES.get(_required_text(outcome_record, "outcome", "label").casefold())
        odds = _decimal_odds(outcome_record)
        if outcome is None or odds is None or outcome in quotes:
            return None
        quotes[outcome] = Quote(
            source=source,
            event_id=match_id,
            outcome=outcome,
            odds=odds,
            observed_at=observed_at,
        )
    return quotes if set(quotes) == set(Outcome) else None


def _required_text(record: Record, kind: str, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise WampFeedProtocolError(f"{kind}.{field} must be a non-empty string")
    return value.strip()


def _required_id(record: Record, kind: str, field: str) -> str:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise WampFeedProtocolError(f"{kind}.{field} must be a non-empty ID")
    identifier = str(value).strip()
    if not identifier:
        raise WampFeedProtocolError(f"{kind}.{field} must be a non-empty ID")
    return identifier


def _live_outcome(record: Record, home_id: str, away_id: str) -> Outcome | None:
    code = record.get("code")
    if isinstance(code, str) and code.casefold() == "#d":
        return Outcome.DRAW
    participant_id = _live_id(record.get("paramParticipantId1"))
    if participant_id == home_id:
        return Outcome.HOME
    if participant_id == away_id:
        return Outcome.AWAY
    label = record.get("translatedName")
    if isinstance(label, str) and _live_label(label) in {"draw", "berabere", "x"}:
        return Outcome.DRAW
    return None


def _is_live_ftr_market(value: str) -> bool:
    return _live_label(value) in {
        "full time result",
        "match result",
        "1x2",
        "kim kazanir normal sure",
        "mac sonucu normal sure",
    }


def _is_live_ou25_market(market: Record) -> bool:
    name = market.get("name")
    if not isinstance(name, str) or "2.5" not in name:
        return False
    name_label = _live_label(name)
    excluded = (
        "handikap",
        "handicap",
        "asya",
        "asian",
        "1 yari",
        "2 yari",
        "1st half",
        "2nd half",
        "ilk yari",
        "ikinci yari",
        "iy",
        "korner",
        "corner",
        "kart",
        "card",
        "ev sahibi",
        "deplasman",
        "home",
        "away",
    )
    if any(ex in name_label for ex in excluded):
        return False
    betting_type_id = market.get("bettingTypeId")
    b_type_str = str(betting_type_id) if isinstance(betting_type_id, (str, int)) else ""
    if b_type_str == "47":
        return True
    return any(
        tok in name_label
        for tok in ("toplam", "total", "alt", "ust", "over", "under", "gol", "goal")
    )


def _live_ou25_outcome(record: Record) -> TwoWayOutcome | None:
    code = record.get("code")
    if isinstance(code, str):
        c = code.upper()
        if "#O" in c or c.endswith("OVER"):
            return TwoWayOutcome.OVER
        if "#U" in c or c.endswith("UNDER"):
            return TwoWayOutcome.UNDER
    header_name = record.get("headerName")
    if isinstance(header_name, str):
        h = _live_label(header_name)
        if h in {"ust", "over", "ustu", "+2.5"}:
            return TwoWayOutcome.OVER
        if h in {"alt", "under", "alti", "-2.5"}:
            return TwoWayOutcome.UNDER
    type_name = record.get("typeName")
    if isinstance(type_name, str):
        t = _live_label(type_name)
        if t in {"over", "ust", "ustu"}:
            return TwoWayOutcome.OVER
        if t in {"under", "alt", "alti"}:
            return TwoWayOutcome.UNDER
    translated_name = record.get("translatedName")
    if isinstance(translated_name, str):
        tr = _live_label(translated_name)
        if tr.startswith(("ust", "over")):
            return TwoWayOutcome.OVER
        if tr.startswith(("alt", "under")):
            return TwoWayOutcome.UNDER
    return None


_EARLY_SETTLEMENT_BETTING_TYPE_ID = "1510"


def _is_early_settlement_market(market: Record) -> bool:
    """True for a market whose price settles early on a decisive scoreline.

    Some operators (observed on Source E) publish this as a second market with the
    identical display name as the standard Full Time Result market, distinguished only
    by bettingTypeId. Source C/Source B markets never carry this field, so this check is a
    no-op for them.
    """
    betting_type_id = market.get("bettingTypeId")
    return (
        isinstance(betting_type_id, (str, int))
        and str(betting_type_id) == _EARLY_SETTLEMENT_BETTING_TYPE_ID
    )


def _is_live_football(record: Record) -> bool:
    sport_id = _live_id(record.get("sportId"))
    if sport_id == "1":
        return True
    sport_name = record.get("sportName")
    return isinstance(sport_name, str) and _live_label(sport_name) in {
        "football",
        "soccer",
        "futbol",
    }


def _is_live_basketball(record: Record) -> bool:
    sport_id = _live_id(record.get("sportId"))
    if sport_id == "8":
        return True
    sport_name = record.get("sportName")
    return isinstance(sport_name, str) and _live_label(sport_name) in {
        "basketball",
        "basketbol",
    }


def _is_live_bask_ml_market(name: str) -> bool:
    label = _live_label(name)
    if "handikap" in label or "handicap" in label:
        return False
    return (
        label
        in {
            "kim kazanir tum mac",
            "kim kazanir uzatmalar dahil",
            "winner incl overtime",
            "match winner incl overtime",
            "match winner incl ot",
            "kazanan uzatmalar dahil",
            "kim kazanir",
            "mac kazanani",
        }
        or ("kim kazanir" in label and "tum mac" in label)
        or ("winner" in label and ("overtime" in label or "ot" in label))
    )


def _is_live_bask_ah_market(name: str) -> bool:
    label = _live_label(name)
    is_ah_keyword = (
        "asya handikap" in label
        or "asian handicap" in label
        or ("handikap" in label and "tum mac" in label)
        or ("handicap" in label and "incl" in label)
    )
    return is_ah_keyword and _extract_handicap_spread(name) is not None


def _extract_handicap_spread(name: str) -> Decimal | None:
    m = re.search(
        r"(?:asya handikap[ıi]?|handicap|handikap)\s*([+-]?\d+(?:\.\d+)?)",
        name,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except (InvalidOperation, ValueError):
        return None


def _is_live_status_closed(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return _live_label(value) in {
        "live",
        "canli",
        "started",
        "in play",
        "suspended",
        "closed",
        "finished",
        "ended",
        "cancelled",
        "canceled",
    }


def _live_kickoff(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _live_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _live_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _live_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    without_punctuation = "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in without_marks
    )
    return " ".join(without_punctuation.replace("ı", "i").split())


def _observed_utc(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return observed_at.astimezone(UTC)


def _kickoff(match: Record) -> datetime | None:
    value = match.get("kickoff")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WampFeedProtocolError("match.kickoff must be an ISO timezone-aware datetime")
    try:
        kickoff = datetime.fromisoformat(value)
    except ValueError:
        raise WampFeedProtocolError(
            "match.kickoff must be an ISO timezone-aware datetime"
        ) from None
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        raise WampFeedProtocolError("match.kickoff must be an ISO timezone-aware datetime")
    return kickoff.astimezone(UTC)


def _decimal_odds(record: Record) -> Decimal | None:
    value = record.get("odds")
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        odds = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return odds if odds.is_finite() and odds > 1 else None


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _live_competition_evidence(
    source: Source,
    match: Record,
    competition: str,
    *,
    resolved_parent_id: str | None = None,
    resolved_parent_name: str | None = None,
) -> SourceCompetitionEvidence:
    parent_id = (
        resolved_parent_id if resolved_parent_id is not None else _live_id(match.get("parentId"))
    )
    parent_name = (
        resolved_parent_name
        if resolved_parent_name is not None
        else _live_text(match.get("parentName"))
    )
    category_id = _live_id(match.get("categoryId"))
    category_name = _live_text(match.get("categoryName"))
    if parent_id is not None and parent_name is not None and category_name is not None:
        return SourceCompetitionEvidence(
            provider=source.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id=parent_id,
            competition_name=parent_name,
            country_id=category_id,
            country_name=category_name,
        )
    return _text_derived_competition(source, competition)


def _text_derived_competition(source: Source, category: str) -> SourceCompetitionEvidence:
    normalized_category = unicodedata.normalize("NFC", category).strip()
    review_key = hashlib.sha256(f"{source.value}\0{normalized_category}".encode()).hexdigest()[:20]
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=SourceIdentityKind.DERIVED_TEXT,
        competition_id=f"text:{review_key}",
        competition_name=normalized_category,
    )
