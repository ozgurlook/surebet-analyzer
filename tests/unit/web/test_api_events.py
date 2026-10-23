"""Unit tests for /api/v1/events taxonomy-aware filters: time_scope, repeated
country_id/league_id, search, invalid filter IDs, and unmapped visibility."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import surebet.web.routes.events as events_module
from surebet.domain.enums import Outcome, Source
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

# The task brief's own example: 21:30 UTC is 00:30 in Europe/Istanbul (UTC+3,
# no DST) -- a NEW Istanbul calendar day (2026-08-30) has just begun.
FIXED_NOW = datetime(2026, 8, 29, 21, 30, 0, tzinfo=UTC)

# Same Istanbul calendar day as FIXED_NOW (2026-08-30), a few minutes later.
TODAY_EARLY = FIXED_NOW + timedelta(minutes=5)  # Istanbul 2026-08-30 00:35

# Same Istanbul calendar day (2026-08-30) but a *different UTC calendar date*
# (2026-08-30, vs FIXED_NOW's UTC date 2026-08-29). Proves the boundary is
# computed from Istanbul calendar days, not sliced by UTC date.
TODAY_LATE_UTC_NEXT_DAY = datetime(2026, 8, 30, 2, 0, 0, tzinfo=UTC)  # Istanbul 05:00

# Same UTC calendar date as FIXED_NOW (2026-08-29) but an *earlier* Istanbul
# calendar day (Istanbul 2026-08-29 23:00, before the Istanbul midnight
# rollover). Must NOT be "today" despite sharing FIXED_NOW's UTC date.
YESTERDAY_ISTANBUL = datetime(2026, 8, 29, 20, 0, 0, tzinfo=UTC)  # Istanbul 2026-08-29 23:00

# Istanbul calendar day 2026-08-31 -- "tomorrow" relative to FIXED_NOW.
TOMORROW_ISTANBUL = datetime(2026, 8, 30, 22, 0, 0, tzinfo=UTC)  # Istanbul 2026-08-31 01:00


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


def _verified_collected_events(
    *,
    competition_id: str,
    competition_name: str,
    country_id: str,
    country_name: str,
    home: str,
    away: str,
    kickoff: datetime,
    event_id_prefix: str,
) -> dict[Source, CollectedEvent]:
    """Build the SOURCE_C + SOURCE_B CollectedEvent pair for one taxonomy-verified match."""
    result: dict[Source, CollectedEvent] = {}
    for source, suffix in ((Source.SOURCE_C, "source_c"), (Source.SOURCE_B, "source_b")):
        event_id = f"{event_id_prefix}-{suffix}"
        evidence = _evidence(source, competition_id, competition_name, country_id, country_name)
        result[source] = CollectedEvent(
            source=source,
            source_event_id=event_id,
            competition=competition_name,
            home=home,
            away=away,
            kickoff=kickoff,
            market_key=FULL_TIME_RESULT_MARKET,
            source_competition=evidence,
            quotes={
                Outcome.HOME: _quote(source, event_id, Outcome.HOME, "2.10", FIXED_NOW),
                Outcome.DRAW: _quote(source, event_id, Outcome.DRAW, "3.30", FIXED_NOW),
                Outcome.AWAY: _quote(source, event_id, Outcome.AWAY, "3.40", FIXED_NOW),
            },
        )
    return result


def _unmapped_collected_event(
    *,
    competition_name: str,
    home: str,
    away: str,
    kickoff: datetime,
) -> CollectedEvent:
    """Build a single-source (SOURCE_A) CollectedEvent whose competition evidence
    is never observed by the taxonomy repo, so it stays TaxonomyStatus.UNMAPPED."""
    evidence = _evidence(Source.SOURCE_A, "unknown-cup", competition_name, "TR", "Türkiye")
    return CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="unmapped-1",
        competition=competition_name,
        home=home,
        away=away,
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=evidence,
        quotes={
            Outcome.HOME: _quote(Source.SOURCE_A, "unmapped-1", Outcome.HOME, "2.00", FIXED_NOW),
            Outcome.DRAW: _quote(Source.SOURCE_A, "unmapped-1", Outcome.DRAW, "3.00", FIXED_NOW),
            Outcome.AWAY: _quote(Source.SOURCE_A, "unmapped-1", Outcome.AWAY, "4.00", FIXED_NOW),
        },
    )


@pytest.fixture
def client(tmp_path: Path, tracked_registry_factory: RegistryFactory) -> tuple[TestClient, str]:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("alice", "correct-horse-battery-staple")

    # This module covers taxonomy/filtering behaviour, so it declares its own
    # tracked scope rather than borrowing the packaged league list.
    registry = tracked_registry_factory(
        [
            (key, region_id, display_name, {Source.SOURCE_C: key, Source.SOURCE_B: key})
            for key, region_id, display_name in (
                ("super-lig", "country:TR", "Süper Lig"),
                ("lig1", "country:TR", "1. Lig"),
                ("bundesliga", "country:DE", "Bundesliga"),
                ("bundesliga2", "country:DE", "2. Bundesliga"),
            )
        ]
    )

    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path, registry)

    verified_specs = [
        {
            "competition_id": "super-lig",
            "competition_name": "Süper Lig",
            "country_id": "TR",
            "country_name": "Türkiye",
            "home": "Galatasaray",
            "away": "Fenerbahce",
            "kickoff": TODAY_EARLY,
            "event_id_prefix": "today-a",
        },
        {
            "competition_id": "lig1",
            "competition_name": "1. Lig",
            "country_id": "TR",
            "country_name": "Türkiye",
            "home": "Trabzonspor",
            "away": "Konyaspor",
            "kickoff": TODAY_LATE_UTC_NEXT_DAY,
            "event_id_prefix": "today-b",
        },
        {
            "competition_id": "bundesliga",
            "competition_name": "Bundesliga",
            "country_id": "DE",
            "country_name": "Almanya",
            "home": "Bayern Munih",
            "away": "Borussia Dortmund",
            "kickoff": YESTERDAY_ISTANBUL,
            "event_id_prefix": "yesterday",
        },
        {
            "competition_id": "bundesliga2",
            "competition_name": "2. Bundesliga",
            "country_id": "DE",
            "country_name": "Almanya",
            "home": "Schalke 04",
            "away": "Hertha Berlin",
            "kickoff": TOMORROW_ISTANBUL,
            "event_id_prefix": "tomorrow",
        },
    ]

    # SnapshotRepository.save_batch() replaces a source's *entire* stored batch
    # on every call (source_snapshots is keyed by source alone) -- so every
    # event for a given source must be collected into ONE CollectionBatch and
    # saved with a single save_batch() call per source, not one call per event.
    source_c_events: list[CollectedEvent] = []
    source_b_events: list[CollectedEvent] = []
    for spec in verified_specs:
        pair = _verified_collected_events(**spec)  # type: ignore[arg-type]
        source_c_events.append(pair[Source.SOURCE_C])
        source_b_events.append(pair[Source.SOURCE_B])
        for source, event in pair.items():
            assert event.source_competition is not None
            taxonomy_repo.observe(source, event.source_competition, FIXED_NOW)

    unmapped_event = _unmapped_collected_event(
        competition_name="Yerel Kupa",
        home="Rakip A",
        away="Rakip B",
        kickoff=TODAY_EARLY,
    )

    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=FIXED_NOW,
            finished_at=FIXED_NOW,
            events=tuple(source_c_events),
        ),
        duration_ms=100,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B,
            started_at=FIXED_NOW,
            finished_at=FIXED_NOW,
            events=tuple(source_b_events),
        ),
        duration_ms=100,
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=FIXED_NOW,
            finished_at=FIXED_NOW,
            events=(unmapped_event,),
        ),
        duration_ms=100,
    )
    # Intentionally never call taxonomy_repo.observe() for `unmapped_event` --
    # that is what keeps its competition unresolved (TaxonomyStatus.UNMAPPED),
    # and outside the tracked registry, so it never reaches the catalog.

    app = create_app(db_path=db_path, tracked_registry=registry)
    c = TestClient(app)
    c.cookies.set("session_token", session.token)
    return c, session.csrf_token


@pytest.fixture(autouse=True)
def _freeze_events_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze events.py's `datetime.now(UTC)` at FIXED_NOW so time_scope
    boundaries are deterministic and match the fixture's kickoff times."""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW

    monkeypatch.setattr(events_module, "datetime", _FrozenDateTime)


