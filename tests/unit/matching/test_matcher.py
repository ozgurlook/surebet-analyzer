from datetime import UTC, datetime, timedelta
from decimal import Decimal

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.matching.matcher import match_events
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.taxonomy.models import (
    CanonicalLeague,
    CanonicalRegion,
    CompetitionKind,
    MappingMethod,
    RegionClass,
    ResolvedCompetition,
    SourceCompetitionEvidence,
    SourceIdentityKind,
    TaxonomyStatus,
)

NOW = datetime(2026, 8, 24, 15, 0, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 25, 20, 0, 0, tzinfo=UTC)


def _make_event(
    source: Source,
    event_id: str,
    competition: str,
    home: str,
    away: str,
    odds_tuple: tuple[str, str, str],
    kickoff: datetime = KICKOFF,
    competition_id: str = "league-1",
) -> CollectedEvent:
    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition=competition,
        home=home,
        away=away,
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=SourceCompetitionEvidence(
            provider=source.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id=competition_id,
            competition_name=competition,
            country_id="TR",
            country_name="Türkiye",
            kind=CompetitionKind.LEAGUE,
        ),
        quotes={
            Outcome.HOME: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.HOME,
                odds=Decimal(odds_tuple[0]),
                observed_at=NOW,
            ),
            Outcome.DRAW: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.DRAW,
                odds=Decimal(odds_tuple[1]),
                observed_at=NOW,
            ),
            Outcome.AWAY: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.AWAY,
                odds=Decimal(odds_tuple[2]),
                observed_at=NOW,
            ),
        },
    )


def _resolved_competition(
    source: Source,
    competition_id: str,
    *,
    iso_code: str = "TR",
    country_name: str = "Türkiye",
    league_id: str = "league:00000000-0000-4000-8000-000000000001",
    league_name: str = "Süper Lig",
) -> ResolvedCompetition:
    region = CanonicalRegion(
        region_id=f"country:{iso_code}",
        region_class=RegionClass.COUNTRY,
        iso_code=iso_code,
        display_name=country_name,
        normalized_key=country_name.casefold(),
    )
    league = CanonicalLeague(
        league_id=league_id,
        region_id=region.region_id,
        display_name=league_name,
        normalized_key=league_name.casefold(),
        kind=CompetitionKind.LEAGUE,
    )
    return ResolvedCompetition(
        source=source,
        competition_id=competition_id,
        region=region,
        league=league,
        method=MappingMethod.MANUAL,
        confidence=1.0,
    )


def _verified_index(*events: CollectedEvent) -> dict[tuple[Source, str], ResolvedCompetition]:
    return {
        (event.source, event.source_competition.competition_id): _resolved_competition(
            event.source, event.source_competition.competition_id
        )
        for event in events
        if event.source_competition is not None
    }


def test_match_events_groups_matching_events_across_sources() -> None:
    source_c_event = _make_event(
        Source.SOURCE_C,
        "source_c_1",
        "Trendyol Süper Lig",
        "Galatasaray A.Ş.",
        "Fenerbahçe SK",
        ("2.10", "3.40", "3.60"),
    )
    source_b_event = _make_event(
        Source.SOURCE_B,
        "source_b_1",
        "Super Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.15", "3.35", "3.55"),
    )
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a_1",
        "Türkiye Süper Lig",
        "Galatasaray Istanbul",
        "Fenerbahce SK",
        ("2.05", "3.50", "3.70"),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_event,)
        ),
    )

    matched = match_events(batches, _verified_index(source_c_event, source_b_event, source_a_event))
    assert len(matched) == 1
    m = matched[0]
    assert m.home in ("Galatasaray A.Ş.", "Galatasaray", "Galatasaray Istanbul")
    assert len(m.source_events) == 3
    assert Source.SOURCE_C in m.source_events
    assert Source.SOURCE_B in m.source_events
    assert Source.SOURCE_A in m.source_events
    assert m.quotes[(Source.SOURCE_C, Outcome.HOME)].odds == Decimal("2.10")
    assert m.quotes[(Source.SOURCE_A, Outcome.DRAW)].odds == Decimal("3.50")
    assert m.taxonomy_status is TaxonomyStatus.VERIFIED
    assert m.league_id == "league:00000000-0000-4000-8000-000000000001"


