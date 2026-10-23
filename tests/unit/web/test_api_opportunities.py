from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.domain.enums import Outcome, Source, Sport
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.settings import UserPreferencesRepository, UserPreferencesUpdate
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import CompetitionKind, SourceCompetitionEvidence, SourceIdentityKind
from surebet.web.app import create_app
from tests.conftest import RegistryFactory


def _source_competition(source: Source) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="super-lig",
        competition_name="Super Lig",
        country_id="TR",
        country_name="Türkiye",
        kind=CompetitionKind.LEAGUE,
    )


def _seed_batches(snapshot_repo: SnapshotRepository, taxonomy_repo: TaxonomyRepository) -> None:
    now = datetime.now(UTC)
    kickoff = now + timedelta(days=1)

    source_c_event = CollectedEvent(
        source=Source.SOURCE_C,
        source_event_id="m1",
        competition="Super Lig",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_source_competition(Source.SOURCE_C),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_C,
                event_id="m1",
                outcome=Outcome.HOME,
                odds=Decimal("2.90"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_C,
                event_id="m1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.40"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_C,
                event_id="m1",
                outcome=Outcome.AWAY,
                odds=Decimal("2.50"),
                observed_at=now,
            ),
        },
    )

    source_b_event = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="j1",
        competition="Super Lig",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_source_competition(Source.SOURCE_B),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_B,
                event_id="j1",
                outcome=Outcome.HOME,
                odds=Decimal("2.60"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_B,
                event_id="j1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.60"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_B,
                event_id="j1",
                outcome=Outcome.AWAY,
                odds=Decimal("3.10"),
                observed_at=now,
            ),
        },
    )

    # An opportunity must span two independent operators, so a Source A draw is required for
    # these API-shape tests to have any opportunity to return at all.
    source_a_event = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="s1",
        competition="Super Lig",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_source_competition(Source.SOURCE_A),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_A,
                event_id="s1",
                outcome=Outcome.HOME,
                odds=Decimal("2.70"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_A,
                event_id="s1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.80"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_A,
                event_id="s1",
                outcome=Outcome.AWAY,
                odds=Decimal("2.80"),
                observed_at=now,
            ),
        },
    )

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=now, finished_at=now, events=(source_c_event,)
        ),
        duration_ms=200,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B, started_at=now, finished_at=now, events=(source_b_event,)
        ),
        duration_ms=250,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A, started_at=now, finished_at=now, events=(source_a_event,)
        ),
        duration_ms=180,
    )
    for event in (source_c_event, source_b_event, source_a_event):
        assert event.source_competition is not None
        taxonomy_repo.observe(event.source, event.source_competition, now)


@pytest.fixture
def client(tmp_path: Path, tracked_registry_factory: RegistryFactory) -> tuple[TestClient, str]:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    # This module covers opportunity calculation, so it declares its own tracked
    # scope rather than borrowing the packaged league list.
    registry = tracked_registry_factory(
        [("super-lig", "country:TR", "Super Lig", {source: "super-lig" for source in Source})]
    )

    snapshot_repo = SnapshotRepository(db_path)
    _seed_batches(snapshot_repo, TaxonomyRepository(db_path, registry))

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


def test_get_opportunities_and_detail(client: tuple[TestClient, str]) -> None:
    c, _ = client

    # GET opportunities with budget
    resp = c.get("/api/v1/opportunities?budget_cents=100000")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    opp = data[0]
    assert opp["home"] == "Galatasaray"
    assert opp["away"] == "Fenerbahce"
    assert opp["budget_cents"] == 100000
    assert "selections" in opp
    assert "scenarios" in opp
    assert opp["min_adjusted_profit_cents"] > 0

    opp_id = opp["opportunity_id"]

    # GET opportunity detail
    detail_resp = c.get(f"/api/v1/opportunities/{opp_id}?budget_cents=100000")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["opportunity_id"] == opp_id
    assert "HOME" in detail["scenarios"]
    assert "DRAW" in detail["scenarios"]
    assert "AWAY" in detail["scenarios"]
    assert "alternatives" in detail
    assert isinstance(detail["alternatives"], list)