def test_default_time_scope_is_today_using_istanbul_calendar_day(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    resp = c.get("/api/v1/events")
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}

    assert "Galatasaray" in homes  # TODAY_EARLY
    assert "Trabzonspor" in homes  # TODAY_LATE_UTC_NEXT_DAY: same Istanbul day, different UTC date
    assert "Rakip A" not in homes  # outside the tracked registry, so never in the catalog
    assert (
        "Bayern Munih" not in homes
    )  # YESTERDAY_ISTANBUL: same UTC date as now, earlier Istanbul day
    assert "Schalke 04" not in homes  # TOMORROW_ISTANBUL


def test_time_scope_tomorrow_uses_istanbul_calendar_day(client: tuple[TestClient, str]) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "tomorrow"})
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Schalke 04"}


def test_time_scope_all_includes_every_event(client: tuple[TestClient, str]) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih", "Schalke 04"}


def test_event_outside_the_tracked_registry_is_never_listed(
    client: tuple[TestClient, str],
) -> None:
    """Scope is locked to the tracked registry, so unmapped events never surface."""
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert resp.status_code == 200
    assert all(ev["home"] != "Rakip A" for ev in resp.json())
    assert all(ev["taxonomy_status"] == "verified" for ev in resp.json())


def test_verified_event_carries_taxonomy_fields(client: tuple[TestClient, str]) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    ev = next(item for item in resp.json() if item["home"] == "Galatasaray")
    assert ev["taxonomy_status"] == "verified"
    assert ev["country_id"] is not None
    assert ev["country_name"] == "Türkiye"
    assert ev["league_id"] is not None
    assert ev["league_name"] == "Süper Lig"


