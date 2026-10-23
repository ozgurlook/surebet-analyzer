import asyncio
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.db import init_db
from surebet.storage.settings import SystemSettingsRepository
from surebet.storage.snapshots import SnapshotRepository, SourceHealthStatus
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind
from surebet.web.collector_runner import CollectorRunner

# Fixed, deterministic reference instant for all fixtures in this module.
NOW = datetime(2026, 8, 24, 15, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _pin_collector_runner_now() -> Iterator[None]:
    """Pin collector_runner.py's datetime.now(UTC) to NOW for every test.

    run_cycle() calls the real datetime.now(UTC) once per source (_collect_source's
    start_time) and once more after asyncio.gather() settles, to purge expired
    snapshots. Every test in this module builds its fixture events with kickoff/
    started_at/finished_at defaulting to NOW (a fixed constant, not wall-clock time),
    so without this patch run_cycle()'s real-time purge call would silently discard
    those fixture events whenever the fixed NOW is more than the purge grace period
    behind the real clock - reintroducing exactly the wall-clock coupling this fixture
    exists to avoid. Patching the module-level `datetime` name that collector_runner.py
    imports keeps every run_cycle()-mediated assertion in this file deterministic.
    """
    fake_datetime = MagicMock()
    fake_datetime.now.return_value = NOW
    with patch("surebet.web.collector_runner.datetime", fake_datetime):
        yield


def _make_batch(
    source: Source,
    competition_ids: tuple[str | None, ...] | None = None,
    kickoff: datetime | None = None,
) -> CollectionBatch:
    if competition_ids is None:
        competition_ids = (None,)
    event_kickoff = kickoff if kickoff is not None else NOW

    events = []
    for index, competition_id in enumerate(competition_ids, start=1):
        event_id = f"{source.value}_{index}"
        q = Quote(
            source=source,
            event_id=event_id,
            outcome=Outcome.HOME,
            odds=Decimal("2.00"),
            observed_at=NOW,
        )
        evidence = (
            SourceCompetitionEvidence(
                provider=source.value,
                identity_kind=SourceIdentityKind.PROVIDER_ID,
                competition_id=competition_id,
                competition_name=f"League {competition_id}",
            )
            if competition_id is not None
            else None
        )
        events.append(
            CollectedEvent(
                source=source,
                source_event_id=event_id,
                competition="League",
                home="Team A",
                away="Team B",
                kickoff=event_kickoff,
                market_key=FULL_TIME_RESULT_MARKET,
                quotes={
                    Outcome.HOME: q,
                    Outcome.DRAW: q.model_copy(
                        update={"outcome": Outcome.DRAW, "odds": Decimal("3.00")}
                    ),
                    Outcome.AWAY: q.model_copy(
                        update={"outcome": Outcome.AWAY, "odds": Decimal("4.00")}
                    ),
                },
                source_competition=evidence,
            )
        )

    return CollectionBatch(
        source=source,
        started_at=NOW,
        finished_at=NOW,
        events=tuple(events),
    )


class _PurgeTrackingSnapshotRepository(SnapshotRepository):
    """Wraps SnapshotRepository to record purge_expired invocations for assertions."""

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.purge_calls: list[datetime] = []

    def purge_expired(
        self,
        now: datetime,
        grace: timedelta = timedelta(minutes=15),
    ) -> dict[Source, int]:
        self.purge_calls.append(now)
        return super().purge_expired(now, grace)


@pytest.mark.asyncio
async def test_collector_runner_executes_all_sources_and_isolates_failures(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)

    async def mock_source_c() -> CollectionBatch:
        return _make_batch(Source.SOURCE_C)

    async def mock_source_b() -> CollectionBatch:
        return _make_batch(Source.SOURCE_B)

    async def mock_source_a_failing() -> CollectionBatch:
        raise RuntimeError("Source A gateway unreachable")

    collectors = {
        Source.SOURCE_C: mock_source_c,
        Source.SOURCE_B: mock_source_b,
        Source.SOURCE_A: mock_source_a_failing,
    }

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs=collectors,
    )

    # Run once
    await runner.run_cycle()

    # Verify Source C & Source B succeeded
    source_c_batch = snapshot_repo.get_latest_batch(Source.SOURCE_C)
    assert source_c_batch is not None
    assert len(source_c_batch.events) == 1

    source_b_batch = snapshot_repo.get_latest_batch(Source.SOURCE_B)
    assert source_b_batch is not None
    assert len(source_b_batch.events) == 1

    # Verify Source A failure was isolated
    health = snapshot_repo.get_source_health()
    assert health[Source.SOURCE_C].status == SourceHealthStatus.HEALTHY
    assert health[Source.SOURCE_B].status == SourceHealthStatus.HEALTHY
    assert health[Source.SOURCE_A].status == SourceHealthStatus.UNAVAILABLE
    assert health[Source.SOURCE_A].error_message == "Source A gateway unreachable"


