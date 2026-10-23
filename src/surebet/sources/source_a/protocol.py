"""Pure builders and parsers for Source A's public WebSocket JSON-RPC data."""

from collections import defaultdict
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from surebet.domain.enums import MarketType, Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind

_MAX_DEPTH = 32
_MAX_CONTAINERS = 50_000
_PREMATCH_FILTER: dict[str, Any] = {
    "@or": [
        {"visible_in_prematch": 1},
        {"type": {"@in": [0, 2]}},
    ]
}
_OUTCOME_MAP = {
    "W1": Outcome.HOME,
    "X": Outcome.DRAW,
    "W2": Outcome.AWAY,
}


def build_games_query() -> dict[str, Any]:
    """Return the allowlisted public pre-match football identity query."""

    return {
        "source": "betting",
        "what": {
            "sport": ["id", "name", "alias"],
            "region": ["id", "name", "alias"],
            "competition": ["id", "name"],
            "game": [
                "id",
                "team1_name",
                "team2_name",
                "start_ts",
                "is_blocked",
                "sport_alias",
            ],
        },
        "where": {
            "sport": {"alias": "Soccer"},
            "game": _PREMATCH_FILTER,
        },
    }


def build_markets_query() -> dict[str, Any]:
    """Return the allowlisted public full-time-result price query."""

    return {
        "source": "betting",
        "what": {
            "sport": ["id"],
            "region": ["id"],
            "competition": ["id"],
            "game": ["id"],
            "market": ["id", "name", "type", "display_key"],
            "event": ["id", "type_1", "price"],
        },
        "where": {
            "sport": {"alias": "Soccer"},
            "game": _PREMATCH_FILTER,
            "market": {
                "type": "P1XP2",
                "display_key": "WINNER",
                "display_sub_key": "MATCH",
            },
        },
    }


def build_ou25_query() -> dict[str, Any]:
    """Return the allowlisted public over/under 2.5 goals price query."""

    return {
        "source": "betting",
        "what": {
            "sport": ["id"],
            "region": ["id"],
            "competition": ["id"],
            "game": ["id"],
            "market": ["id", "name", "type", "base"],
            "event": ["id", "type_1", "price"],
        },
        "where": {
            "sport": {"alias": "Soccer"},
            "game": _PREMATCH_FILTER,
            "market": {
                "type": "OverUnder",
                "base": 2.5,
            },
        },
    }


