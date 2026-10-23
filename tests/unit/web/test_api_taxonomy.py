"""Tests for the authenticated /api/v1/countries and /api/v1/leagues catalog APIs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import CompetitionKind, SourceCompetitionEvidence, SourceIdentityKind
from surebet.taxonomy.tracked import TrackedCompetitionRegistry
from surebet.web.app import create_app
from tests.conftest import RegistryFactory


def _evidence(
    source: Source, competition_id: str, competition_name: str, country_name: str
) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id=competition_id,
        competition_name=competition_name,
        country_name=country_name,
        kind=CompetitionKind.LEAGUE,
    )


def _event(
    source: Source,
    event_id: str,
    competition: str,
    home: str,
    away: str,
    odds: tuple[str, str, str],
    evidence: SourceCompetitionEvidence | None,
    observed_at: datetime,
) -> CollectedEvent:
    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition=competition,
        home=home,
        away=away,
        kickoff=observed_at + timedelta(days=1),
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=evidence,
        quotes={
            Outcome.HOME: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.HOME,
                odds=Decimal(odds[0]),
                observed_at=observed_at,
            ),
            Outcome.DRAW: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.DRAW,
                odds=Decimal(odds[1]),
                observed_at=observed_at,
            ),
            Outcome.AWAY: Quote(
                source=source,
                event_id=event_id,
                outcome=Outcome.AWAY,
                odds=Decimal(odds[2]),
                observed_at=observed_at,
            ),
        },
    )


def _seed(db_path: Path, registry: TrackedCompetitionRegistry | None = None) -> None:
    """Seed a positive Germany/Bundesliga event, a break-even Turkey/Süper Lig event, an
    International/Champions League event, and one unmapped orphan event that must never
    inflate any country's or league's opportunity_count."""
    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)
    observed_at = datetime.now(UTC)

    de_source_c = _evidence(Source.SOURCE_C, "bl", "Bundesliga", "Germany")
    de_source_b = _evidence(Source.SOURCE_B, "bl", "Bundesliga", "Germany")
    taxonomy_repo.observe(Source.SOURCE_C, de_source_c, observed_at)
    taxonomy_repo.observe(Source.SOURCE_B, de_source_b, observed_at)
    de_source_c_ev = _event(
        Source.SOURCE_C,
        "m_de",
        "Bundesliga",
        "Bayern",
        "Dortmund",
        ("2.85", "3.40", "2.50"),
        de_source_c,
        observed_at,
    )
    de_source_b_ev = _event(
        Source.SOURCE_B,
        "j_de",
        "Bundesliga",
        "Bayern",
        "Dortmund",
        ("2.60", "3.65", "3.15"),
        de_source_b,
        observed_at,
    )

    tr_source_c = _evidence(Source.SOURCE_C, "sl", "Süper Lig", "Türkiye")
    tr_source_b = _evidence(Source.SOURCE_B, "sl", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_C, tr_source_c, observed_at)
    taxonomy_repo.observe(Source.SOURCE_B, tr_source_b, observed_at)
    tr_source_c_ev = _event(
        Source.SOURCE_C,
        "m_tr",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.00", "3.20", "3.50"),
        tr_source_c,
        observed_at,
    )
    tr_source_b_ev = _event(
        Source.SOURCE_B,
        "j_tr",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.00", "3.20", "3.50"),
        tr_source_b,
        observed_at,
    )

    # An opportunity must span two independent operators, so both the positive Germany event
    # and the break-even Turkey event need a Source A leg to keep testing what they test:
    # Germany that a positive count is aggregated, Turkey that a non-positive event counts zero.
    de_source_a = _evidence(Source.SOURCE_A, "bl", "Bundesliga", "Germany")
    taxonomy_repo.observe(Source.SOURCE_A, de_source_a, observed_at)
    de_source_a_ev = _event(
        Source.SOURCE_A,
        "s_de",
        "Bundesliga",
        "Bayern",
        "Dortmund",
        ("2.70", "3.70", "2.90"),
        de_source_a,
        observed_at,
    )

    tr_source_a = _evidence(Source.SOURCE_A, "sl", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_A, tr_source_a, observed_at)
    tr_source_a_ev = _event(
        Source.SOURCE_A,
        "s_tr",
        "Süper Lig",
        "Galatasaray",
        "Fenerbahce",
        ("2.00", "3.20", "3.50"),
        tr_source_a,
        observed_at,
    )

    intl_source_c = _evidence(Source.SOURCE_C, "ucl", "Champions League", "International")
    taxonomy_repo.observe(Source.SOURCE_C, intl_source_c, observed_at)
    intl_ev = _event(
        Source.SOURCE_C,
        "m_intl",
        "Champions League",
        "Real Madrid",
        "Man City",
        ("2.0", "3.0", "3.5"),
        intl_source_c,
        observed_at,
    )

    orphan_evidence = _evidence(Source.SOURCE_A, "orphan", "Mystery Cup", "Nowhereland")
    orphan_event = _event(
        Source.SOURCE_A,
        "s_orphan",
        "Mystery Cup",
        "Team A",
        "Team B",
        ("2.0", "3.0", "3.0"),
        orphan_evidence,
        observed_at,
    )

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=observed_at,
            finished_at=observed_at,
            events=(de_source_c_ev, tr_source_c_ev, intl_ev),
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B,
            started_at=observed_at,
            finished_at=observed_at,
            events=(de_source_b_ev, tr_source_b_ev),
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=observed_at,
            finished_at=observed_at,
            events=(de_source_a_ev, tr_source_a_ev, orphan_event),
        )
    )


@pytest.fixture
def client(tmp_path: Path, tracked_registry_factory: RegistryFactory) -> tuple[TestClient, str]:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    # These tests cover country/league listing shape, so they declare their own
    # tracked scope rather than borrowing the packaged league list. The "orphan"
    # competition is deliberately left out: it must never inflate any count.
    registry = tracked_registry_factory(
        [
            (key, region_id, display_name, {source: key for source in Source})
            for key, region_id, display_name in (
                ("bl", "country:DE", "Bundesliga"),
                ("sl", "country:TR", "Süper Lig"),
                ("ucl", "region:international", "Champions League"),
            )
        ]
    )
    _seed(db_path, registry)

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


def test_countries_requires_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    _seed(db_path)

    app = create_app(db_path=db_path)
    c = TestClient(app)

    assert c.get("/api/v1/countries").status_code == 401
    assert c.get("/api/v1/countries?full_catalog=true").status_code == 401


def test_leagues_requires_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    _seed(db_path)

    app = create_app(db_path=db_path)
    c = TestClient(app)

    resp = c.get("/api/v1/leagues?full_catalog=true")
    assert resp.status_code == 401


def _scoped_client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "scoped.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    observed_at = datetime.now(UTC)
    tracked = _evidence(
        Source.SOURCE_C,
        "910000000000000106",
        "Almanya Bundesliga 2026/2027",
        "Germany",
    )
    untracked = _evidence(Source.SOURCE_C, "local-cup", "Local Cup", "France")
    taxonomy_repo = TaxonomyRepository(db_path)
    taxonomy_repo.observe(Source.SOURCE_C, tracked, observed_at)
    taxonomy_repo.observe(Source.SOURCE_C, untracked, observed_at)
    SnapshotRepository(db_path).save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=observed_at,
            finished_at=observed_at,
            events=(
                _event(
                    Source.SOURCE_C,
                    "tracked-event",
                    tracked.competition_name,
                    "Bayern",
                    "Dortmund",
                    ("2.0", "3.0", "3.5"),
                    tracked,
                    observed_at,
                ),
                _event(
                    Source.SOURCE_C,
                    "untracked-event",
                    untracked.competition_name,
                    "Home",
                    "Away",
                    ("2.0", "3.0", "3.5"),
                    untracked,
                    observed_at,
                ),
            ),
        )
    )
    app = create_app(db_path=db_path)
    scoped_client = TestClient(app)
    scoped_client.cookies.set("session_token", session.token)
    return scoped_client


def test_default_tracked_scope_limits_country_and_league_filters(tmp_path: Path) -> None:
    c = _scoped_client(tmp_path)

    countries = c.get("/api/v1/countries")
    leagues = c.get("/api/v1/leagues")

    assert countries.status_code == 200
    assert leagues.status_code == 200
    assert [(row["display_name"], row["league_count"]) for row in countries.json()] == [
        ("Almanya", 1)
    ]
    assert [row["display_name"] for row in leagues.json()] == ["Almanya Bundesliga"]


def test_leagues_full_catalog_keeps_manual_mapping_targets_available(
    tmp_path: Path,
) -> None:
    c = _scoped_client(tmp_path)

    response = c.get("/api/v1/leagues?full_catalog=true")

    assert response.status_code == 200
    assert {row["display_name"] for row in response.json()} == {
        "Almanya Bundesliga",
        "Local Cup",
    }


def test_countries_full_catalog_keeps_manual_mapping_country_targets_available(
    tmp_path: Path,
) -> None:
    c = _scoped_client(tmp_path)

    countries = c.get("/api/v1/countries")
    full_catalog = c.get("/api/v1/countries?full_catalog=true")

    assert countries.status_code == 200
    assert full_catalog.status_code == 200
    assert [row["display_name"] for row in countries.json()] == ["Almanya"]
    assert {row["display_name"] for row in full_catalog.json()} == {"Almanya", "Fransa"}


def test_countries_lists_international_first_then_turkish_alphabetical(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    resp = c.get("/api/v1/countries")
    assert resp.status_code == 200
    data = resp.json()

    names = [row["display_name"] for row in data]
    assert names[0] == "Uluslararası"
    # "Almanya" (Germany) sorts before "Türkiye" (Turkey) in Turkish alphabetical order.
    assert names.index("Almanya") < names.index("Türkiye")

    by_name = {row["display_name"]: row for row in data}
    germany = by_name["Almanya"]
    assert germany["region_class"] == "country"
    assert germany["iso_code"] == "DE"
    assert germany["league_count"] == 1
    assert germany["event_count"] == 1
    # Cross-operator assignments + single-brand WAMP feed assignments yield 5 positive opportunities.
    assert germany["opportunity_count"] == 5
    assert germany["freshest_at"] is not None

    turkey = by_name["Türkiye"]
    assert turkey["event_count"] == 1
    # 2.00/3.20/3.50 vs 2.00/3.20/3.50 is not a positive cashback arbitrage.
    assert turkey["opportunity_count"] == 0

    intl = by_name["Uluslararası"]
    assert intl["league_count"] == 1
    assert intl["event_count"] == 1

    # The unmapped "Nowhereland" orphan event must never surface as a country here.
    assert "Nowhereland" not in by_name


def test_countries_uses_turkish_collation_not_codepoint_order(
    tmp_path: Path, tracked_registry_factory: RegistryFactory
) -> None:
    """ "Çekya" (Czechia) must sort in Turkish-alphabet position -- between "Cezayir"
    (Algeria, C) and "Danimarka" (Denmark, D) -- not after "Zimbabve" as plain
    str.casefold() (Unicode codepoint order) would place it, since Ç (U+00C7) sorts
    after every plain ASCII letter under codepoint comparison."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    countries = [
        ("dz", "Algeria"),
        ("cz", "Czechia"),
        ("dk", "Denmark"),
        ("zw", "Zimbabwe"),
    ]
    registry = tracked_registry_factory(
        [
            (code, f"country:{code.upper()}", f"{country_name} League", {Source.SOURCE_C: code})
            for code, country_name in countries
        ]
    )

    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)
    observed_at = datetime.now(UTC)
    events = []
    for code, country_name in countries:
        competition_name = f"{country_name} League"
        evidence = _evidence(Source.SOURCE_C, code, competition_name, country_name)
        taxonomy_repo.observe(Source.SOURCE_C, evidence, observed_at)
        events.append(
            _event(
                Source.SOURCE_C,
                f"m_{code}",
                competition_name,
                "Home",
                "Away",
                ("2.0", "3.0", "3.5"),
                evidence,
                observed_at,
            )
        )

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=observed_at,
            finished_at=observed_at,
            events=tuple(events),
        )
    )

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)

    resp = c.get("/api/v1/countries")
    assert resp.status_code == 200
    names = [row["display_name"] for row in resp.json()]

    assert {"Cezayir", "Çekya", "Danimarka", "Zimbabve"} <= set(names)
    assert (
        names.index("Cezayir")
        < names.index("Çekya")
        < names.index("Danimarka")
        < names.index("Zimbabve")
    )