def test_country_id_filter_is_or_within_dimension(client: tuple[TestClient, str]) -> None:
    c, _ = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")
    de_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Bayern Munih")
    assert tr_country_id != de_country_id

    resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all", "country_id": [tr_country_id, de_country_id]},
    )
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    # OR within country_id: both countries' verified events appear; the
    # unmapped event (no country_id) is correctly excluded by the filter.
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih", "Schalke 04"}


def test_league_id_filter(client: tuple[TestClient, str]) -> None:
    c, _ = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    lig1_league_id = next(ev["league_id"] for ev in all_events if ev["home"] == "Trabzonspor")

    resp = c.get("/api/v1/events", params={"time_scope": "all", "league_id": lig1_league_id})
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Trabzonspor"}


def test_country_and_league_filters_are_anded_across_dimensions(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")
    de_league_id = next(ev["league_id"] for ev in all_events if ev["home"] == "Bayern Munih")

    # AND across dimensions: TR country + a DE league_id -> nothing satisfies both.
    resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all", "country_id": tr_country_id, "league_id": de_league_id},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_invalid_filter_ids_return_empty_without_crashing(client: tuple[TestClient, str]) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all", "country_id": "does-not-exist"})
    assert resp.status_code == 200
    assert resp.json() == []

    resp2 = c.get("/api/v1/events", params={"time_scope": "all", "league_id": "does-not-exist"})
    assert resp2.status_code == 200
    assert resp2.json() == []