def test_opportunities_grouped_by_match_with_alternatives(client: tuple[TestClient, str]) -> None:
    c, _ = client

    resp = c.get("/api/v1/opportunities?budget_cents=100000")
    assert resp.status_code == 200
    data = resp.json()

    # Each match should only appear ONCE in the top-level list
    matched_ids = [opp["matched_id"] for opp in data]
    assert len(matched_ids) == len(set(matched_ids))

    for opp in data:
        assert "alternatives" in opp
        assert isinstance(opp["alternatives"], list)
        for alt in opp["alternatives"]:
            assert alt["matched_id"] == opp["matched_id"]
            # Alternatives should be sorted descending by profit
            assert alt["min_adjusted_profit_cents"] <= opp["min_adjusted_profit_cents"]


@pytest.mark.parametrize(
    "path",
    ["/api/v1/opportunities", "/api/v1/opportunities/does-not-matter"],
)
def test_opportunities_reject_budget_above_ten_million_liras(
    client: tuple[TestClient, str], path: str
) -> None:
    c, _ = client

    response = c.get(path, params={"budget_cents": 1_000_000_001})

    assert response.status_code == 422


def _evidence(
    source: Source, competition_id: str, competition_name: str, country_id: str, country_name: str
) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id=competition_id,
        competition_name=competition_name,
        country_id=country_id,
        country_name=country_name,
        kind=CompetitionKind.LEAGUE,
    )


def _quote(
    source: Source, event_id: str, outcome: Outcome, odds: str, observed_at: datetime
) -> Quote:
    return Quote(
        source=source,
        event_id=event_id,
        outcome=outcome,
        odds=Decimal(odds),
        observed_at=observed_at,
    )


def _matched_pair(
    *,
    competition_id: str,
    competition_name: str,
    country_id: str,
    country_name: str,
    home: str,
    away: str,
    kickoff: datetime,
    event_id_prefix: str,
    observed_at: datetime,
) -> dict[Source, CollectedEvent]:
    """SOURCE_C + SOURCE_B + SOURCE_A triple with the same odds pattern as `_seed_batches`, so any two
    matches built with this helper tie on `min_adjusted_profit_cents`.

    Source A is present because an opportunity must span two independent operators; without it
    every assignment would sit inside the single WAMP feed operator and produce nothing."""
    pair: dict[Source, CollectedEvent] = {}
    odds = {
        Source.SOURCE_C: {Outcome.HOME: "2.90", Outcome.DRAW: "3.40", Outcome.AWAY: "2.50"},
        Source.SOURCE_B: {Outcome.HOME: "2.60", Outcome.DRAW: "3.60", Outcome.AWAY: "3.10"},
        Source.SOURCE_A: {Outcome.HOME: "2.70", Outcome.DRAW: "3.80", Outcome.AWAY: "2.80"},
    }
    for source, suffix in (
        (Source.SOURCE_C, "source_c"),
        (Source.SOURCE_B, "source_b"),
        (Source.SOURCE_A, "source_a"),
    ):
        event_id = f"{event_id_prefix}-{suffix}"
        evidence = _evidence(source, competition_id, competition_name, country_id, country_name)
        pair[source] = CollectedEvent(
            source=source,
            source_event_id=event_id,
            competition=competition_name,
            home=home,
            away=away,
            kickoff=kickoff,
            market_key=FULL_TIME_RESULT_MARKET,
            source_competition=evidence,
            quotes={
                outcome: _quote(source, event_id, outcome, value, observed_at)
                for outcome, value in odds[source].items()
            },
        )
    return pair