def test_leagues_lists_stable_names_with_counts_and_freshness(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    resp = c.get("/api/v1/leagues")
    assert resp.status_code == 200
    data = resp.json()

    names = [row["display_name"] for row in data]
    assert names == sorted(names, key=str.casefold)

    by_name = {row["display_name"]: row for row in data}
    bundesliga = by_name["Bundesliga"]
    # Registry-linked leagues are created with CompetitionKind.UNKNOWN: the
    # curated registry carries no kind, unlike name-based auto-mapping.
    assert bundesliga["kind"] == "unknown"
    assert bundesliga["active"] is True
    assert bundesliga["event_count"] == 1
    assert bundesliga["opportunity_count"] == 5
    assert bundesliga["freshest_at"] is not None

    sl = by_name["Süper Lig"]
    assert sl["opportunity_count"] == 0

    assert "Mystery Cup" not in by_name


def test_leagues_filters_by_repeated_country_id(client: tuple[TestClient, str]) -> None:
    c, _ = client
    countries_resp = c.get("/api/v1/countries")
    by_name = {row["display_name"]: row for row in countries_resp.json()}
    germany_id = by_name["Almanya"]["country_id"]

    resp = c.get(f"/api/v1/leagues?country_id={germany_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["display_name"] == "Bundesliga"
    assert data[0]["country_id"] == germany_id


def test_leagues_pagination_is_bounded_and_capped_at_200(client: tuple[TestClient, str]) -> None:
    c, _ = client

    resp = c.get("/api/v1/leagues?limit=10000")
    assert resp.status_code == 200
    assert len(resp.json()) <= 200

    first_page = c.get("/api/v1/leagues?limit=2&offset=0").json()
    second_page = c.get("/api/v1/leagues?limit=2&offset=2").json()
    assert len(first_page) == 2
    all_ids = [row["league_id"] for row in first_page] + [row["league_id"] for row in second_page]
    assert len(all_ids) == len(set(all_ids))