@pytest.mark.asyncio
async def test_collector_runner_rate_limits_manual_refresh(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)

    mock_collect = AsyncMock(return_value=_make_batch(Source.SOURCE_C))
    collectors = {Source.SOURCE_C: mock_collect}

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs=collectors,
        cooldown_seconds=10,
    )

    # First manual refresh should succeed
    success1 = await runner.trigger_refresh()
    assert success1 is True

    # Immediate second manual refresh should be rejected due to cooldown
    success2 = await runner.trigger_refresh()
    assert success2 is False


@pytest.mark.asyncio
async def test_manual_refresh_waits_until_collection_cycle_finishes(tmp_path: Path) -> None:
    db_path = tmp_path / "manual-refresh-wait.db"
    init_db(db_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_collect() -> CollectionBatch:
        started.set()
        await release.wait()
        return _make_batch(Source.SOURCE_C)

    runner = CollectorRunner(
        snapshot_repo=SnapshotRepository(db_path),
        settings_repo=SystemSettingsRepository(db_path),
        collector_funcs={Source.SOURCE_C: blocked_collect},
    )

    refresh_task = asyncio.create_task(runner.trigger_refresh())
    await started.wait()
    completed_before_collection = refresh_task.done()
    release.set()
    result = await refresh_task

    assert completed_before_collection is False
    assert result is True


@pytest.mark.asyncio
async def test_default_runner_reloads_endpoints_each_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    seen_urls: list[tuple[Source, str]] = []

    def collector_factory(source: Source, endpoint_config: object):
        seen_urls.append((source, endpoint_config.websocket_url))

        async def collect() -> CollectionBatch:
            return _make_batch(source)

        return collect

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_factory=collector_factory,
    )

    await runner.run_cycle()
    monkeypatch.setenv("SUREBET_SOURCE_B_ENDPOINT", "wss://rotated.example.test/v2")
    await runner.run_cycle()

    assert [url for source, url in seen_urls if source is Source.SOURCE_B] == [
        "wss://source-b.example.test/v2",
        "wss://rotated.example.test/v2",
    ]


@pytest.mark.asyncio
async def test_unconfigured_source_is_recorded_as_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    monkeypatch.delenv("SUREBET_SOURCE_A_ENDPOINT")

    def collector_factory(source: Source, endpoint_config: object):
        async def collect() -> CollectionBatch:
            return _make_batch(source)

        return collect

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=SystemSettingsRepository(db_path),
        collector_factory=collector_factory,
    )

    await runner.run_cycle()

    health = snapshot_repo.get_source_health()
    assert "SUREBET_SOURCE_A_ENDPOINT" in (health[Source.SOURCE_A].error_message or "")
    assert snapshot_repo.get_latest_batch(Source.SOURCE_B) is not None


@pytest.mark.asyncio
async def test_default_runner_skips_source_c_collection(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    collected_sources: list[Source] = []

    def collector_factory(source: Source, endpoint_config: object):
        collected_sources.append(source)

        async def collect() -> CollectionBatch:
            return _make_batch(source)

        return collect

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_factory=collector_factory,
    )

    await runner.run_cycle()
    assert Source.SOURCE_C not in collected_sources
    assert Source.SOURCE_B in collected_sources
    assert Source.SOURCE_A in collected_sources
    assert Source.SOURCE_D in collected_sources
    assert Source.SOURCE_E in collected_sources


class _RecordingTaxonomyRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.observations: list[tuple[Source, SourceCompetitionEvidence, datetime]] = []
        self._error = error

    def observe(
        self,
        source: Source,
        evidence: SourceCompetitionEvidence,
        observed_at: datetime,
    ) -> None:
        if self._error is not None:
            raise self._error
        self.observations.append((source, evidence, observed_at))

    def observe_batch(
        self,
        source: Source,
        evidences: Sequence[SourceCompetitionEvidence],
        observed_at: datetime,
    ) -> None:
        if self._error is not None:
            raise self._error
        for evidence in evidences:
            self.observations.append((source, evidence, observed_at))