@pytest.fixture
def filtered_client(
    tmp_path: Path, tracked_registry_factory: RegistryFactory
) -> tuple[TestClient, str]:
    """Seeds two TR events (different leagues) and one DE event that ties the
    TR/Süper Lig event on profit but kicks off a day earlier, plus one unmapped
    (never-observed) event that must never surface as an opportunity."""
    db_path = tmp_path / "test_filtered.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    registry = tracked_registry_factory(
        [
            (key, region_id, display_name, {source: key for source in Source})
            for key, region_id, display_name in (
                ("super-lig", "country:TR", "Süper Lig"),
                ("lig1", "country:TR", "1. Lig"),
                ("bundesliga", "country:DE", "Bundesliga"),
            )
        ]
    )

    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)
    now = datetime.now(UTC)

    specs = [
        {
            "competition_id": "super-lig",
            "competition_name": "Süper Lig",
            "country_id": "TR",
            "country_name": "Türkiye",
            "home": "Galatasaray",
            "away": "Fenerbahce",
            "kickoff": now + timedelta(days=2),
            "event_id_prefix": "tr-super",
        },
        {
            "competition_id": "lig1",
            "competition_name": "1. Lig",
            "country_id": "TR",
            "country_name": "Türkiye",
            "home": "Trabzonspor",
            "away": "Konyaspor",
            "kickoff": now + timedelta(days=2),
            "event_id_prefix": "tr-lig1",
        },
        {
            "competition_id": "bundesliga",
            "competition_name": "Bundesliga",
            "country_id": "DE",
            "country_name": "Almanya",
            "home": "Bayern Munih",
            "away": "Borussia Dortmund",
            # earlier kickoff, same odds -> ties on profit with the TR/Süper Lig event
            "kickoff": now + timedelta(days=1),
            "event_id_prefix": "de-bundesliga",
        },
    ]

    source_c_events: list[CollectedEvent] = []
    source_b_events: list[CollectedEvent] = []
    source_a_events: list[CollectedEvent] = []
    for spec in specs:
        pair = _matched_pair(observed_at=now, **spec)  # type: ignore[arg-type]
        source_c_events.append(pair[Source.SOURCE_C])
        source_b_events.append(pair[Source.SOURCE_B])
        source_a_events.append(pair[Source.SOURCE_A])
        for source, event in pair.items():
            assert event.source_competition is not None
            taxonomy_repo.observe(source, event.source_competition, now)

    # Unmapped: never observed by the taxonomy repo, so it stays UNMAPPED and
    # must never produce or surface an opportunity, even though it carries
    # what would otherwise be arbitrage-positive odds.
    unmapped_source_c = CollectedEvent(
        source=Source.SOURCE_C,
        source_event_id="unmapped-source_c",
        competition="Belirsiz Kupa",
        home="Gizli A",
        away="Gizli B",
        kickoff=now + timedelta(days=2),
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_evidence(
            Source.SOURCE_C, "obscure-cup", "Belirsiz Kupa", "TR", "Türkiye"
        ),
        quotes={
            Outcome.HOME: _quote(Source.SOURCE_C, "unmapped-source_c", Outcome.HOME, "2.90", now),
            Outcome.DRAW: _quote(Source.SOURCE_C, "unmapped-source_c", Outcome.DRAW, "3.40", now),
            Outcome.AWAY: _quote(Source.SOURCE_C, "unmapped-source_c", Outcome.AWAY, "2.50", now),
        },
    )
    source_c_events.append(unmapped_source_c)
    unmapped_source_b = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="unmapped-source_b",
        competition="Belirsiz Kupa",
        home="Gizli A",
        away="Gizli B",
        kickoff=now + timedelta(days=2),
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_evidence(
            Source.SOURCE_B, "obscure-cup", "Belirsiz Kupa", "TR", "Türkiye"
        ),
        quotes={
            Outcome.HOME: _quote(Source.SOURCE_B, "unmapped-source_b", Outcome.HOME, "2.60", now),
            Outcome.DRAW: _quote(Source.SOURCE_B, "unmapped-source_b", Outcome.DRAW, "3.60", now),
            Outcome.AWAY: _quote(Source.SOURCE_B, "unmapped-source_b", Outcome.AWAY, "3.10", now),
        },
    )
    source_b_events.append(unmapped_source_b)
    unmapped_source_a = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="unmapped-source_a",
        competition="Belirsiz Kupa",
        home="Gizli A",
        away="Gizli B",
        kickoff=now + timedelta(days=2),
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=_evidence(
            Source.SOURCE_A, "obscure-cup", "Belirsiz Kupa", "TR", "Türkiye"
        ),
        quotes={
            Outcome.HOME: _quote(Source.SOURCE_A, "unmapped-source_a", Outcome.HOME, "2.70", now),
            Outcome.DRAW: _quote(Source.SOURCE_A, "unmapped-source_a", Outcome.DRAW, "3.80", now),
            Outcome.AWAY: _quote(Source.SOURCE_A, "unmapped-source_a", Outcome.AWAY, "2.80", now),
        },
    )
    source_a_events.append(unmapped_source_a)
    # Intentionally never call taxonomy_repo.observe() for the unmapped triple.

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=now, finished_at=now, events=tuple(source_c_events)
        ),
        duration_ms=100,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B, started_at=now, finished_at=now, events=tuple(source_b_events)
        ),
        duration_ms=100,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A, started_at=now, finished_at=now, events=tuple(source_a_events)
        ),
        duration_ms=100,
    )

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


