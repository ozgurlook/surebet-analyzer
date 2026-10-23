from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.db import get_connection, init_db
from surebet.storage.snapshots import SnapshotRepository, SourceHealthStatus
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind


def _make_event(source: Source, event_id: str, kickoff: datetime) -> CollectedEvent:
    quote_home = Quote(
        source=source,
        event_id=event_id,
        outcome=Outcome.HOME,
        odds=Decimal("2.00"),
        observed_at=kickoff,
    )
    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition="Super Lig",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=kickoff,
        market_key=FULL_TIME_RESULT_MARKET,
        quotes={
            Outcome.HOME: quote_home,
            Outcome.DRAW: quote_home.model_copy(
                update={"outcome": Outcome.DRAW, "odds": Decimal("3.00")}
            ),
            Outcome.AWAY: quote_home.model_copy(
                update={"outcome": Outcome.AWAY, "odds": Decimal("4.00")}
            ),
        },
    )


def test_snapshots_save_and_retrieve_latest_and_health(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    repo = SnapshotRepository(db_path)
    now = datetime(2026, 8, 24, 15, 0, 0, tzinfo=UTC)

    quote_home = Quote(
        source=Source.SOURCE_C,
        event_id="source_c_101",
        outcome=Outcome.HOME,
        odds=Decimal("2.10"),
        observed_at=now,
    )
    quote_draw = Quote(
        source=Source.SOURCE_C,
        event_id="source_c_101",
        outcome=Outcome.DRAW,
        odds=Decimal("3.40"),
        observed_at=now,
    )
    quote_away = Quote(
        source=Source.SOURCE_C,
        event_id="source_c_101",
        outcome=Outcome.AWAY,
        odds=Decimal("3.80"),
        observed_at=now,
    )

    event = CollectedEvent(
        source=Source.SOURCE_C,
        source_event_id="source_c_101",
        competition="Super Lig",
        home="Galatasaray",
        away="Fenerbahce",
        kickoff=now,
        market_key=FULL_TIME_RESULT_MARKET,
        quotes={
            Outcome.HOME: quote_home,
            Outcome.DRAW: quote_draw,
            Outcome.AWAY: quote_away,
        },
    )

    batch = CollectionBatch(
        source=Source.SOURCE_C,
        started_at=now,
        finished_at=now,
        events=(event,),
    )

    repo.save_batch(batch, duration_ms=450)

    loaded = repo.get_latest_batch(Source.SOURCE_C)
    assert loaded is not None
    assert loaded.source == Source.SOURCE_C
    assert len(loaded.events) == 1
    assert loaded.events[0].home == "Galatasaray"
    assert loaded.events[0].quotes[Outcome.HOME].odds == Decimal("2.10")

    health = repo.get_source_health()
    assert Source.SOURCE_C in health
    source_c_health = health[Source.SOURCE_C]
    assert source_c_health.status == SourceHealthStatus.HEALTHY
    assert source_c_health.event_count == 1
    assert source_c_health.duration_ms == 450
    assert source_c_health.last_success_at == now

    # Test recording a failure preserves last successful batch
    fail_time = datetime(2026, 8, 24, 15, 1, 0, tzinfo=UTC)
    repo.record_failure(
        Source.SOURCE_C,
        started_at=fail_time,
        error_message="Connection timed out",
        duration_ms=5000,
    )

    still_loaded = repo.get_latest_batch(Source.SOURCE_C)
    assert still_loaded is not None
    assert len(still_loaded.events) == 1

    updated_health = repo.get_source_health()[Source.SOURCE_C]
    assert updated_health.status == SourceHealthStatus.DEGRADED
    assert updated_health.error_message == "Connection timed out"
    assert updated_health.last_success_at == now


def test_purge_expired_retains_at_kickoff_plus_14m59s_and_deletes_at_15m(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = SnapshotRepository(db_path)

    now = datetime(2026, 8, 24, 16, 0, 0, tzinfo=UTC)
    retained_kickoff = now - timedelta(minutes=14, seconds=59)
    expired_kickoff = now - timedelta(minutes=15)

    retained_event = _make_event(Source.SOURCE_C, "source_c_retain", retained_kickoff)
    expired_event = _make_event(Source.SOURCE_C, "source_c_expire", expired_kickoff)

    batch = CollectionBatch(
        source=Source.SOURCE_C,
        started_at=now,
        finished_at=now,
        events=(retained_event, expired_event),
    )
    repo.save_batch(batch, duration_ms=100)
    before_health = repo.get_source_health()[Source.SOURCE_C]

    removed = repo.purge_expired(now)

    assert removed == {Source.SOURCE_C: 1}

    loaded = repo.get_latest_batch(Source.SOURCE_C)
    assert loaded is not None
    assert [event.source_event_id for event in loaded.events] == ["source_c_retain"]

    after_health = repo.get_source_health()[Source.SOURCE_C]
    assert after_health.event_count == 1
    assert after_health.status == SourceHealthStatus.HEALTHY
    assert after_health.duration_ms == before_health.duration_ms
    assert after_health.started_at == before_health.started_at
    assert after_health.finished_at == before_health.finished_at
    assert after_health.last_success_at == before_health.last_success_at
    assert after_health.updated_at == now


def test_purge_expired_leaves_taxonomy_tables_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path)

    now = datetime(2026, 8, 24, 16, 0, 0, tzinfo=UTC)
    expired_kickoff = now - timedelta(minutes=20)

    evidence = SourceCompetitionEvidence(
        provider=Source.SOURCE_C.value,
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="comp-1",
        competition_name="Super Lig",
    )
    taxonomy_repo.observe(Source.SOURCE_C, evidence, now)

    batch = CollectionBatch(
        source=Source.SOURCE_C,
        started_at=now,
        finished_at=now,
        events=(_make_event(Source.SOURCE_C, "source_c_expire", expired_kickoff),),
    )
    snapshot_repo.save_batch(batch)

    def _table_rows(table: str) -> list[dict[str, object]]:
        with get_connection(db_path) as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table};").fetchall()]

    before = {
        table: _table_rows(table)
        for table in (
            "source_competitions",
            "competition_mappings",
            "canonical_leagues",
            "taxonomy_audit",
        )
    }
    assert before["source_competitions"]  # sanity: taxonomy data actually exists

    removed = snapshot_repo.purge_expired(now)
    assert removed == {Source.SOURCE_C: 1}

    for table, rows in before.items():
        assert _table_rows(table) == rows


