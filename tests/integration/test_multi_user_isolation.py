"""End-to-end, full-stack proof that two independently authenticated users
never leak personal state into each other, while shared/system state stays
common to both -- Task 10's completion gate for the whole multi-user
migration (Tasks 1-9).

Follows the house style of `test_web_app_full.py`: one fresh temp SQLite
database, `TestClient` instances built from `create_app(db_path=...)`, and
real registration/login HTTP calls rather than shortcuts, so a bug in the
actual wiring (route -> repository -> schema) would be caught here even if
each task's own unit tests pass in isolation.

Also covers Step 2 of the Task 10 brief: fake-registration-code secrecy
across HTTP responses, logs, the raw SQLite file (scanned per text column,
not just as a byte blob), and -- after a real `npm run build` -- the built
frontend/dist output.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.domain.models import Quote
from surebet.notifications.models import (
    CardState,
    DualPolicyEvaluation,
    LifecycleStatus,
    OutboxActionDraft,
    OutboxActionType,
    card_key,
)
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.db import get_connection, init_db
from surebet.storage.notifications import NotificationRepository
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import CompetitionKind, SourceCompetitionEvidence, SourceIdentityKind
from surebet.taxonomy.tracked import TrackedCompetitionRegistry
from surebet.web.app import create_app
from tests.conftest import RegistryFactory

FAKE_REGISTRATION_CODE = "test-registration-code"
PASSWORD_A = "correct-horse-battery-staple-a"
PASSWORD_B = "correct-horse-battery-staple-b"
NOW_ISO = "2026-09-01T12:00:00+00:00"


# --------------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------------


def _populate_one_matched_event(db_path: Path, registry: TrackedCompetitionRegistry) -> None:
    """One Source C+Source B+Source A event under Turkey/"Trendyol Super Lig"
    with a positive cashback arbitrage, mirroring `test_web_app_full.py`'s
    fixture. All three sources are needed: Source C and Source B are both
    books on the shared WAMP backend (`operator_group` collapses them to one
    group), so a valid two-leg-minimum arbitrage combination requires
    Source A's distinct operator group too -- two sources alone never clear
    `calculate_event_opportunities`'s single-operator-group filter.
    """
    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)
    now = datetime.now(UTC)
    kickoff = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if kickoff <= now:
        from datetime import timedelta

        kickoff += timedelta(days=1)

    def _event(
        source: Source, source_event_id: str, home_odds: str, draw_odds: str, away_odds: str
    ) -> CollectedEvent:
        return CollectedEvent(
            source=source,
            source_event_id=source_event_id,
            competition="Trendyol Super Lig",
            home="Besiktas",
            away="Trabzonspor",
            kickoff=kickoff,
            market_key=FULL_TIME_RESULT_MARKET,
            source_competition=SourceCompetitionEvidence(
                provider=source.value,
                identity_kind=SourceIdentityKind.PROVIDER_ID,
                competition_id="super-lig",
                competition_name="Trendyol Super Lig",
                country_id="TR",
                country_name="Türkiye",
                kind=CompetitionKind.LEAGUE,
            ),
            quotes={
                Outcome.HOME: Quote(
                    source=source,
                    event_id=source_event_id,
                    outcome=Outcome.HOME,
                    odds=Decimal(home_odds),
                    observed_at=now,
                ),
                Outcome.DRAW: Quote(
                    source=source,
                    event_id=source_event_id,
                    outcome=Outcome.DRAW,
                    odds=Decimal(draw_odds),
                    observed_at=now,
                ),
                Outcome.AWAY: Quote(
                    source=source,
                    event_id=source_event_id,
                    outcome=Outcome.AWAY,
                    odds=Decimal(away_odds),
                    observed_at=now,
                ),
            },
        )

    source_c_ev = _event(Source.SOURCE_C, "m_super_1", "2.85", "3.40", "2.50")
    source_b_ev = _event(Source.SOURCE_B, "j_super_1", "2.60", "3.65", "3.15")
    source_a_ev = _event(Source.SOURCE_A, "s_super_1", "3.60", "3.20", "2.45")

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


class TwoUsers:
    def __init__(
        self,
        db_path: Path,
        client_a: TestClient,
        csrf_a: str,
        user_id_a: int,
        client_b: TestClient,
        csrf_b: str,
        user_id_b: int,
    ) -> None:
        self.db_path = db_path
        self.client_a = client_a
        self.csrf_a = csrf_a
        self.user_id_a = user_id_a
        self.client_b = client_b
        self.csrf_b = csrf_b
        self.user_id_b = user_id_b


@pytest.fixture
def two_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracked_registry_factory: RegistryFactory,
) -> TwoUsers:
    """One fresh DB, one running app, two users registered through the real
    HTTP registration endpoint (not a storage-layer shortcut) using a fake
    environment registration code."""
    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    db_path = tmp_path / "isolation.db"
    init_db(db_path)
    # This suite's own tracked scope: one Turkish competition on every source.
    registry = tracked_registry_factory(
        [("super-lig", "country:TR", "Super Lig", {source: "super-lig" for source in Source})]
    )
    _populate_one_matched_event(db_path, registry)

    app = create_app(db_path=db_path, tracked_registry=registry)
    client_a = TestClient(app)
    client_b = TestClient(app)

    reg_a = client_a.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "password": PASSWORD_A,
            "registration_code": FAKE_REGISTRATION_CODE,
        },
    )
    assert reg_a.status_code == 200
    reg_b = client_b.post(
        "/api/v1/auth/register",
        json={
            "username": "bob",
            "password": PASSWORD_B,
            "registration_code": FAKE_REGISTRATION_CODE,
        },
    )
    assert reg_b.status_code == 200

    return TwoUsers(
        db_path=db_path,
        client_a=client_a,
        csrf_a=reg_a.json()["csrf_token"],
        user_id_a=reg_a.json()["user_id"],
        client_b=client_b,
        csrf_b=reg_b.json()["csrf_token"],
        user_id_b=reg_b.json()["user_id"],
    )


# --------------------------------------------------------------------------
# Step 1.2-1.3: personal settings, filters, and Telegram configs never cross
# --------------------------------------------------------------------------


def test_personal_settings_are_isolated_per_user(two_users: TwoUsers) -> None:
    u = two_users

    resp_a = u.client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": u.csrf_a},
        json={
            "default_budget_cents": 150_000,
            "include_source_a_match_sides": True,
            "tracked_competitions_only": False,
        },
    )
    assert resp_a.status_code == 200

    resp_b = u.client_b.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": u.csrf_b},
        json={
            "default_budget_cents": 900_000,
            "include_source_a_match_sides": False,
            "tracked_competitions_only": True,
        },
    )
    assert resp_b.status_code == 200

    settings_a = u.client_a.get("/api/v1/settings").json()["personal"]
    settings_b = u.client_b.get("/api/v1/settings").json()["personal"]

    assert settings_a["default_budget_cents"] == 150_000
    assert settings_a["include_source_a_match_sides"] is True
    assert settings_a["tracked_competitions_only"] is False

    assert settings_b["default_budget_cents"] == 900_000
    assert settings_b["include_source_a_match_sides"] is False
    assert settings_b["tracked_competitions_only"] is True

    # Neither client's response ever mentions the other user's own values.
    assert settings_a["default_budget_cents"] != settings_b["default_budget_cents"]


def test_saved_taxonomy_filters_are_isolated_per_user_and_scope(two_users: TwoUsers) -> None:
    u = two_users

    def _insert_league(display_name: str, region_id: str = "country:TR") -> str:
        from uuid import uuid4

        from surebet.taxonomy.normalizer import normalize_taxonomy_text

        league_id = f"league:{uuid4()}"
        with get_connection(u.db_path) as conn:
            conn.execute(
                "INSERT INTO canonical_leagues "
                "(league_id, region_id, display_name, normalized_key, kind, gender, age_group, "
                "is_reserve, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 1, ?, ?);",
                (
                    league_id,
                    region_id,
                    display_name,
                    normalize_taxonomy_text(display_name),
                    CompetitionKind.UNKNOWN.value,
                    NOW_ISO,
                    NOW_ISO,
                ),
            )
        return league_id

    league_a_events = _insert_league("A's Events League")
    league_b_events = _insert_league("B's Events League")
    league_a_opps = _insert_league("A's Opportunities League", region_id="country:DE")
    league_b_opps = _insert_league("B's Opportunities League", region_id="country:DE")

    # events scope
    u.client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": [league_a_events]},
    )
    u.client_b.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"country_ids": ["country:DE"], "league_ids": [league_b_events]},
    )
    # opportunities scope
    u.client_a.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"country_ids": ["country:DE"], "league_ids": [league_a_opps]},
    )
    u.client_b.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"country_ids": ["country:TR"], "league_ids": [league_b_opps]},
    )

    events_a = u.client_a.get("/api/v1/preferences/filters/events").json()
    events_b = u.client_b.get("/api/v1/preferences/filters/events").json()
    opps_a = u.client_a.get("/api/v1/preferences/filters/opportunities").json()
    opps_b = u.client_b.get("/api/v1/preferences/filters/opportunities").json()

    assert events_a["country_ids"] == ["country:TR"]
    assert events_a["league_ids"] == [league_a_events]
    assert events_b["country_ids"] == ["country:DE"]
    assert events_b["league_ids"] == [league_b_events]

    assert opps_a["country_ids"] == ["country:DE"]
    assert opps_a["league_ids"] == [league_a_opps]
    assert opps_b["country_ids"] == ["country:TR"]
    assert opps_b["league_ids"] == [league_b_opps]

    # Cross-checks: A never sees B's values under any scope, and vice versa.
    assert league_b_events not in events_a["league_ids"]
    assert league_a_events not in events_b["league_ids"]
    assert league_b_opps not in opps_a["league_ids"]
    assert league_a_opps not in opps_b["league_ids"]


def test_telegram_settings_are_isolated_even_with_the_same_chat_id(two_users: TwoUsers) -> None:
    """The hardest case: both users point at the exact same Telegram chat_id.
    Proven at the unit level in Tasks 6/7; this proves it holds at the full
    HTTP stack too."""
    u = two_users
    shared_chat_id = "777777777"

    resp_a = u.client_a.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": u.csrf_a},
        json={
            "chat_id": shared_chat_id,
            "minimum_profit_percent": "1.25",
            "decrease_threshold_percentage_points": "0.30",
            "alert_cooldown_seconds": 120,
        },
    )
    assert resp_a.status_code == 200

    resp_b = u.client_b.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": u.csrf_b},
        json={
            "chat_id": shared_chat_id,
            "minimum_profit_percent": "2.50",
            "decrease_threshold_percentage_points": "0.75",
            "alert_cooldown_seconds": 600,
        },
    )
    assert resp_b.status_code == 200

    config_a = u.client_a.get("/api/v1/notifications/telegram").json()
    config_b = u.client_b.get("/api/v1/notifications/telegram").json()

    # Both share the same chat_id (deliberately)...
    assert config_a["chat_id"] == shared_chat_id
    assert config_b["chat_id"] == shared_chat_id
    # ...but every other personal field stays fully independent.
    assert config_a["minimum_profit_percent"] == "1.25"
    assert config_b["minimum_profit_percent"] == "2.50"
    assert config_a["decrease_threshold_percentage_points"] == "0.30"
    assert config_b["decrease_threshold_percentage_points"] == "0.75"
    assert config_a["alert_cooldown_seconds"] == 120
    assert config_b["alert_cooldown_seconds"] == 600


# --------------------------------------------------------------------------
# Step 1.4: shared/system settings are visible to both users
# --------------------------------------------------------------------------


def test_system_settings_are_shared_and_visible_to_both_users(two_users: TwoUsers) -> None:
    u = two_users

    before_a = u.client_a.get("/api/v1/settings").json()["system"]
    before_b = u.client_b.get("/api/v1/settings").json()["system"]
    assert before_a["refresh_interval_seconds"] == before_b["refresh_interval_seconds"]

    new_interval = (
        before_a["refresh_interval_seconds"] + 15
        if before_a["refresh_interval_seconds"] < 285
        else 30
    )
    update_resp = u.client_a.put(
        "/api/v1/settings/system",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"refresh_interval_seconds": new_interval},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["refresh_interval_seconds"] == new_interval

    # User B, who never touched this endpoint, sees A's shared-system change.
    after_b = u.client_b.get("/api/v1/settings").json()["system"]
    assert after_b["refresh_interval_seconds"] == new_interval


# --------------------------------------------------------------------------
# Step 1.5: an explicit query-parameter filter override never persists
# --------------------------------------------------------------------------


def test_explicit_query_filter_override_does_not_persist(two_users: TwoUsers) -> None:
    u = two_users

    saved = u.client_a.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    assert saved.status_code == 200

    # Saved filter (country:TR) matches the fixture event -> opportunities present.
    baseline = u.client_a.get(
        "/api/v1/opportunities?budget_cents=100000&positive_only=false"
    ).json()
    assert len(baseline) >= 1

    # A one-off override to a country with no events must suppress the result
    # for this request only.
    overridden = u.client_a.get(
        "/api/v1/opportunities?budget_cents=100000&positive_only=false&country_id=country:DE"
    ).json()
    assert overridden == []

    # A follow-up request WITHOUT the override must return to the saved
    # filter's real results -- the override must never have been written back.
    # Compare identity fields only (not `calculated_at`/quote-age, which
    # legitimately differ between two separately timed requests).
    after = u.client_a.get("/api/v1/opportunities?budget_cents=100000&positive_only=false").json()
    assert len(after) >= 1
    assert {o["opportunity_id"] for o in after} == {o["opportunity_id"] for o in baseline}

    still_saved = u.client_a.get("/api/v1/preferences/filters/opportunities").json()
    assert still_saved["country_ids"] == ["country:TR"]

    # Same proof for /events.
    saved_events = u.client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    assert saved_events.status_code == 200

    baseline_events = u.client_a.get("/api/v1/events?time_scope=all").json()
    assert len(baseline_events) >= 1

    overridden_events = u.client_a.get("/api/v1/events?time_scope=all&country_id=country:DE").json()
    assert overridden_events == []

    after_events = u.client_a.get("/api/v1/events?time_scope=all").json()
    assert {e["matched_id"] for e in after_events} == {e["matched_id"] for e in baseline_events}

    still_saved_events = u.client_a.get("/api/v1/preferences/filters/events").json()
    assert still_saved_events["country_ids"] == ["country:TR"]


# --------------------------------------------------------------------------
# Step 1.6: logging out one session leaves the other session authenticated
# --------------------------------------------------------------------------


def test_logout_affects_only_the_logged_out_session(two_users: TwoUsers) -> None:
    u = two_users

    assert u.client_a.get("/api/v1/auth/me").json()["authenticated"] is True
    assert u.client_b.get("/api/v1/auth/me").json()["authenticated"] is True

    logout_resp = u.client_a.post("/api/v1/auth/logout", headers={"X-CSRF-Token": u.csrf_a})
    assert logout_resp.status_code == 200

    me_a = u.client_a.get("/api/v1/auth/me").json()
    assert me_a["authenticated"] is False

    me_b = u.client_b.get("/api/v1/auth/me").json()
    assert me_b["authenticated"] is True
    assert me_b["username"] == "bob"

    # B's own personal data is still reachable after A's logout.
    settings_b = u.client_b.get("/api/v1/settings")
    assert settings_b.status_code == 200


# --------------------------------------------------------------------------
# Step 1.6b (spec 13.2): CSRF tokens are session-specific
# --------------------------------------------------------------------------


def test_csrf_token_is_rejected_on_a_different_users_session(two_users: TwoUsers) -> None:
    """User B's own, currently-valid CSRF token must be rejected when replayed
    against user A's authenticated session cookie -- proving CSRF tokens are
    checked against the specific session presenting them, not merely "any
    currently-valid token", per spec section 13.2."""
    u = two_users

    resp = u.client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"default_budget_cents": 150_000},
    )

    assert resp.status_code == 403


