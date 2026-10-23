"""End-to-End integration tests for the FastAPI web server with full scenario calculations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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


def _populate_realistic_events(db_path: Path, registry: TrackedCompetitionRegistry) -> None:
    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)
    now = datetime.now(UTC)
    kickoff = now + timedelta(days=2)

    # Event 1: Source C + Source B + Source A matched event with positive cashback arbitrage
    # Source C: H: 2.85, D: 3.40, A: 2.50
    # Source B: H: 2.60, D: 3.65, A: 3.15
    # Source A: H: 2.95, D: 3.20, A: 2.45
    source_c_ev = CollectedEvent(
        source=Source.SOURCE_C,
        source_event_id="m_super_1",
        competition="Trendyol Super Lig",
        home="Besiktas",
        away="Trabzonspor",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=SourceCompetitionEvidence(
            provider=Source.SOURCE_C.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id="super-lig",
            competition_name="Trendyol Super Lig",
            country_id="TR",
            country_name="Türkiye",
            kind=CompetitionKind.LEAGUE,
        ),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_C,
                event_id="m_super_1",
                outcome=Outcome.HOME,
                odds=Decimal("2.85"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_C,
                event_id="m_super_1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.40"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_C,
                event_id="m_super_1",
                outcome=Outcome.AWAY,
                odds=Decimal("2.50"),
                observed_at=now,
            ),
        },
    )

    source_b_ev = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="j_super_1",
        competition="Trendyol Super Lig",
        home="Besiktas",
        away="Trabzonspor",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=SourceCompetitionEvidence(
            provider=Source.SOURCE_B.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id="super-lig",
            competition_name="Trendyol Super Lig",
            country_id="TR",
            country_name="Türkiye",
            kind=CompetitionKind.LEAGUE,
        ),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_B,
                event_id="j_super_1",
                outcome=Outcome.HOME,
                odds=Decimal("2.60"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_B,
                event_id="j_super_1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.65"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_B,
                event_id="j_super_1",
                outcome=Outcome.AWAY,
                odds=Decimal("3.15"),
                observed_at=now,
            ),
        },
    )

    source_a_ev = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="s_super_1",
        competition="Trendyol Super Lig",
        home="Besiktas",
        away="Trabzonspor",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=SourceCompetitionEvidence(
            provider=Source.SOURCE_A.value,
            identity_kind=SourceIdentityKind.PROVIDER_ID,
            competition_id="super-lig",
            competition_name="Trendyol Super Lig",
            country_id="TR",
            country_name="Türkiye",
            kind=CompetitionKind.LEAGUE,
        ),
        quotes={
            Outcome.HOME: Quote(
                source=Source.SOURCE_A,
                event_id="s_super_1",
                outcome=Outcome.HOME,
                odds=Decimal("3.60"),
                observed_at=now,
            ),
            Outcome.DRAW: Quote(
                source=Source.SOURCE_A,
                event_id="s_super_1",
                outcome=Outcome.DRAW,
                odds=Decimal("3.20"),
                observed_at=now,
            ),
            Outcome.AWAY: Quote(
                source=Source.SOURCE_A,
                event_id="s_super_1",
                outcome=Outcome.AWAY,
                odds=Decimal("2.45"),
                observed_at=now,
            ),
        },
    )

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=now, finished_at=now, events=(source_c_ev,)
        ),
        duration_ms=180,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B, started_at=now, finished_at=now, events=(source_b_ev,)
        ),
        duration_ms=210,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A, started_at=now, finished_at=now, events=(source_a_ev,)
        ),
        duration_ms=320,
    )
    for event in (source_c_ev, source_b_ev, source_a_ev):
        assert event.source_competition is not None
        taxonomy_repo.observe(event.source, event.source_competition, now)


def test_full_web_application_e2e_flow(
    tmp_path: Path, tracked_registry_factory: RegistryFactory
) -> None:
    db_path = tmp_path / "e2e.db"
    init_db(db_path)
    # This flow's own tracked scope: one Turkish competition on every source.
    registry = tracked_registry_factory(
        [("super-lig", "country:TR", "Super Lig", {source: "super-lig" for source in Source})]
    )
    _populate_realistic_events(db_path, registry)

    dist_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
    app = create_app(
        db_path=db_path,
        static_dir=dist_dir if dist_dir.exists() else None,
        tracked_registry=registry,
    )
    client = TestClient(app)

    # 1. Unauthenticated /api/v1/auth/me returns authenticated=False; no
    # registration code was configured, so registration stays closed.
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["authenticated"] is False
    assert me_res.json()["registration_enabled"] is False

    # 2. Create the account directly (registration is closed in this test)
    # and log in with username + password.
    _user, _initial_session = AuthRepository(db_path).register_user(
        "admin", "secure_admin_pass_123"
    )
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secure_admin_pass_123"},
    )
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    assert len(csrf_token) > 10

    # Verify session cookie was set
    assert "session_token" in client.cookies

    # 3. GET /api/v1/dashboard returns live overall status and positive count
    dash_res = client.get("/api/v1/dashboard")
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert dash_data["overall_status"] == "live"
    assert dash_data["positive_opportunities_count"] >= 1
    assert "source_c" in dash_data["sources_health"]
    assert "source_b" in dash_data["sources_health"]
    assert "source_a" in dash_data["sources_health"]

    # 4. GET /api/v1/opportunities with default 1000 TL (100,000 cents)
    opps_res = client.get("/api/v1/opportunities?budget_cents=100000")
    assert opps_res.status_code == 200
    opps = opps_res.json()
    assert len(opps) >= 1

    opp = opps[0]
    assert opp["home"] == "Besiktas"
    assert opp["away"] == "Trabzonspor"
    assert opp["budget_cents"] == 100000

    # Check that stakes add up exactly to 100,000 kuruş
    selections = opp["selections"]
    total_stake = sum(selections[k]["stake_cents"] for k in ["HOME", "DRAW", "AWAY"])
    assert total_stake == 100000

    # Check verified integer profits for all 3 scenarios
    scenarios = opp["scenarios"]
    for outcome_key in ["HOME", "DRAW", "AWAY"]:
        sc = scenarios[outcome_key]
        assert sc["net_profit_cents"] > 0
        assert sc["gross_payout_cents"] + sc["cashback_cents"] - 100000 == sc["net_profit_cents"]

    # 5. Test optional Source A switch on
    source_a_opps_res = client.get(
        "/api/v1/opportunities?budget_cents=100000&include_source_a_match_sides=true"
    )
    assert source_a_opps_res.status_code == 200
    source_a_opps = source_a_opps_res.json()
    assert len(source_a_opps) >= 1
    source_a_opp = source_a_opps[0]
    # In this fixture Source A HOME is 2.95 (better than Source C 2.85)
    assert source_a_opp["selections"]["HOME"]["source"] == "source_a"
    assert source_a_opp["selections"]["HOME"]["cashback_eligible"] is False

    # 6. GET /api/v1/events (kickoff is 2 days out, so use time_scope=all --
    # the default "today" scope is exercised by tests/unit/web/test_api_events.py)
    events_res = client.get("/api/v1/events?time_scope=all")
    assert events_res.status_code == 200
    events = events_res.json()
    assert len(events) >= 1

    # 7. GET /api/v1/sources/health
    sources_res = client.get("/api/v1/sources/health")
    assert sources_res.status_code == 200
    sources = sources_res.json()
    assert sources["source_c"]["status"] == "disabled"
    assert sources["source_b"]["event_count"] == 1
    assert sources["source_a"]["event_count"] == 1

    # 8. GET /api/v1/settings returns both the caller's personal preferences
    # and the shared system settings; PUT /api/v1/settings/personal only ever
    # touches the caller's own row.
    combined_res = client.get("/api/v1/settings")
    assert combined_res.status_code == 200
    combined = combined_res.json()
    assert "personal" in combined
    assert "system" in combined

    settings_res = client.put(
        "/api/v1/settings/personal",
        json={"default_budget_cents": 250000, "include_source_a_match_sides": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert settings_res.status_code == 200
    updated = settings_res.json()
    assert updated["default_budget_cents"] == 250000
    assert updated["include_source_a_match_sides"] is True

    # 9. Test static files mounting if dist exists
    if dist_dir.exists():
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "<!doctype html>" in index_res.text