@pytest.mark.asyncio
async def test_collector_runner_observes_distinct_non_null_taxonomy_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "taxonomy-observation.db"
    init_db(db_path)
    taxonomy_repo = _RecordingTaxonomyRepository()
    runner = CollectorRunner(
        snapshot_repo=SnapshotRepository(db_path),
        settings_repo=SystemSettingsRepository(db_path),
        collector_funcs={
            Source.SOURCE_C: AsyncMock(
                return_value=_make_batch(
                    Source.SOURCE_C,
                    ("competition-1", "competition-1", None, "competition-2"),
                )
            )
        },
        taxonomy_repo=taxonomy_repo,
    )

    await runner.run_cycle()

    assert [
        (source, evidence.competition_id, observed_at)
        for source, evidence, observed_at in taxonomy_repo.observations
    ] == [
        (Source.SOURCE_C, "competition-1", NOW),
        (Source.SOURCE_C, "competition-2", NOW),
    ]


@pytest.mark.asyncio
async def test_taxonomy_failure_does_not_change_source_health_or_other_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "taxonomy-failure.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    taxonomy_repo = _RecordingTaxonomyRepository(RuntimeError("taxonomy write failed"))
    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=SystemSettingsRepository(db_path),
        collector_funcs={
            Source.SOURCE_C: AsyncMock(
                return_value=_make_batch(Source.SOURCE_C, ("source_c-league",))
            ),
            Source.SOURCE_B: AsyncMock(
                return_value=_make_batch(Source.SOURCE_B, ("source_b-league",))
            ),
        },
        taxonomy_repo=taxonomy_repo,
    )

    with caplog.at_level("WARNING", logger="surebet.web.collector"):
        await runner.run_cycle()

    health = snapshot_repo.get_source_health()
    assert health[Source.SOURCE_C].status is SourceHealthStatus.HEALTHY
    assert health[Source.SOURCE_C].error_message is None
    assert health[Source.SOURCE_B].status is SourceHealthStatus.HEALTHY
    source_c_batch = snapshot_repo.get_latest_batch(Source.SOURCE_C)
    source_b_batch = snapshot_repo.get_latest_batch(Source.SOURCE_B)
    assert source_c_batch is not None and len(source_c_batch.events) == 1
    assert source_b_batch is not None and len(source_b_batch.events) == 1
    assert "Taxonomy observation failed for source_c" in caplog.text


@pytest.mark.asyncio
async def test_run_cycle_purges_expired_snapshots_once_after_all_sources_settle(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot_repo = _PurgeTrackingSnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)

    expired_kickoff = NOW - timedelta(hours=1)

    async def mock_source_c_expired() -> CollectionBatch:
        return _make_batch(Source.SOURCE_C, kickoff=expired_kickoff)

    async def mock_source_b_fresh() -> CollectionBatch:
        return _make_batch(Source.SOURCE_B)

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs={
            Source.SOURCE_C: mock_source_c_expired,
            Source.SOURCE_B: mock_source_b_fresh,
        },
    )

    # _pin_collector_runner_now (autouse) pins run_cycle()'s purge call to NOW, so
    # expired_kickoff (NOW - 1h) is reliably past the purge boundary (NOW - grace)
    # and the fresh Source B event (kickoff=NOW) reliably survives it.
    await runner.run_cycle()

    # Cleanup runs exactly once per cycle, after asyncio.gather() settles both
    # source tasks, so a single source task cannot race cleanup against another save.
    assert len(snapshot_repo.purge_calls) == 1
    assert snapshot_repo.purge_calls[0] == NOW

    source_c_batch = snapshot_repo.get_latest_batch(Source.SOURCE_C)
    assert source_c_batch is not None
    assert source_c_batch.events == ()

    source_b_batch = snapshot_repo.get_latest_batch(Source.SOURCE_B)
    assert source_b_batch is not None
    assert len(source_b_batch.events) == 1

    source_c_health = snapshot_repo.get_source_health()[Source.SOURCE_C]
    assert source_c_health.status == SourceHealthStatus.HEALTHY
    assert source_c_health.event_count == 0


def _make_runner(
    tmp_path: Path, *, notification_cycle: AsyncMock | None = None
) -> tuple[CollectorRunner, SnapshotRepository]:
    db_path = tmp_path / "notification-cycle.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs={Source.SOURCE_C: AsyncMock(return_value=_make_batch(Source.SOURCE_C))},
        notification_cycle=notification_cycle,
    )
    return runner, snapshot_repo