# --------------------------------------------------------------------------
# Step 1.7: raw-database inspection -- user_id boundaries hold in stored rows
# --------------------------------------------------------------------------


def test_raw_database_rows_are_correctly_scoped_by_user_id(two_users: TwoUsers) -> None:
    u = two_users

    # Populate distinguishing personal state through the real API first.
    u.client_a.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"default_budget_cents": 111_000},
    )
    u.client_b.put(
        "/api/v1/settings/personal",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"default_budget_cents": 222_000},
    )
    u.client_a.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"country_ids": ["country:TR"], "league_ids": []},
    )
    u.client_b.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"country_ids": ["country:DE"], "league_ids": []},
    )
    shared_chat_id = 888888888
    u.client_a.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": u.csrf_a},
        json={"chat_id": str(shared_chat_id)},
    )
    u.client_b.put(
        "/api/v1/notifications/telegram",
        headers={"X-CSRF-Token": u.csrf_b},
        json={"chat_id": str(shared_chat_id)},
    )

    # Seed telegram_opportunity_cards / telegram_notification_outbox directly
    # through the repository (bypassing the full evaluate_cycle pipeline,
    # which needs a real live snapshot) using the SAME chat_id/matched_id/
    # generation for both users -- the worst case for accidental collision.
    repo = NotificationRepository(u.db_path)
    shared_matched_id = "shared-matched-event-1"
    transitions = []
    for user_id in (u.user_id_a, u.user_id_b):
        state = _fake_notification_state(user_id, shared_chat_id, shared_matched_id)
        draft = _fake_outbox_draft()
        transitions.append((state, (draft,)))
    repo.apply_transitions(transitions)

    # Now open a RAW connection directly (not through any repository) and
    # verify every table's rows respect user_id ownership.
    conn = sqlite3.connect(u.db_path)
    conn.row_factory = sqlite3.Row
    try:
        prefs_rows = {
            row["user_id"]: row["default_budget_cents"]
            for row in conn.execute(
                "SELECT user_id, default_budget_cents FROM user_preferences "
                "WHERE user_id IN (?, ?);",
                (u.user_id_a, u.user_id_b),
            )
        }
        assert prefs_rows[u.user_id_a] == 111_000
        assert prefs_rows[u.user_id_b] == 222_000

        country_filter_rows = conn.execute(
            "SELECT user_id, scope, country_id FROM user_country_filters WHERE user_id IN (?, ?);",
            (u.user_id_a, u.user_id_b),
        ).fetchall()
        by_user: dict[int, set[str]] = {u.user_id_a: set(), u.user_id_b: set()}
        for row in country_filter_rows:
            by_user[row["user_id"]].add(row["country_id"])
        assert by_user[u.user_id_a] == {"country:TR"}
        assert by_user[u.user_id_b] == {"country:DE"}

        telegram_rows = conn.execute(
            "SELECT user_id, chat_id FROM telegram_notification_settings WHERE user_id IN (?, ?);",
            (u.user_id_a, u.user_id_b),
        ).fetchall()
        assert len(telegram_rows) == 2
        for row in telegram_rows:
            assert row["chat_id"] == shared_chat_id  # both configured the same chat...
        assert {row["user_id"] for row in telegram_rows} == {u.user_id_a, u.user_id_b}

        state_rows = conn.execute(
            "SELECT user_id, chat_id, matched_id, card_key FROM telegram_opportunity_cards "
            "WHERE matched_id = ?;",
            (shared_matched_id,),
        ).fetchall()
        assert len(state_rows) == 2  # composite PK (user_id, card_key, generation) allowed both
        assert {row["user_id"] for row in state_rows} == {u.user_id_a, u.user_id_b}
        for row in state_rows:
            assert row["chat_id"] == shared_chat_id

        outbox_rows = conn.execute(
            "SELECT user_id, chat_id, matched_id, coalesce_key FROM telegram_notification_outbox "
            "WHERE matched_id = ?;",
            (shared_matched_id,),
        ).fetchall()
        assert len(outbox_rows) == 2  # UNIQUE(coalesce_key) did not collide across users
        assert {row["user_id"] for row in outbox_rows} == {u.user_id_a, u.user_id_b}
        coalesce_keys = {row["coalesce_key"] for row in outbox_rows}
        assert len(coalesce_keys) == 2  # user_id prefix keeps them distinct
        for row in outbox_rows:
            assert str(row["user_id"]) in row["coalesce_key"]

        # Also verify repository surface returns each user's cards in isolation
        cards_a = repo.list_current_cards(u.user_id_a)
        cards_b = repo.list_current_cards(u.user_id_b)
        assert len(cards_a) == 1
        assert len(cards_b) == 1
    finally:
        conn.close()