def test_search_matches_normalized_country_league_home_away(client: tuple[TestClient, str]) -> None:
    c, _ = client

    resp = c.get("/api/v1/events", params={"time_scope": "all", "search": "Türkiye"})
    assert {ev["home"] for ev in resp.json()} == {"Galatasaray", "Trabzonspor"}

    resp2 = c.get("/api/v1/events", params={"time_scope": "all", "search": "Bundesliga"})
    assert {ev["home"] for ev in resp2.json()} == {"Bayern Munih", "Schalke 04"}

    resp3 = c.get("/api/v1/events", params={"time_scope": "all", "search": "Galatasaray"})
    assert {ev["home"] for ev in resp3.json()} == {"Galatasaray"}

    resp4 = c.get("/api/v1/events", params={"time_scope": "all", "search": "Fenerbahce"})
    assert {ev["home"] for ev in resp4.json()} == {"Galatasaray"}


def test_search_cannot_reach_events_outside_the_tracked_registry(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all", "search": "Yerel"})
    assert resp.status_code == 200
    assert resp.json() == []


# --- Saved default filters: omitted-vs-explicit-override behavior ---------


@pytest.fixture
def second_user_client(client: tuple[TestClient, str], tmp_path: Path) -> tuple[TestClient, str]:
    """A second real user/session sharing the same database as `client`, so
    isolation assertions prove a saved filter write stayed scoped to its
    owner rather than merely matching a response shape."""
    c, _csrf = client
    db_path = tmp_path / "test.db"
    auth = AuthRepository(db_path)
    user_b, session_b = auth.register_user("bob", "correct-horse-battery-staple")
    # None of this module's fixture competitions are tracked-registry
    # entries, so leaving the tracked-competitions-only default (True) would
    # hide everything for bob regardless of taxonomy filters -- match the
    # `client` fixture's own override so this test isolates filter behavior.
    UserPreferencesRepository(db_path).update(
        user_b.user_id, UserPreferencesUpdate(tracked_competitions_only=False)
    )
    client_b = TestClient(c.app)
    client_b.cookies.set("session_token", session_b.token)
    return client_b, session_b.csrf_token


def test_omitted_filters_load_the_users_saved_events_scope_default(
    client: tuple[TestClient, str],
) -> None:
    c, csrf = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")

    put_resp = c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )
    assert put_resp.status_code == 200

    # No country_id/league_id query params at all -> the saved default applies.
    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor"}


def test_wanted_ids_helper_treats_explicit_empty_list_as_no_narrowing(
    client: tuple[TestClient, str],
) -> None:
    """`country_id=[]`/`league_id=[]` (a genuinely empty, but explicitly
    supplied, override -- not reachable through plain query-string encoding,
    which cannot distinguish 'zero values' from 'omitted') must still be
    honored as an override and show everything, ignoring any saved filter --
    exercised directly against the resolver since HTTP transport cannot
    produce this case."""
    assert events_module._wanted_ids([], frozenset({"country:TR"})) is None
    assert events_module._wanted_ids(None, frozenset({"country:TR"})) == {"country:TR"}
    assert events_module._wanted_ids(["country:DE"], frozenset({"country:TR"})) == {"country:DE"}
    assert events_module._wanted_ids(None, frozenset()) is None


def test_use_saved_filters_false_with_cleared_filters_shows_everything(
    client: tuple[TestClient, str],
) -> None:
    """A plain query string cannot distinguish an explicitly-cleared filter
    list from an omitted one (`appendMany` sends nothing for `[]`), so a
    client clearing all filters must be able to say "don't fall back to my
    saved filter" via `use_saved_filters=false`. With it set, even a fully
    omitted country_id/league_id must be treated as an explicit empty
    override (show everything), never as "use my saved filter" -- proven
    here against a saved TR-only filter that would otherwise narrow results."""
    c, csrf = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # No country_id/league_id query params, but use_saved_filters=false --
    # must show everything, not fall back to the saved TR-only filter.
    resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all", "use_saved_filters": "false"},
    )
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih", "Schalke 04"}

    # Sanity: the saved filter is untouched and still applies on a plain request.
    plain_resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert {ev["home"] for ev in plain_resp.json()} == {"Galatasaray", "Trabzonspor"}


