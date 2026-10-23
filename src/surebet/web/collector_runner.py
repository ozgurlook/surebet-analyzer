"""Background collector runner for scheduled and on-demand source collection."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from surebet.domain.enums import ACTIVE_COLLECTION_SOURCES, Source
from surebet.sources.endpoints import SourceEndpointConfig, load_source_endpoints
from surebet.sources.models import CollectionBatch
from surebet.sources.source_a.collector import SourceACollector
from surebet.sources.source_b.collector import SourceBCollector
from surebet.sources.source_c.collector import SourceCCollector
from surebet.sources.source_d.collector import SourceDCollector
from surebet.sources.source_e.collector import SourceECollector
from surebet.sources.source_f.collector import SourceFCollector
from surebet.storage.auth import AuthRepository
from surebet.storage.auth_rate_limit import AuthRateLimitRepository
from surebet.storage.settings import SystemSettingsRepository
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import SourceCompetitionEvidence
from surebet.taxonomy.tracked import load_tracked_tournament_ids

logger = logging.getLogger("surebet.web.collector")

CollectorFunc = Callable[[], Awaitable[CollectionBatch]]
CollectorFactory = Callable[[Source, SourceEndpointConfig], CollectorFunc]
EndpointLoader = Callable[[Source], SourceEndpointConfig]
NotificationCycle = Callable[[datetime], Awaitable[None]]
CollectorCloser = Callable[[], Awaitable[None]]


def default_collector_factory(
    source: Source,
    endpoint_config: SourceEndpointConfig,
) -> CollectorFunc:
    if source is Source.SOURCE_C:
        return SourceCCollector(
            endpoint_config=endpoint_config,
            timeout_seconds=25.0,
            tournament_ids=load_tracked_tournament_ids(source),
        ).collect
    if source is Source.SOURCE_B:
        return SourceBCollector(
            endpoint_config=endpoint_config,
            timeout_seconds=25.0,
            tournament_ids=load_tracked_tournament_ids(source),
        ).collect
    if source is Source.SOURCE_A:
        return SourceACollector(endpoint_config=endpoint_config, timeout_seconds=25.0).collect
    if source is Source.SOURCE_D:
        return SourceDCollector(endpoint_config=endpoint_config, timeout_seconds=25.0).collect
    if source is Source.SOURCE_E:
        return SourceECollector(
            endpoint_config=endpoint_config,
            timeout_seconds=25.0,
            tournament_ids=load_tracked_tournament_ids(source),
        ).collect
    if source is Source.SOURCE_F:
        return SourceFCollector(
            endpoint_config=endpoint_config,
            timeout_seconds=25.0,
            tournament_ids=load_tracked_tournament_ids(source),
        ).collect
    raise ValueError("unsupported source")


class CollectorRunner:
    """Coordinates periodic and on-demand concurrent collection with source isolation."""

    def __init__(
        self,
        snapshot_repo: SnapshotRepository,
        settings_repo: SystemSettingsRepository,
        collector_funcs: dict[Source, CollectorFunc] | None = None,
        endpoint_loader: EndpointLoader = load_source_endpoints,
        collector_factory: CollectorFactory = default_collector_factory,
        cooldown_seconds: float = 10.0,
        taxonomy_repo: TaxonomyRepository | None = None,
        notification_cycle: NotificationCycle | None = None,
        auth_repo: AuthRepository | None = None,
        auth_rate_limit_repo: AuthRateLimitRepository | None = None,
        active_sources: tuple[Source, ...] | None = None,
        auxiliary_collector_funcs: dict[Source, CollectorFunc] | None = None,
        auxiliary_collector_closers: tuple[CollectorCloser, ...] = (),
    ) -> None:
        self._snapshot_repo = snapshot_repo
        self._settings_repo = settings_repo
        self._notification_cycle = notification_cycle
        self._collector_funcs = collector_funcs
        self._endpoint_loader = endpoint_loader
        self._taxonomy_repo = taxonomy_repo
        self._auth_repo = auth_repo
        self._auth_rate_limit_repo = auth_rate_limit_repo
        self._collector_factory = collector_factory
        self._cooldown_seconds = cooldown_seconds
        self._active_sources = (
            active_sources if active_sources is not None else ACTIVE_COLLECTION_SOURCES
        )
        self._auxiliary_collector_funcs = auxiliary_collector_funcs or {}
        self._auxiliary_collector_closers = auxiliary_collector_closers
        self._last_manual_refresh: float = 0.0
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._auxiliary_tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

    def _persist_source_batch(
        self,
        source: Source,
        batch: CollectionBatch,
        duration_ms: int,
    ) -> None:
        self._snapshot_repo.save_batch(batch, duration_ms=duration_ms)
        try:
            if self._taxonomy_repo is not None:
                observed_evidences: list[SourceCompetitionEvidence] = []
                seen_ids: set[str] = set()
                for event in batch.events:
                    evidence = event.source_competition
                    if evidence is None or evidence.competition_id in seen_ids:
                        continue
                    seen_ids.add(evidence.competition_id)
                    observed_evidences.append(evidence)
                if observed_evidences:
                    if hasattr(self._taxonomy_repo, "observe_batch"):
                        self._taxonomy_repo.observe_batch(
                            source, observed_evidences, batch.finished_at
                        )
                    else:
                        for evidence in observed_evidences:
                            self._taxonomy_repo.observe(source, evidence, batch.finished_at)
        except Exception as error:  # noqa: BLE001 - taxonomy must not affect source health
            logger.warning("Taxonomy observation failed for %s: %s", source.value, error)

    async def _collect_source(self, source: Source, func: CollectorFunc) -> None:
        start_time = datetime.now(UTC)
        t0 = time.monotonic()
        try:
            batch = await func()
            duration_ms = int((time.monotonic() - t0) * 1000)
            await asyncio.to_thread(self._persist_source_batch, source, batch, duration_ms)
            logger.info(
                "Collected %s: %d events in %dms", source.value, len(batch.events), duration_ms
            )
        except Exception as error:  # noqa: BLE001 - each source is an isolation boundary
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_msg = str(error) or error.__class__.__name__
            self._snapshot_repo.record_failure(
                source=source,
                started_at=start_time,
                error_message=error_msg,
                duration_ms=duration_ms,
            )
            logger.warning("Collection failed for %s: %s", source.value, error_msg)

    async def run_cycle(self) -> None:
        """Run all collectors concurrently, isolating errors between sources."""
        async with self._lock:
            if self._collector_funcs is not None:
                tasks = [
                    self._collect_source(source, func)
                    for source, func in self._collector_funcs.items()
                ]
            else:
                tasks = [
                    self._collect_source(source, self._current_collector_func(source))
                    for source in self._active_sources
                ]
            await asyncio.gather(*tasks, return_exceptions=True)
            # Run once, after every source task has settled, so a single source task
            # cannot race retention cleanup against another source's save.
            now = datetime.now(UTC)
            self._snapshot_repo.purge_expired(now)
            if self._auth_repo is not None:
                self._auth_repo.cleanup_expired_sessions(now)
            if self._auth_rate_limit_repo is not None:
                self._auth_rate_limit_repo.cleanup_expired(now)
            if self._notification_cycle is not None:
                try:
                    await self._notification_cycle(now)
                except Exception as error:  # noqa: BLE001 - notifications must not fail collection
                    logger.error("Notification evaluation failed: %s", error)

    def _current_collector_func(self, source: Source) -> CollectorFunc:
        async def collect() -> CollectionBatch:
            return await self._collect_current(source)

        return collect

    async def _collect_current(self, source: Source) -> CollectionBatch:
        endpoint_config = self._endpoint_loader(source)
        return await self._collector_factory(source, endpoint_config)()

    async def trigger_refresh(self) -> bool:
        """Attempt manual refresh. Return False if currently in cooldown."""
        now_ts = time.monotonic()
        if now_ts - self._last_manual_refresh < self._cooldown_seconds:
            return False

        self._last_manual_refresh = now_ts
        await self.run_cycle()
        return True

    @property
    def enabled_sources(self) -> tuple[Source, ...]:
        """Sources visible as enabled, including independently scheduled collectors."""

        return tuple(dict.fromkeys((*self._active_sources, *self._auxiliary_collector_funcs)))

    async def run_auxiliary_cycle(self) -> None:
        """Collect auxiliary sources without making the main cycle wait for them."""

        await asyncio.gather(
            *(
                self._collect_source(source, collect)
                for source, collect in self._auxiliary_collector_funcs.items()
            ),
            return_exceptions=True,
        )

    async def start(self) -> None:
        """Start the background collection loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._auxiliary_tasks = [
            asyncio.create_task(self._auxiliary_loop(source, collect))
            for source, collect in self._auxiliary_collector_funcs.items()
        ]

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        for task in self._auxiliary_tasks:
            task.cancel()
        if self._auxiliary_tasks:
            await asyncio.gather(*self._auxiliary_tasks, return_exceptions=True)
        self._auxiliary_tasks = []
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        for close in self._auxiliary_collector_closers:
            try:
                await close()
            except Exception as error:  # noqa: BLE001 - one cleanup must not block others
                logger.warning("Auxiliary collector cleanup failed: %s", error)

    async def _auxiliary_loop(self, source: Source, collect: CollectorFunc) -> None:
        while self._running:
            try:
                await self._collect_source(source, collect)
            except asyncio.CancelledError:
                break
            settings = self._settings_repo.get()
            try:
                await asyncio.sleep(settings.refresh_interval_seconds)
            except asyncio.CancelledError:
                break

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 - keep the background loop alive
                logger.error("Error in collector loop: %s", e)

            settings = self._settings_repo.get()
            interval = settings.refresh_interval_seconds
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
