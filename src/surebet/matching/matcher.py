"""Conservative event matcher grouping collected events across sources."""

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from surebet.domain.enums import Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.taxonomy.models import ResolvedCompetition, TaxonomyStatus

from .models import MatchedEvent
from .normalizer import normalize_team_name

_MAX_KICKOFF_DELTA = timedelta(hours=3)
_MAX_SOURCE_A_KICKOFF_CORRECTION = timedelta(hours=24)


_SOURCE_DISPLAY_PRIORITY: dict[Source, int] = {
    Source.SOURCE_C: 0,
    Source.SOURCE_B: 1,
    Source.SOURCE_A: 2,
    Source.SOURCE_D: 3,
}


def _team_similarity(norm1: str, norm2: str) -> bool:
    if norm1 == norm2:
        return True
    if len(norm1) >= 4 and (norm1 in norm2 or norm2 in norm1):
        return True
    # Token set intersection
    set1 = set(norm1.split())
    set2 = set(norm2.split())
    if bool(set1) and bool(set2) and (set1.issubset(set2) or set2.issubset(set1)):
        return True
    # High fuzzy similarity for slight transliterations / typos (len >= 5)
    return (
        len(norm1) >= 5 and len(norm2) >= 5 and SequenceMatcher(None, norm1, norm2).ratio() >= 0.84
    )


def _strong_team_similarity(norm1: str, norm2: str) -> bool:
    """Require equality or a meaningful containment match for time correction."""

    return norm1 == norm2 or (
        min(len(norm1), len(norm2)) >= 4 and (norm1 in norm2 or norm2 in norm1)
    )


def _events_match(e1: CollectedEvent, e2: CollectedEvent) -> bool:
    # Check kickoff time
    t1 = e1.kickoff.astimezone(UTC)
    t2 = e2.kickoff.astimezone(UTC)
    kickoff_delta = abs(t1 - t2)
    extended_source_a_time_match = kickoff_delta > _MAX_KICKOFF_DELTA
    if extended_source_a_time_match and (
        kickoff_delta > _MAX_SOURCE_A_KICKOFF_CORRECTION
        or Source.SOURCE_A not in {e1.source, e2.source}
        or e1.sport is not Sport.FOOTBALL
        or e2.sport is not Sport.FOOTBALL
    ):
        return False

    # Check home team
    h1 = normalize_team_name(e1.home)
    h2 = normalize_team_name(e2.home)
    team_matches = _strong_team_similarity if extended_source_a_time_match else _team_similarity
    if not team_matches(h1, h2):
        return False

    # Check away team
    a1 = normalize_team_name(e1.away)
    a2 = normalize_team_name(e2.away)
    return team_matches(a1, a2)


def _make_matched_id(identity: str, home: str, away: str, kickoff: datetime) -> str:
    utc_kickoff = kickoff.astimezone(UTC).strftime("%Y-%m-%d")
    raw = f"{identity}:{home}:{away}:{utc_kickoff}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_event(
    event: CollectedEvent,
    taxonomy_index: Mapping[tuple[Source, str], ResolvedCompetition],
) -> ResolvedCompetition | None:
    evidence = event.source_competition
    if evidence is None:
        return None
    return taxonomy_index.get((event.source, evidence.competition_id))


def match_events(
    batches: Sequence[CollectionBatch],
    taxonomy_index: Mapping[tuple[Source, str], ResolvedCompetition],
) -> tuple[MatchedEvent, ...]:
    """Group events across sportsbook collection batches into canonical MatchedEvents."""
    all_events: list[CollectedEvent] = []
    for batch in batches:
        all_events.extend(batch.events)

    clusters: list[list[tuple[CollectedEvent, ResolvedCompetition | None]]] = []
    clusters_by_league: dict[str, list[list[tuple[CollectedEvent, ResolvedCompetition | None]]]] = (
        defaultdict(list)
    )

    for event in all_events:
        resolved = _resolve_event(event, taxonomy_index)
        if resolved is None:
            clusters.append([(event, None)])
            continue

        target_clusters = clusters_by_league[resolved.league.league_id]
        matched_cluster: list[tuple[CollectedEvent, ResolvedCompetition | None]] | None = None
        for cluster in target_clusters:
            # Check if this cluster already has an event from the same source
            if any(existing.source is event.source for existing, _ in cluster):
                continue
            # Transitive matching: check if event matches any event in the cluster
            if any(_events_match(existing, event) for existing, _ in cluster):
                matched_cluster = cluster
                break

        if matched_cluster is not None:
            matched_cluster.append((event, resolved))
        else:
            new_cluster: list[tuple[CollectedEvent, ResolvedCompetition | None]] = [
                (event, resolved)
            ]
            clusters.append(new_cluster)
            target_clusters.append(new_cluster)

    result: list[MatchedEvent] = []
    for cluster in clusters:
        # Choose canonical names and kickoff
        first, resolved = cluster[0]
        # Sort cluster so Source C or Source B preferred for display name
        canonical_event = min(
            (event for event, _ in cluster),
            key=lambda e: _SOURCE_DISPLAY_PRIORITY.get(e.source, 99),
        )

        norm_home = normalize_team_name(canonical_event.home)
        norm_away = normalize_team_name(canonical_event.away)
        identity = (
            resolved.league.league_id
            if resolved is not None
            else f"unmapped:{first.source.value}:{first.source_event_id}"
        )
        matched_id = _make_matched_id(identity, norm_home, norm_away, canonical_event.kickoff)

        source_events: dict[Source, str] = {}
        quotes: dict[tuple[Source, Outcome], Quote] = {}
        ou25_quotes: dict[tuple[Source, TwoWayOutcome], Quote] = {}
        bask_ml_quotes: dict[tuple[Source, Outcome], Quote] = {}
        bask_ah_quotes: dict[tuple[Source, Outcome, Decimal], Quote] = {}
        sport = canonical_event.sport

        for ev, _ in cluster:
            source_events[ev.source] = ev.source_event_id
            for outcome, quote in ev.quotes.items():
                quotes[(ev.source, outcome)] = quote
            if ev.ou25_quotes:
                for outcome_2w, quote in ev.ou25_quotes.items():
                    ou25_quotes[(ev.source, outcome_2w)] = quote
            if ev.bask_ml_quotes:
                for b_outcome, quote in ev.bask_ml_quotes.items():
                    bask_ml_quotes[(ev.source, b_outcome)] = quote
            if ev.bask_ah_quotes:
                for (b_outcome, b_line), quote in ev.bask_ah_quotes.items():
                    bask_ah_quotes[(ev.source, b_outcome, b_line)] = quote

        result.append(
            MatchedEvent(
                matched_id=matched_id,
                taxonomy_status=(
                    TaxonomyStatus.VERIFIED if resolved is not None else TaxonomyStatus.UNMAPPED
                ),
                competition=canonical_event.competition,
                country_id=resolved.region.region_id if resolved is not None else None,
                country_name=resolved.region.display_name if resolved is not None else None,
                country_iso_code=resolved.region.iso_code if resolved is not None else None,
                league_id=resolved.league.league_id if resolved is not None else None,
                league_name=resolved.league.display_name if resolved is not None else None,
                home=canonical_event.home,
                away=canonical_event.away,
                kickoff=canonical_event.kickoff,
                source_events=source_events,
                quotes=quotes,
                ou25_quotes=ou25_quotes,
                sport=sport,
                bask_ml_quotes=bask_ml_quotes,
                bask_ah_quotes=bask_ah_quotes,
            )
        )

    # Sort by kickoff time, then home team
    result.sort(key=lambda m: (m.kickoff, m.home))
    return tuple(result)