def test_purge_expired_handles_degraded_source_independently_of_healthy_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = SnapshotRepository(db_path)

    now = datetime(2026, 8, 24, 16, 0, 0, tzinfo=UTC)
    retained_kickoff = now - timedelta(minutes=5)
    expired_kickoff = now - timedelta(minutes=30)

    source_c_batch = CollectionBatch(
        source=Source.SOURCE_C,
        started_at=now,
        finished_at=now,
        events=(_make_event(Source.SOURCE_C, "source_c_ok", retained_kickoff),),
    )
    repo.save_batch(source_c_batch)

    source_b_batch = CollectionBatch(
        source=Source.SOURCE_B,
        started_at=now,
        finished_at=now,
        events=(_make_event(Source.SOURCE_B, "source_b_stale", expired_kickoff),),
    )
    repo.save_batch(source_b_batch)
    # Simulate a failed refresh cycle following the last successful Source B batch.
    repo.record_failure(Source.SOURCE_B, started_at=now, error_message="timeout", duration_ms=5000)

    removed = repo.purge_expired(now)

    assert removed == {Source.SOURCE_B: 1}

    source_c_loaded = repo.get_latest_batch(Source.SOURCE_C)
    assert source_c_loaded is not None
    assert len(source_c_loaded.events) == 1

    source_b_loaded = repo.get_latest_batch(Source.SOURCE_B)
    assert source_b_loaded is not None
    assert source_b_loaded.events == ()

    source_b_health = repo.get_source_health()[Source.SOURCE_B]
    assert source_b_health.status == SourceHealthStatus.DEGRADED
    assert source_b_health.error_message == "timeout"
    assert source_b_health.event_count == 0