def _fake_notification_state(user_id: int, chat_id: int, matched_id: str) -> CardState:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    opp = CalculatedOpportunity(
        opportunity_id=f"{matched_id}:134",
        matched_id=matched_id,
        market_type=MarketType.FULL_TIME_RESULT,
        competition="Trendyol Super Lig",
        country_id="country:TR",
        country_name="Türkiye",
        country_iso_code="TR",
        league_id=None,
        league_name=None,
        home="Besiktas",
        away="Trabzonspor",
        kickoff=now,
        budget_cents=100_000,
        include_source_a_match_sides=False,
        selections=MappingProxyType(
            {
                Outcome.HOME: StrategySelection(
                    outcome=Outcome.HOME,
                    source=Source.SOURCE_C,
                    odds=Decimal("2.10"),
                    stake_cents=34_000,
                    cashback_eligible=True,
                ),
                Outcome.DRAW: StrategySelection(
                    outcome=Outcome.DRAW,
                    source=Source.SOURCE_B,
                    odds=Decimal("3.40"),
                    stake_cents=33_000,
                    cashback_eligible=True,
                ),
                Outcome.AWAY: StrategySelection(
                    outcome=Outcome.AWAY,
                    source=Source.SOURCE_C,
                    odds=Decimal("2.50"),
                    stake_cents=33_000,
                    cashback_eligible=True,
                ),
            }
        ),
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=1_340,
        min_adjusted_profit_percent=1.34,
        min_standard_profit_cents=1_340,
        min_standard_profit_percent=1.34,
        is_positive=True,
        oldest_quote_age_seconds=5,
        calculated_at=now,
    )
    evaluation = DualPolicyEvaluation(
        matched_id=matched_id,
        market_type=MarketType.FULL_TIME_RESULT,
        selected=opp,
        draw_only=opp,
        uses_source_a_match_sides=False,
        relevant_sources=frozenset({Source.SOURCE_C, Source.SOURCE_B}),
    )
    return CardState(
        user_id=user_id,
        chat_id=chat_id,
        card_key=card_key(matched_id, MarketType.FULL_TIME_RESULT),
        matched_id=matched_id,
        market_type=MarketType.FULL_TIME_RESULT,
        generation=1,
        status=LifecycleStatus.NEW,
        message_id=None,
        evaluation=evaluation,
        first_profit_bps=134,
        current_profit_bps=134,
        peak_profit_bps=134,
        anchor_bps=134,
        last_alert_at=None,
        below_since=None,
        closed_at=None,
        revival_count=0,
        first_seen_at=now,
        last_seen_at=now,
        last_edit_at=None,
        rendered_fingerprint=None,
    )