def test_match_events_rejects_mismatched_kickoffs_or_teams() -> None:
    source_c_event = _make_event(
        Source.SOURCE_C,
        "source_c_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.10", "3.40", "3.60"),
        kickoff=datetime(2026, 8, 25, 20, 0, 0, tzinfo=UTC),
    )
    source_b_diff_kickoff = _make_event(
        Source.SOURCE_B,
        "source_b_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.15", "3.35", "3.55"),
        kickoff=datetime(2026, 8, 28, 20, 0, 0, tzinfo=UTC),
    )
    source_a_diff_teams = _make_event(
        Source.SOURCE_A,
        "source_a_1",
        "Süper Lig",
        "Besiktas",
        "Trabzonspor",
        ("2.05", "3.50", "3.70"),
        kickoff=datetime(2026, 8, 25, 20, 0, 0, tzinfo=UTC),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_diff_kickoff,)
        ),
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_diff_teams,)
        ),
    )

    matched = match_events(
        batches, _verified_index(source_c_event, source_b_diff_kickoff, source_a_diff_teams)
    )
    assert len(matched) == 3
    # Each is in its own single-source matched event
    for m in matched:
        assert len(m.source_events) == 1


def test_unmapped_events_never_cross_source_match() -> None:
    source_c_event = _make_event(
        Source.SOURCE_C,
        "source_c_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahçe",
        ("2.10", "3.40", "3.60"),
    )
    source_b_event = _make_event(
        Source.SOURCE_B,
        "source_b_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahçe",
        ("2.15", "3.35", "3.55"),
    )
    batches = (
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
    )

    matched = match_events(batches, taxonomy_index={})

    assert len(matched) == 2
    assert all(event.taxonomy_status is TaxonomyStatus.UNMAPPED for event in matched)
    assert len({event.matched_id for event in matched}) == 2


def test_equal_league_names_in_different_countries_do_not_match() -> None:
    source_c_event = _make_event(
        Source.SOURCE_C, "source_c_1", "Premier League", "United", "City", ("2.10", "3.40", "3.60")
    )
    source_b_event = _make_event(
        Source.SOURCE_B, "source_b_1", "Premier League", "United", "City", ("2.15", "3.35", "3.55")
    )
    index = {
        (Source.SOURCE_C, "league-1"): _resolved_competition(
            Source.SOURCE_C,
            "league-1",
            iso_code="GB",
            country_name="United Kingdom",
            league_id="league:00000000-0000-4000-8000-000000000002",
            league_name="Premier League",
        ),
        (Source.SOURCE_B, "league-1"): _resolved_competition(
            Source.SOURCE_B,
            "league-1",
            iso_code="US",
            country_name="United States",
            league_id="league:00000000-0000-4000-8000-000000000003",
            league_name="Premier League",
        ),
    }
    batches = (
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
    )

    matched = match_events(batches, index)

    assert len(matched) == 2
    assert all(event.taxonomy_status is TaxonomyStatus.VERIFIED for event in matched)


def test_match_events_groups_all_four_sources_including_source_d() -> None:
    source_c_event = _make_event(
        Source.SOURCE_C,
        "source_c_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahçe",
        ("2.10", "3.40", "3.60"),
    )
    source_b_event = _make_event(
        Source.SOURCE_B,
        "source_b_1",
        "Süper Lig",
        "Galatasaray A.Ş.",
        "Fenerbahçe SK",
        ("2.15", "3.35", "3.55"),
    )
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a_1",
        "Türkiye Süper Lig",
        "Galatasaray Istanbul",
        "Fenerbahce",
        ("2.05", "3.50", "3.70"),
    )
    source_d_event = _make_event(
        Source.SOURCE_D,
        "source_d_1",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce SK",
        ("2.12", "3.45", "3.65"),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_D, started_at=NOW, finished_at=NOW, events=(source_d_event,)
        ),
    )

    index = _verified_index(source_c_event, source_b_event, source_a_event, source_d_event)
    matched = match_events(batches, index)

    assert len(matched) == 1
    m = matched[0]
    assert len(m.source_events) == 4
    assert set(m.source_events.keys()) == {
        Source.SOURCE_C,
        Source.SOURCE_B,
        Source.SOURCE_A,
        Source.SOURCE_D,
    }
    assert m.quotes[(Source.SOURCE_D, Outcome.HOME)].odds == Decimal("2.12")
    assert m.quotes[(Source.SOURCE_D, Outcome.DRAW)].odds == Decimal("3.45")
    assert m.quotes[(Source.SOURCE_D, Outcome.AWAY)].odds == Decimal("3.65")
    assert m.taxonomy_status is TaxonomyStatus.VERIFIED


def test_match_events_groups_bayern_munich_variations() -> None:
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a_bm",
        "Bundesliga",
        "Schalke 04",
        "Bayern Munich",
        ("14.0", "9.4", "1.14"),
    )
    source_b_event = _make_event(
        Source.SOURCE_B,
        "source_b_bm",
        "Bundesliga",
        "Schalke 04",
        "FC Bayern Münih",
        ("13.0", "7.5", "1.14"),
    )
    source_d_event = _make_event(
        Source.SOURCE_D,
        "source_d_bm",
        "Bundesliga",
        "Schalke",
        "Bayern Münih",
        ("14.0", "8.7", "1.15"),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_D, started_at=NOW, finished_at=NOW, events=(source_d_event,)
        ),
    )
    index = _verified_index(source_a_event, source_b_event, source_d_event)
    matched = match_events(batches, index)

    assert len(matched) == 1
    assert len(matched[0].source_events) == 3