def test_opportunities_sort_order_ties_broken_by_kickoff_then_id(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert resp.status_code == 200
    opps = resp.json()
    assert len(opps) >= 2

    profits = [o["min_adjusted_profit_cents"] for o in opps]
    assert profits == sorted(profits, reverse=True)

    # Galatasaray (TR/Süper Lig, kickoff +2d) and Bayern Munih (DE/Bundesliga,
    # kickoff +1d) share identical odds, so they tie on min_adjusted_profit_cents.
    # The tie must be broken by ascending kickoff: Bayern (earlier) before Galatasaray.
    tr_indexes = [i for i, o in enumerate(opps) if o["home"] == "Galatasaray"]
    de_indexes = [i for i, o in enumerate(opps) if o["home"] == "Bayern Munih"]
    assert tr_indexes and de_indexes
    tr_profit = opps[tr_indexes[0]]["min_adjusted_profit_cents"]
    de_profit = opps[de_indexes[0]]["min_adjusted_profit_cents"]
    assert tr_profit == de_profit
    assert de_indexes[0] < tr_indexes[0]


def test_opportunities_never_surface_unmapped_events(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert resp.status_code == 200
    opps = resp.json()
    homes = {o["home"] for o in opps}
    assert "Gizli A" not in homes
    assert all(o["country_id"] is not None for o in opps)


def test_opportunity_detail_404_outside_the_tracked_catalog(
    filtered_client: tuple[TestClient, str],
) -> None:
    """The unmapped fixture event is outside the tracked scope, so it has no detail."""
    c, _ = filtered_client
    events_resp = c.get("/api/v1/events?time_scope=all")
    assert events_resp.status_code == 200
    assert all(ev["home"] != "Gizli A" for ev in events_resp.json())

    detail_resp = c.get("/api/v1/opportunities/not-a-matched-id")
    assert detail_resp.status_code == 404


def test_opportunities_country_id_filter_is_or_within_dimension(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")
    de_country_id = next(o["country_id"] for o in baseline if o["home"] == "Bayern Munih")
    assert tr_country_id != de_country_id

    resp = c.get(
        "/api/v1/opportunities",
        params={
            "budget_cents": 100000,
            "positive_only": "false",
            "country_id": [tr_country_id, de_country_id],
        },
    )
    assert resp.status_code == 200
    homes = {o["home"] for o in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih"}


def test_opportunities_country_and_league_filters_anded_across_dimensions(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")
    de_league_id = next(o["league_id"] for o in baseline if o["home"] == "Bayern Munih")

    resp = c.get(
        "/api/v1/opportunities",
        params={
            "budget_cents": 100000,
            "positive_only": "false",
            "country_id": tr_country_id,
            "league_id": de_league_id,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_opportunities_invalid_filter_id_returns_empty_without_crash(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    resp = c.get(
        "/api/v1/opportunities",
        params={"budget_cents": 100000, "positive_only": "false", "country_id": "does-not-exist"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_dashboard_summary(client: tuple[TestClient, str]) -> None:
    c, _ = client
    resp = c.get("/api/v1/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_opportunities_count" in data
    assert "positive_opportunities_count" in data
    assert "best_margin_percent" in data
    assert "sources_health" in data
    assert "overall_status" in data


# --- Saved default filters: omitted-vs-explicit-override behavior ---------


@pytest.fixture
def second_filtered_client(
    filtered_client: tuple[TestClient, str], tmp_path: Path
) -> tuple[TestClient, str]:
    """A second real user/session sharing `filtered_client`'s database, so
    isolation assertions prove a saved filter write stayed scoped to its
    owner rather than merely matching a response shape."""
    c, _csrf = filtered_client
    db_path = tmp_path / "test_filtered.db"
    auth = AuthRepository(db_path)
    user_b, session_b = auth.register_user("bob", "correct-horse-battery-staple")
    UserPreferencesRepository(db_path).update(
        user_b.user_id, UserPreferencesUpdate(tracked_competitions_only=False)
    )
    client_b = TestClient(c.app)
    client_b.cookies.set("session_token", session_b.token)
    return client_b, session_b.csrf_token


def test_omitted_filters_load_the_users_saved_opportunities_scope_default(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, csrf = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")

    put_resp = c.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )
    assert put_resp.status_code == 200

    resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert resp.status_code == 200
    homes = {o["home"] for o in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor"}


def test_use_saved_filters_false_with_cleared_filters_shows_everything(
    filtered_client: tuple[TestClient, str],
) -> None:
    """A plain query string cannot distinguish an explicitly-cleared filter
    list from an omitted one (`appendMany` sends nothing for `[]`), so a
    client clearing all filters must be able to say "don't fall back to my
    saved filter" via `use_saved_filters=false`. With it set, even a fully
    omitted country_id/league_id must be treated as an explicit empty
    override (show everything), never as "use my saved filter" -- proven
    here against a saved TR-only filter that would otherwise narrow results."""
    c, csrf = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # No country_id/league_id query params, but use_saved_filters=false --
    # must show everything, not fall back to the saved TR-only filter.
    resp = c.get(
        "/api/v1/opportunities",
        params={
            "budget_cents": 100000,
            "positive_only": "false",
            "use_saved_filters": "false",
        },
    )
    assert resp.status_code == 200
    homes = {o["home"] for o in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih"}

    # Sanity: the saved filter is untouched and still applies on a plain request.
    plain_resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert {o["home"] for o in plain_resp.json()} == {"Galatasaray", "Trabzonspor"}


def test_query_override_does_not_persist_and_a_later_plain_request_reverts_to_saved(
    filtered_client: tuple[TestClient, str],
) -> None:
    """The core non-persistence invariant: a one-off query override must never
    be written back to the saved filter, no matter what it was set to."""
    c, csrf = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")
    de_country_id = next(o["country_id"] for o in baseline if o["home"] == "Bayern Munih")

    c.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # A request-time override to DE only -- must not be persisted.
    override_resp = c.get(
        "/api/v1/opportunities",
        params={
            "budget_cents": 100000,
            "positive_only": "false",
            "country_id": [de_country_id],
        },
    )
    assert {o["home"] for o in override_resp.json()} == {"Bayern Munih"}

    # The saved filter itself must still read back as the original TR selection.
    saved = c.get("/api/v1/preferences/filters/opportunities").json()
    assert saved["country_ids"] == [tr_country_id]

    # A subsequent plain request (no query params) reverts to the ORIGINAL
    # saved (TR) filter, not the override.
    plain_resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert {o["home"] for o in plain_resp.json()} == {"Galatasaray", "Trabzonspor"}


def test_saved_opportunities_filter_is_isolated_per_user(
    filtered_client: tuple[TestClient, str],
    second_filtered_client: tuple[TestClient, str],
) -> None:
    c, csrf = filtered_client
    c_b, _csrf_b = second_filtered_client

    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    resp_a = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert {o["home"] for o in resp_a.json()} == {"Galatasaray", "Trabzonspor"}

    # User B has no saved filter of their own -- their plain request must
    # still see everything, proving the saved filter never leaked cross-user.
    resp_b = c_b.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    homes_b = {o["home"] for o in resp_b.json()}
    assert homes_b == {"Galatasaray", "Trabzonspor", "Bayern Munih"}


def test_saved_filter_scope_is_opportunities_only_not_shared_with_events(
    filtered_client: tuple[TestClient, str],
) -> None:
    """A filter saved for the `opportunities` scope must never narrow the
    `opportunities` endpoint via the `events` scope's stored rows."""
    c, csrf = filtered_client
    baseline = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    tr_country_id = next(o["country_id"] for o in baseline if o["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    resp = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    homes = {o["home"] for o in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih"}


def test_opportunity_detail_only_scores_the_requested_match(
    filtered_client: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detail route must not score every event to find one opportunity.

    `opportunity_id` carries its `matched_id` as a prefix, so the route can
    narrow to a single event before doing any work.
    """
    c, _ = filtered_client
    listing = c.get("/api/v1/opportunities?budget_cents=100000&positive_only=false")
    assert listing.status_code == 200
    scorable = {opp["matched_id"]: opp["opportunity_id"] for opp in listing.json()}
    assert len(scorable) > 1

    # Pick the match the route reaches LAST, so a full scan is distinguishable
    # from a targeted lookup.
    events = c.get("/api/v1/events?time_scope=all")
    assert events.status_code == 200
    order = [ev["matched_id"] for ev in events.json() if ev["matched_id"] in scorable]
    assert len(order) > 1
    target = scorable[order[-1]]

    from surebet.web.routes import opportunities as route_module

    scored: list[str] = []
    original = route_module.calculate_event_opportunities

    def counting(**kwargs):  # type: ignore[no-untyped-def]
        scored.append(kwargs["event"].matched_id)
        return original(**kwargs)

    monkeypatch.setattr(route_module, "calculate_event_opportunities", counting)

    detail = c.get(f"/api/v1/opportunities/{target}?budget_cents=100000")
    assert detail.status_code == 200
    assert detail.json()["opportunity_id"] == target
    assert scored == [target.split(":", 1)[0]]


def test_opportunities_returns_etag_and_304_on_match(
    filtered_client: tuple[TestClient, str],
) -> None:
    c, _ = filtered_client
    resp = c.get("/api/v1/opportunities")
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None
    assert etag.startswith('W/"opp-')

    # Second request with matching If-None-Match should return 304 Not Modified
    cached_resp = c.get("/api/v1/opportunities", headers={"if-none-match": etag})
    assert cached_resp.status_code == 304
    assert cached_resp.text == ""


def test_list_opportunities_filters_by_sport(
    tmp_path: Path, tracked_registry_factory: RegistryFactory
) -> None:
    db_path = tmp_path / "test_sport.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    registry = tracked_registry_factory(
        [
            ("super-lig", "country:TR", "Super Lig", {source: "super-lig" for source in Source}),
            (
                "euroleague",
                "region:international",
                "Euroleague",
                {source: "euroleague" for source in Source},
            ),
        ]
    )

    now = datetime.now(UTC)
    kickoff = now + timedelta(days=1)

    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)

    # 1. Football match: Super Lig (Galatasaray vs Fenerbahce)
    fb_pair = _matched_pair(
        competition_id="super-lig",
        competition_name="Super Lig",
        country_id="TR",
        country_name="Türkiye",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=kickoff,
        event_id_prefix="fb",
        observed_at=now,
    )
    for src, ev in fb_pair.items():
        snapshot_repo.save_batch(
            CollectionBatch(source=src, started_at=now, finished_at=now, events=(ev,)),
            duration_ms=100,
        )
        assert ev.source_competition is not None
        taxonomy_repo.observe(src, ev.source_competition, now)

    # 2. Basketball match: Euroleague (Real Madrid vs Barcelona)
    bask_source_b = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="bb-source_b",
        sport=Sport.BASKETBALL,
        competition="Euroleague",
        home="Real Madrid",
        away="Barcelona",
        kickoff=kickoff,
        market_key="BASKETBALL_MONEYLINE",
        source_competition=_evidence(
            Source.SOURCE_B, "euroleague", "Euroleague", "INT", "International"
        ),
        bask_ml_quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_B,
                event_id="bb-source_b",
                outcome=Outcome.HOME,
                odds=Decimal("2.10"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_B,
                event_id="bb-source_b",
                outcome=Outcome.AWAY,
                odds=Decimal("1.80"),
                observed_at=now,
            ),
        },
    )
    bask_source_a = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="bb-source_a",
        sport=Sport.BASKETBALL,
        competition="Euroleague",
        home="Real Madrid",
        away="Barcelona",
        kickoff=kickoff,
        market_key="BASKETBALL_MONEYLINE",
        source_competition=_evidence(
            Source.SOURCE_A, "euroleague", "Euroleague", "INT", "International"
        ),
        bask_ml_quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_A,
                event_id="bb-source_a",
                outcome=Outcome.HOME,
                odds=Decimal("1.70"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_A,
                event_id="bb-source_a",
                outcome=Outcome.AWAY,
                odds=Decimal("2.25"),
                observed_at=now,
            ),
        },
    )
    for ev in (bask_source_b, bask_source_a):
        snapshot_repo.save_batch(
            CollectionBatch(source=ev.source, started_at=now, finished_at=now, events=(ev,)),
            duration_ms=100,
        )
        assert ev.source_competition is not None
        taxonomy_repo.observe(ev.source, ev.source_competition, now)

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)

    # All opportunities
    all_resp = c.get("/api/v1/opportunities?budget_cents=100000")
    assert all_resp.status_code == 200
    all_opps = all_resp.json()
    sports_present = {o["sport"] for o in all_opps}
    assert "football" in sports_present
    assert "basketball" in sports_present

    # Football filter
    fb_resp = c.get("/api/v1/opportunities?budget_cents=100000&sport=football")
    assert fb_resp.status_code == 200
    fb_opps = fb_resp.json()
    assert len(fb_opps) >= 1
    assert all(o["sport"] == "football" for o in fb_opps)

    # Basketball filter
    bb_resp = c.get("/api/v1/opportunities?budget_cents=100000&sport=basketball")
    assert bb_resp.status_code == 200
    bb_opps = bb_resp.json()
    assert len(bb_opps) >= 1
    assert all(o["sport"] == "basketball" for o in bb_opps)