def _fake_outbox_draft() -> OutboxActionDraft:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return OutboxActionDraft(
        action=OutboxActionType.SEND_CARD,
        text="fixture message",
        fingerprint="fixture-fingerprint",
        next_attempt_at=now,
    )


# --------------------------------------------------------------------------
# Step 2: secret-artifact regression checks
# --------------------------------------------------------------------------


def test_fake_registration_code_absent_from_http_responses_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    db_path = tmp_path / "secret-check.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    responses = [
        client.get("/api/v1/auth/me"),
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "carol",
                "password": "correct-horse-battery-staple-c",
                "registration_code": FAKE_REGISTRATION_CODE,
            },
        ),
        client.get("/api/v1/auth/me"),
        client.post(
            "/api/v1/auth/login",
            json={"username": "carol", "password": "correct-horse-battery-staple-c"},
        ),
    ]

    for resp in responses:
        assert FAKE_REGISTRATION_CODE not in resp.text

    assert FAKE_REGISTRATION_CODE not in caplog.text


def test_fake_registration_code_absent_from_every_text_column_in_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUREBET_REGISTRATION_CODE", FAKE_REGISTRATION_CODE)
    db_path = tmp_path / "secret-check-db.db"
    init_db(db_path)
    app = create_app(db_path=db_path)
    client = TestClient(app)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "dave",
            "password": "correct-horse-battery-staple-d",
            "registration_code": FAKE_REGISTRATION_CODE,
        },
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "dave", "password": "correct-horse-battery-staple-d"},
    )
    # A failed registration attempt too, to make sure the rejected code is
    # never persisted anywhere either.
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "erin",
            "password": "correct-horse-battery-staple-e",
            "registration_code": "some-other-wrong-code",
        },
    )

    conn = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            )
        ]
        assert tables, "expected at least one application table"
        for table in tables:
            columns = conn.execute(f"PRAGMA table_info('{table}');").fetchall()
            text_columns = [col[1] for col in columns if (col[2] or "").upper() in ("TEXT", "")]
            for column in text_columns:
                rows = conn.execute(f'SELECT "{column}" FROM "{table}";').fetchall()
                for (value,) in rows:
                    if value is None:
                        continue
                    assert FAKE_REGISTRATION_CODE not in str(value), (
                        f"fake registration code leaked into {table}.{column}"
                    )
    finally:
        conn.close()

    # Also scan the raw file bytes (covers WAL/rowid encodings that PRAGMA
    # table_info's column typing might not perfectly categorize).
    blobs = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            blobs += candidate.read_bytes()
    assert FAKE_REGISTRATION_CODE.encode() not in blobs


def test_fake_registration_code_absent_from_frontend_build_output() -> None:
    """Registration code is a runtime, server-only env var never referenced by
    frontend source, so this should trivially pass -- but we assert it
    explicitly against the real build output rather than assuming.
    """
    dist_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if not dist_dir.exists():
        pytest.skip("frontend/dist not built in this environment; run `npm run build` first")

    offending: list[str] = []
    for path in dist_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # binary asset (image/font/etc.) -- not a text artifact
        if FAKE_REGISTRATION_CODE in text:
            offending.append(str(path))

    assert offending == [], f"fake registration code found in build output: {offending}"