def test_match_events_groups_psg_monaco_variations() -> None:
    source_d_event = _make_event(
        Source.SOURCE_D,
        "source_d_psg",
        "Ligue 1",
        "Paris St-Germain",
        "Monaco",
        ("1.43", "5.10", "6.20"),
    )
    source_b_event = _make_event(
        Source.SOURCE_B, "source_b_psg", "Ligue 1", "PSG", "Monaco", ("1.42", "4.75", "6.00")
    )
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a_psg",
        "Ligue 1",
        "Paris Saint-Germain",
        "AS Monaco",
        ("1.41", "5.10", "6.40"),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_D, started_at=NOW, finished_at=NOW, events=(source_d_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_event,)
        ),
    )
    index = _verified_index(source_d_event, source_b_event, source_a_event)
    matched = match_events(batches, index)

    assert len(matched) == 1
    assert len(matched[0].source_events) == 3


def test_match_events_groups_deportivo_coruna_variations() -> None:
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a_dep",
        "La Liga",
        "Villarreal",
        "Deportivo de A Coruna",
        ("1.48", "4.60", "6.00"),
    )
    source_d_event = _make_event(
        Source.SOURCE_D,
        "source_d_dep",
        "La Liga",
        "Villarreal",
        "Dep La Coruna",
        ("1.49", "4.43", "6.00"),
    )

    batches = (
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_event,)
        ),
        CollectionBatch(
            source=Source.SOURCE_D, started_at=NOW, finished_at=NOW, events=(source_d_event,)
        ),
    )
    index = _verified_index(source_a_event, source_d_event)
    matched = match_events(batches, index)

    assert len(matched) == 1
    assert len(matched[0].source_events) == 2


def test_matched_event_supports_basketball_quotes() -> None:
    from surebet.domain.enums import MarketType, Sport
    from surebet.matching.models import MatchedEvent

    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    q_ml = Quote(
        source=Source.SOURCE_B,
        event_id="e1",
        outcome=Outcome.HOME,
        odds=Decimal("1.85"),
        observed_at=now,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )
    q_ah = Quote(
        source=Source.SOURCE_A,
        event_id="e2",
        outcome=Outcome.AWAY,
        odds=Decimal("1.92"),
        observed_at=now,
        market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
        line=Decimal("4.5"),
    )

    ev = MatchedEvent(
        matched_id="bask-match-1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Euroleague",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id="bask-euroleague",
        league_name="Euroleague",
        home="Fenerbahçe",
        away="Panathinaikos",
        kickoff=now,
        source_events={Source.SOURCE_B: "e1", Source.SOURCE_A: "e2"},
        quotes={},
        sport=Sport.BASKETBALL,
        bask_ml_quotes={(Source.SOURCE_B, Outcome.HOME): q_ml},
        bask_ah_quotes={(Source.SOURCE_A, Outcome.AWAY, Decimal("4.5")): q_ah},
    )

    assert ev.sport is Sport.BASKETBALL
    assert (Source.SOURCE_B, Outcome.HOME) in ev.bask_ml_quotes
    assert (Source.SOURCE_A, Outcome.AWAY, Decimal("4.5")) in ev.bask_ah_quotes