@pytest.mark.asyncio
async def test_notification_callback_runs_after_purge_and_does_not_call_telegram(
    tmp_path: Path,
) -> None:
    callback = AsyncMock()
    runner, _snapshot_repo = _make_runner(tmp_path, notification_cycle=callback)

    await runner.run_cycle()

    callback.assert_awaited_once_with(NOW)


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_collection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    callback = AsyncMock(side_effect=RuntimeError("notification evaluate failed"))
    runner, snapshot_repo = _make_runner(tmp_path, notification_cycle=callback)

    with caplog.at_level("ERROR", logger="surebet.web.collector"):
        await runner.run_cycle()

    assert snapshot_repo.get_latest_batch(Source.SOURCE_C) is not None
    assert "Notification evaluation failed" in caplog.text


@pytest.mark.asyncio
async def test_no_notification_callback_is_a_no_op(tmp_path: Path) -> None:
    runner, snapshot_repo = _make_runner(tmp_path, notification_cycle=None)

    await runner.run_cycle()

    assert snapshot_repo.get_latest_batch(Source.SOURCE_C) is not None


@pytest.mark.asyncio
async def test_run_cycle_cleans_up_expired_auth_sessions_and_rate_limit_rows(
    tmp_path: Path,
) -> None:
    """Spec section 8 requires expired rows to be periodically deleted. The
    cleanup methods on AuthRepository and AuthRateLimitRepository are
    correctly implemented and unit-tested in isolation, but have no
    production caller unless run_cycle() invokes them here, alongside the
    existing snapshot purge, on every collection cycle."""
    db_path = tmp_path / "auth-cleanup.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    auth_repo = MagicMock()
    auth_rate_limit_repo = MagicMock()

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs={Source.SOURCE_C: AsyncMock(return_value=_make_batch(Source.SOURCE_C))},
        auth_repo=auth_repo,
        auth_rate_limit_repo=auth_rate_limit_repo,
    )

    await runner.run_cycle()

    auth_repo.cleanup_expired_sessions.assert_called_once_with(NOW)
    auth_rate_limit_repo.cleanup_expired.assert_called_once_with(NOW)


@pytest.mark.asyncio
async def test_run_cycle_without_auth_repos_does_not_raise(tmp_path: Path) -> None:
    """auth_repo/auth_rate_limit_repo remain optional so existing callers and
    tests that construct a CollectorRunner without them keep working."""
    runner, snapshot_repo = _make_runner(tmp_path)

    await runner.run_cycle()

    assert snapshot_repo.get_latest_batch(Source.SOURCE_C) is not None


@pytest.mark.asyncio
async def test_auxiliary_failure_cannot_change_existing_main_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auxiliary-isolation.db"
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    existing = _make_batch(Source.SOURCE_A)
    snapshot_repo.save_batch(existing)

    async def failing_auxiliary() -> CollectionBatch:
        raise RuntimeError("isolated auxiliary failure")

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        collector_funcs={Source.SOURCE_C: AsyncMock(return_value=_make_batch(Source.SOURCE_C))},
        auxiliary_collector_funcs={Source.SOURCE_D: failing_auxiliary},
    )

    await runner.run_cycle()
    await runner.run_auxiliary_cycle()

    assert snapshot_repo.get_latest_batch(Source.SOURCE_A) == existing
    assert snapshot_repo.get_latest_batch(Source.SOURCE_C) is not None
    health = snapshot_repo.get_source_health()
    assert health[Source.SOURCE_D].error_message == "isolated auxiliary failure"


def test_default_collector_factory_configures_tracked_tournament_ids() -> None:
    from surebet.web.collector_runner import default_collector_factory
    from tests.conftest import example_endpoint

    endpoint = example_endpoint(Source.SOURCE_B)

    with (
        patch("surebet.web.collector_runner.SourceBCollector") as mock_source_b,
        patch("surebet.web.collector_runner.SourceCCollector") as mock_source_c,
        patch("surebet.web.collector_runner.SourceECollector") as mock_source_e,
        patch("surebet.web.collector_runner.SourceFCollector") as mock_source_f,
    ):
        default_collector_factory(Source.SOURCE_B, endpoint)
        _, kwargs_source_b = mock_source_b.call_args
        assert "910000000000000109" in kwargs_source_b.get("tournament_ids", ())

        default_collector_factory(Source.SOURCE_C, endpoint)
        _, kwargs_source_c = mock_source_c.call_args
        assert "910000000000000109" in kwargs_source_c.get("tournament_ids", ())

        default_collector_factory(Source.SOURCE_E, endpoint)
        _, kwargs_source_e = mock_source_e.call_args
        assert "910000000000000109" in kwargs_source_e.get("tournament_ids", ())

        default_collector_factory(Source.SOURCE_F, endpoint)
        _, kwargs_source_f = mock_source_f.call_args
        assert "910000000000000109" in kwargs_source_f.get("tournament_ids", ())