def _walk(root: object) -> Iterator[Mapping[str, Any]]:
    stack: list[tuple[object, int]] = [(root, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_DEPTH:
            raise ValueError("JSON-RPC payload exceeds maximum nesting depth")
        if isinstance(value, Mapping):
            visited += 1
            if visited > _MAX_CONTAINERS:
                raise ValueError("JSON-RPC payload exceeds maximum container count")
            yield value
            for child in reversed(tuple(value.values())):
                if isinstance(child, (Mapping, list, tuple)):
                    stack.append((child, depth + 1))
        elif isinstance(value, (list, tuple)):
            visited += 1
            if visited > _MAX_CONTAINERS:
                raise ValueError("JSON-RPC payload exceeds maximum container count")
            for child in reversed(value):
                if isinstance(child, (Mapping, list, tuple)):
                    stack.append((child, depth + 1))


def _entities(value: object) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return tuple(item for item in value.values() if isinstance(item, Mapping))
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _collection_root(value: object) -> object:
    if isinstance(value, Mapping) and set(value) == {"data"}:
        nested = value["data"]
        if isinstance(nested, (Mapping, list, tuple)):
            return nested
    return value


def _safe_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _games_by_id(
    root: object, source: Source = Source.SOURCE_A
) -> dict[str, tuple[SourceCompetitionEvidence, Mapping[str, Any]]]:
    games: dict[str, tuple[SourceCompetitionEvidence, Mapping[str, Any]]] = {}
    # Preserve the existing bounded-payload validation before following the
    # documented sport/region/competition hierarchy below.
    for _ in _walk(root):
        pass
    if not isinstance(root, Mapping):
        return games
    for sport in _entities(root.get("sport")):
        for region in _entities(sport.get("region")):
            country_id = _safe_id(region.get("id"))
            country_name = _safe_text(region.get("name"))
            country_alias = _safe_text(region.get("alias"))
            for competition in _entities(region.get("competition")):
                competition_id = _safe_id(competition.get("id"))
                competition_name = _safe_text(competition.get("name"))
                if competition_id is None or competition_name is None:
                    continue
                evidence = SourceCompetitionEvidence(
                    provider=source.value,
                    identity_kind=SourceIdentityKind.PROVIDER_ID,
                    country_id=country_id,
                    country_name=country_name,
                    country_alias=country_alias,
                    competition_id=competition_id,
                    competition_name=competition_name,
                )
                for game in _entities(competition.get("game")):
                    game_id = _safe_id(game.get("id"))
                    if game_id is None or game_id in games:
                        continue
                    games[game_id] = (evidence, game)

    if not games:
        for competition in _entities(root.get("competition")):
            competition_id = _safe_id(competition.get("id"))
            competition_name = _safe_text(competition.get("name"))
            if competition_id is None or competition_name is None:
                continue
            evidence = SourceCompetitionEvidence(
                provider=source.value,
                identity_kind=SourceIdentityKind.PROVIDER_ID,
                competition_id=competition_id,
                competition_name=competition_name,
            )
            for game in _entities(competition.get("game")):
                game_id = _safe_id(game.get("id"))
                if game_id is None or game_id in games:
                    continue
                games[game_id] = (evidence, game)

    if not games:
        for game in _entities(root.get("game")):
            game_id = _safe_id(game.get("id"))
            if game_id is None or game_id in games:
                continue
            raw_comp = game.get("competition")
            comp_dict: Mapping[str, Any] = raw_comp if isinstance(raw_comp, Mapping) else {}
            comp_id = _safe_id(comp_dict.get("id")) or "7111"
            comp_name = _safe_text(comp_dict.get("name")) or "Avrupa Ligi"
            evidence = SourceCompetitionEvidence(
                provider=source.value,
                identity_kind=SourceIdentityKind.PROVIDER_ID,
                competition_id=comp_id,
                competition_name=comp_name,
            )
            games[game_id] = (evidence, game)

    return games


def _safe_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal_price(value: object) -> Decimal | None:
    if isinstance(value, (bool, float)):
        return None
    if not isinstance(value, (Decimal, int, str)):
        return None
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not price.is_finite() or price <= 1:
        return None
    return price


def _market_quotes(root: object, observed_at: datetime) -> dict[str, Mapping[Outcome, Quote]]:
    markets_by_game: dict[str, list[Mapping[Outcome, Quote]]] = {}
    for game in _walk(root):
        if "market" not in game:
            continue
        game_id = _safe_id(game.get("id"))
        if game_id is None:
            continue
        for market in _entities(game.get("market")):
            if market.get("type") != "P1XP2" or market.get("display_key") != "WINNER":
                continue
            quotes: dict[Outcome, Quote] = {}
            invalid = False
            for event in _entities(market.get("event")):
                outcome_name = event.get("type_1")
                outcome = _OUTCOME_MAP.get(outcome_name) if isinstance(outcome_name, str) else None
                price = _decimal_price(event.get("price"))
                if outcome is None or price is None or outcome in quotes:
                    invalid = True
                    break
                quotes[outcome] = Quote(
                    source=Source.SOURCE_A,
                    event_id=game_id,
                    outcome=outcome,
                    odds=price,
                    observed_at=observed_at,
                    market_type=MarketType.FULL_TIME_RESULT,
                )
            if not invalid and set(quotes) == set(Outcome):
                markets_by_game.setdefault(game_id, []).append(quotes)
    return {
        game_id: candidates[0]
        for game_id, candidates in markets_by_game.items()
        if len(candidates) == 1
    }


def _ou25_quotes(root: object, observed_at: datetime) -> dict[str, Mapping[TwoWayOutcome, Quote]]:
    if not root:
        return {}
    markets_by_game: dict[str, list[Mapping[TwoWayOutcome, Quote]]] = {}
    for game in _walk(root):
        if "market" not in game:
            continue
        game_id = _safe_id(game.get("id"))
        if game_id is None:
            continue
        for market in _entities(game.get("market")):
            m_type = market.get("type")
            m_base = market.get("base")
            if m_type != "OverUnder":
                continue
            if m_base is not None and str(m_base) != "2.5":
                continue
            quotes: dict[TwoWayOutcome, Quote] = {}
            invalid = False
            for event in _entities(market.get("event")):
                outcome_name = event.get("type_1")
                outcome = None
                if outcome_name == "Over":
                    outcome = TwoWayOutcome.OVER
                elif outcome_name == "Under":
                    outcome = TwoWayOutcome.UNDER
                price = _decimal_price(event.get("price"))
                if outcome is None or price is None or outcome in quotes:
                    invalid = True
                    break
                quotes[outcome] = Quote(
                    source=Source.SOURCE_A,
                    event_id=game_id,
                    outcome=outcome,
                    odds=price,
                    observed_at=observed_at,
                    market_type=MarketType.TOTAL_GOALS_25,
                )
            if not invalid and set(quotes) == {TwoWayOutcome.OVER, TwoWayOutcome.UNDER}:
                markets_by_game.setdefault(game_id, []).append(quotes)
    return {
        game_id: candidates[0]
        for game_id, candidates in markets_by_game.items()
        if len(candidates) == 1
    }


def parse_collection(
    games_data: object,
    markets_data: object,
    ou25_data: object | None = None,
    *,
    observed_at: datetime,
) -> tuple[CollectedEvent, ...]:
    """Convert sanitized JSON-RPC snapshots into complete canonical 1X2 events."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    games = _games_by_id(_collection_root(games_data))
    prices = _market_quotes(_collection_root(markets_data), observed_at)
    ou25_prices = _ou25_quotes(_collection_root(ou25_data), observed_at) if ou25_data else {}
    collected: list[CollectedEvent] = []
    for game_id, (source_competition, game) in games.items():
        if game_id not in prices or game.get("is_blocked") not in (None, 0, False):
            continue
        home = game.get("team1_name")
        away = game.get("team2_name")
        start_ts = game.get("start_ts")
        if (
            not isinstance(home, str)
            or not home.strip()
            or not isinstance(away, str)
            or not away.strip()
            or isinstance(start_ts, bool)
            or not isinstance(start_ts, int)
            or game.get("sport_alias") not in (None, "Soccer")
        ):
            continue
        try:
            kickoff = datetime.fromtimestamp(start_ts, tz=UTC)
            if kickoff <= observed_at.astimezone(UTC):
                continue
            event = CollectedEvent(
                source=Source.SOURCE_A,
                source_event_id=game_id,
                competition=source_competition.competition_name,
                home=home,
                away=away,
                kickoff=kickoff,
                market_key=FULL_TIME_RESULT_MARKET,
                quotes=prices[game_id],
                ou25_quotes=ou25_prices.get(game_id),
                source_competition=source_competition,
            )
        except (OSError, OverflowError, ValueError):
            continue
        collected.append(event)
    return tuple(sorted(collected, key=lambda item: (item.kickoff, item.source_event_id)))


def build_basketball_games_query(competition_id: int = 7111) -> dict[str, Any]:
    """Return the allowlisted public pre-match basketball games query."""
    return {
        "source": "betting",
        "what": {
            "sport": ["id", "name", "alias"],
            "region": ["id", "name", "alias"],
            "competition": ["id", "name"],
            "game": [
                "id",
                "team1_name",
                "team2_name",
                "start_ts",
                "is_blocked",
                "sport_alias",
            ],
        },
        "where": {
            "competition": {"id": competition_id},
            "game": _PREMATCH_FILTER,
        },
    }


def build_basketball_markets_query(competition_id: int = 7111) -> dict[str, Any]:
    """Return the allowlisted public basketball winner & handicap markets query."""
    return {
        "source": "betting",
        "what": {
            "sport": ["id"],
            "region": ["id"],
            "competition": ["id"],
            "game": ["id"],
            "market": ["id", "name", "type", "base", "display_key"],
            "event": ["id", "name", "type_1", "price", "base"],
        },
        "where": {
            "competition": {"id": competition_id},
            "game": _PREMATCH_FILTER,
            "market": {
                "type": {"@in": ["P1P2", "MatchHandicap"]},
            },
        },
    }


def _basketball_market_quotes(
    root: object, observed_at: datetime, source: Source = Source.SOURCE_A
) -> tuple[dict[str, Mapping[Outcome, Quote]], dict[str, Mapping[tuple[Outcome, Decimal], Quote]]]:
    ml_by_game: dict[str, Mapping[Outcome, Quote]] = {}
    ah_by_game: dict[str, dict[tuple[Outcome, Decimal], Quote]] = defaultdict(dict)

    for node in _walk(root):
        game_items: list[tuple[str | None, Mapping[str, Any]]] = []
        if "market" in node:
            game_items.append((_safe_id(node.get("id")), node))
        if "game" in node and isinstance(node["game"], Mapping):
            for gid, g in node["game"].items():
                if isinstance(g, Mapping) and "market" in g:
                    game_items.append((_safe_id(g.get("id")) or _safe_id(gid), g))

        for game_id, game in game_items:
            if game_id is None:
                continue
            for market in _entities(game.get("market")):
                m_type = market.get("type")
                m_name = str(market.get("name") or "").strip().lower()

                # Moneyline
                if m_type == "P1P2" or "kazanan" in m_name or market.get("display_key") == "WINNER":
                    quotes: dict[Outcome, Quote] = {}
                    for event in _entities(market.get("event")):
                        name = str(event.get("name") or "").strip()
                        type_1 = str(event.get("type_1") or "").strip()
                        outcome = None
                        if type_1 in ("W1", "1") or name in ("W1", "1"):
                            outcome = Outcome.HOME
                        elif type_1 in ("W2", "2") or name in ("W2", "2"):
                            outcome = Outcome.AWAY
                        price = _decimal_price(event.get("price"))
                        if outcome is not None and price is not None and outcome not in quotes:
                            quotes[outcome] = Quote(
                                source=source,
                                event_id=game_id,
                                outcome=outcome,
                                odds=price,
                                observed_at=observed_at,
                                market_type=MarketType.BASKETBALL_MONEYLINE,
                            )
                    if set(quotes) == {Outcome.HOME, Outcome.AWAY} and game_id not in ml_by_game:
                        ml_by_game[game_id] = quotes

                # Asian Handicap
                if (
                    m_type in ("MatchHandicap", "Handicap")
                    or "handikap" in m_name
                    or market.get("display_key") == "HANDICAP"
                ):
                    m_base = market.get("base")
                    for event in _entities(market.get("event")):
                        name = str(event.get("name") or "").strip()
                        type_1 = str(event.get("type_1") or "").strip()
                        outcome = None
                        if type_1 in ("Handicap1", "W1", "1") or name in ("Handicap 1", "1"):
                            outcome = Outcome.HOME
                        elif type_1 in ("Handicap2", "W2", "2") or name in ("Handicap 2", "2"):
                            outcome = Outcome.AWAY
                        price = _decimal_price(event.get("price"))
                        raw_base = event.get("base") if event.get("base") is not None else m_base
                        if raw_base is None:
                            continue
                        try:
                            base_val = Decimal(str(raw_base))
                        except (InvalidOperation, ValueError):
                            continue
                        if outcome is not None and price is not None:
                            quote = Quote(
                                source=source,
                                event_id=game_id,
                                outcome=outcome,
                                odds=price,
                                observed_at=observed_at,
                                market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
                                line=base_val,
                            )
                            ah_by_game[game_id][(outcome, base_val)] = quote

    return ml_by_game, dict(ah_by_game)


def parse_basketball_collection(
    games_data: object,
    markets_data: object,
    *,
    observed_at: datetime,
    source: Source = Source.SOURCE_A,
) -> tuple[CollectedEvent, ...]:
    """Convert sanitized JSON-RPC basketball snapshots into canonical events."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    games = _games_by_id(_collection_root(games_data))
    ml_quotes_by_game, ah_quotes_by_game = _basketball_market_quotes(
        _collection_root(markets_data), observed_at, source=source
    )
    collected: list[CollectedEvent] = []
    for game_id, (source_competition, game) in games.items():
        if game.get("is_blocked") not in (None, 0, False):
            continue
        ml_quotes = ml_quotes_by_game.get(game_id)
        ah_quotes = ah_quotes_by_game.get(game_id)
        if not ml_quotes and not ah_quotes:
            continue
        home = game.get("team1_name")
        away = game.get("team2_name")
        start_ts = game.get("start_ts")
        if (
            not isinstance(home, str)
            or not home.strip()
            or not isinstance(away, str)
            or not away.strip()
            or isinstance(start_ts, bool)
            or not isinstance(start_ts, int)
        ):
            continue
        try:
            kickoff = datetime.fromtimestamp(start_ts, tz=UTC)
            if kickoff <= observed_at.astimezone(UTC):
                continue
            event = CollectedEvent(
                source=source,
                source_event_id=game_id,
                competition=source_competition.competition_name,
                home=home,
                away=away,
                kickoff=kickoff,
                market_key="basketball.euroleague",
                sport=Sport.BASKETBALL,
                quotes={},
                bask_ml_quotes=ml_quotes,
                bask_ah_quotes=ah_quotes,
                source_competition=source_competition,
            )
        except (OSError, OverflowError, ValueError):
            continue
        collected.append(event)
    return tuple(sorted(collected, key=lambda item: (item.kickoff, item.source_event_id)))