def test_matcher_unifies_czechia_variations_across_three_sources() -> None:
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
    kickoff = datetime(2026, 9, 26, 18, 45, 0, tzinfo=UTC)

    e_source_a = _make_event(
        source=Source.SOURCE_A,
        event_id="source_a-cz-1",
        competition="UEFA Uluslar Ligi",
        home="Çek Cumh.",
        away="Hırvatistan",
        odds_tuple=("3.27", "3.50", "2.14"),
        kickoff=kickoff,
        competition_id="uefa-nations-league",
    )
    e_source_b = _make_event(
        source=Source.SOURCE_B,
        event_id="source_b-cz-1",
        competition="UEFA Uluslar Ligi",
        home="Çek Cumhuriyeti",
        away="Hırvatistan",
        odds_tuple=("3.05", "3.50", "2.16"),
        kickoff=kickoff,
        competition_id="uefa-nations-league",
    )
    e_source_d = _make_event(
        source=Source.SOURCE_D,
        event_id="source_d-cz-1",
        competition="UEFA Uluslar Ligi",
        home="Çekya",
        away="Hırvatistan",
        odds_tuple=("3.13", "3.49", "2.16"),
        kickoff=kickoff,
        competition_id="uefa-nations-league",
    )

    resolved_source_a = _resolved_competition(
        Source.SOURCE_A,
        "uefa-nations-league",
        league_id="league:00000000-0000-4000-8000-000000000099",
    )
    resolved_source_b = _resolved_competition(
        Source.SOURCE_B,
        "uefa-nations-league",
        league_id="league:00000000-0000-4000-8000-000000000099",
    )
    resolved_source_d = _resolved_competition(
        Source.SOURCE_D,
        "uefa-nations-league",
        league_id="league:00000000-0000-4000-8000-000000000099",
    )

    taxonomy_index = {
        (Source.SOURCE_A, "uefa-nations-league"): resolved_source_a,
        (Source.SOURCE_B, "uefa-nations-league"): resolved_source_b,
        (Source.SOURCE_D, "uefa-nations-league"): resolved_source_d,
    }

    batches = [
        CollectionBatch(
            source=Source.SOURCE_A, events=[e_source_a], started_at=now, finished_at=now
        ),
        CollectionBatch(
            source=Source.SOURCE_B, events=[e_source_b], started_at=now, finished_at=now
        ),
        CollectionBatch(
            source=Source.SOURCE_D, events=[e_source_d], started_at=now, finished_at=now
        ),
    ]

    matched = match_events(batches, taxonomy_index)
    assert len(matched) == 1
    assert set(matched[0].source_events.keys()) == {
        Source.SOURCE_A,
        Source.SOURCE_B,
        Source.SOURCE_D,
    }


def test_matcher_recovers_a_strong_source_a_team_match_with_bad_provider_time() -> None:
    canonical_kickoff = datetime(2026, 10, 10, 18, 45, tzinfo=UTC)
    source_a_event = _make_event(
        Source.SOURCE_A,
        "30903195",
        "Lig 1",
        "Stade Brestois",
        "Angers SCO",
        ("2.10", "3.40", "3.60"),
        kickoff=canonical_kickoff - timedelta(hours=18, minutes=22),
    )
    other_event = _make_event(
        Source.SOURCE_B,
        "40740080",
        "France. Ligue 1",
        "Brest",
        "Angers",
        ("2.15", "3.35", "3.55"),
        kickoff=canonical_kickoff,
    )

    matched = match_events(
        (
            CollectionBatch(
                source=Source.SOURCE_A,
                started_at=NOW,
                finished_at=NOW,
                events=(source_a_event,),
            ),
            CollectionBatch(
                source=Source.SOURCE_B,
                started_at=NOW,
                finished_at=NOW,
                events=(other_event,),
            ),
        ),
        _verified_index(source_a_event, other_event),
    )

    assert len(matched) == 1
    assert set(matched[0].source_events) == {Source.SOURCE_A, Source.SOURCE_B}


def test_matcher_keeps_extended_time_recovery_specific_to_source_a() -> None:
    source_b_event = _make_event(
        Source.SOURCE_B,
        "source_b-1",
        "Lig 1",
        "Brest",
        "Angers",
        ("2.10", "3.40", "3.60"),
        kickoff=KICKOFF,
    )
    source_e_event = _make_event(
        Source.SOURCE_E,
        "source_e-1",
        "Lig 1",
        "Brest",
        "Angers",
        ("2.15", "3.35", "3.55"),
        kickoff=KICKOFF + timedelta(hours=4),
    )

    matched = match_events(
        (
            CollectionBatch(
                source=Source.SOURCE_B,
                started_at=NOW,
                finished_at=NOW,
                events=(source_b_event,),
            ),
            CollectionBatch(
                source=Source.SOURCE_E,
                started_at=NOW,
                finished_at=NOW,
                events=(source_e_event,),
            ),
        ),
        _verified_index(source_b_event, source_e_event),
    )

    assert len(matched) == 2


def test_matcher_rejects_source_a_time_recovery_beyond_one_day() -> None:
    source_a_event = _make_event(
        Source.SOURCE_A,
        "source_a-1",
        "Lig 1",
        "Brest",
        "Angers",
        ("2.10", "3.40", "3.60"),
        kickoff=KICKOFF,
    )
    other_event = _make_event(
        Source.SOURCE_B,
        "source_b-1",
        "Lig 1",
        "Brest",
        "Angers",
        ("2.15", "3.35", "3.55"),
        kickoff=KICKOFF + timedelta(hours=25),
    )

    matched = match_events(
        (
            CollectionBatch(
                source=Source.SOURCE_A,
                started_at=NOW,
                finished_at=NOW,
                events=(source_a_event,),
            ),
            CollectionBatch(
                source=Source.SOURCE_B,
                started_at=NOW,
                finished_at=NOW,
                events=(other_event,),
            ),
        ),
        _verified_index(source_a_event, other_event),
    )

    assert len(matched) == 2