def test_explicit_override_replaces_rather_than_merges_with_saved_filter(
    client: tuple[TestClient, str],
) -> None:
    c, csrf = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # A deliberately different country_id override must *replace* (not
    # add to) the saved TR filter -- TR results disappear entirely.
    de_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Bayern Munih")
    resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all", "country_id": [de_country_id]},
    )
    assert resp.status_code == 200
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Bayern Munih", "Schalke 04"}


def test_query_override_does_not_persist_and_a_later_plain_request_reverts_to_saved(
    client: tuple[TestClient, str],
) -> None:
    """The core non-persistence invariant: a one-off query override must never
    be written back to the saved filter, no matter what it was set to."""
    c, csrf = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")
    de_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Bayern Munih")

    c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # A request-time override to DE only -- must not be persisted.
    override_resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all", "country_id": [de_country_id]},
    )
    assert {ev["home"] for ev in override_resp.json()} == {"Bayern Munih", "Schalke 04"}

    # The saved filter itself must still read back as the original TR selection.
    saved = c.get("/api/v1/preferences/filters/events").json()
    assert saved["country_ids"] == [tr_country_id]

    # A subsequent plain request (no query params) reverts to the ORIGINAL
    # saved (TR) filter, not the override.
    plain_resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert {ev["home"] for ev in plain_resp.json()} == {"Galatasaray", "Trabzonspor"}


def test_saved_events_filter_is_isolated_per_user(
    client: tuple[TestClient, str],
    second_user_client: tuple[TestClient, str],
) -> None:
    c, csrf = client
    c_b, _csrf_b = second_user_client

    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/events",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    # User A's plain request is narrowed by their saved filter.
    resp_a = c.get("/api/v1/events", params={"time_scope": "all"})
    assert {ev["home"] for ev in resp_a.json()} == {"Galatasaray", "Trabzonspor"}

    # User B has no saved filter of their own -- their plain request must
    # still see everything, proving the saved filter never leaked cross-user.
    resp_b = c_b.get("/api/v1/events", params={"time_scope": "all"})
    homes_b = {ev["home"] for ev in resp_b.json()}
    assert homes_b == {"Galatasaray", "Trabzonspor", "Bayern Munih", "Schalke 04"}


def test_saved_filter_scope_is_events_only_not_shared_with_opportunities(
    client: tuple[TestClient, str],
) -> None:
    """A filter saved for the `events` scope must never narrow the events
    endpoint via the `opportunities` scope's stored rows -- proven by writing
    to the `opportunities` scope only and confirming events stays unfiltered."""
    c, csrf = client
    all_events = c.get("/api/v1/events", params={"time_scope": "all"}).json()
    tr_country_id = next(ev["country_id"] for ev in all_events if ev["home"] == "Galatasaray")

    c.put(
        "/api/v1/preferences/filters/opportunities",
        headers={"X-CSRF-Token": csrf},
        json={"country_ids": [tr_country_id], "league_ids": []},
    )

    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    homes = {ev["home"] for ev in resp.json()}
    assert homes == {"Galatasaray", "Trabzonspor", "Bayern Munih", "Schalke 04"}


def test_events_returns_etag_and_304_on_match(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    resp = c.get("/api/v1/events", params={"time_scope": "all"})
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None
    assert etag.startswith('W/"ev-')

    # Second request with matching If-None-Match should return 304 Not Modified
    cached_resp = c.get(
        "/api/v1/events",
        params={"time_scope": "all"},
        headers={"if-none-match": etag},
    )
    assert cached_resp.status_code == 304
    assert cached_resp.text == ""
