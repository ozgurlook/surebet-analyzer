"""FastAPI web application factory with static file serving and lifecycle."""

import asyncio
import hashlib
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from surebet.accounting.service import AccountingService
from surebet.accounting.storage import AccountingRepository
from surebet.catalog.service import CatalogService
from surebet.domain.enums import Source
from surebet.notifications.dispatcher import TelegramOutboxDispatcher
from surebet.notifications.listener import TelegramBotListener
from surebet.notifications.service import TelegramNotificationService
from surebet.notifications.telegram import TelegramClient
from surebet.storage.auth import AuthRepository
from surebet.storage.auth_rate_limit import AuthRateLimitRepository
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository
from surebet.storage.settings import SystemSettingsRepository, UserPreferencesRepository
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.storage.user_filters import UserFilterRepository
from surebet.taxonomy.tracked import TrackedCompetitionRegistry
from surebet.web.collector_runner import (
    CollectorFactory,
    CollectorFunc,
    CollectorRunner,
    default_collector_factory,
)
from surebet.web.routes.accounting import router as accounting_router
from surebet.web.routes.auth import router as auth_router
from surebet.web.routes.dashboard import router as dashboard_router
from surebet.web.routes.events import router as events_router
from surebet.web.routes.mappings import router as mappings_router
from surebet.web.routes.notifications import router as notifications_router
from surebet.web.routes.opportunities import router as opportunities_router
from surebet.web.routes.preferences import router as preferences_router
from surebet.web.routes.settings import router as settings_router
from surebet.web.routes.sources import router as sources_router
from surebet.web.routes.taxonomy import router as taxonomy_router

_TELEGRAM_TOKEN_ENV_VAR = "SUREBET_TELEGRAM_BOT_TOKEN"
_REGISTRATION_CODE_ENV_VAR = "SUREBET_REGISTRATION_CODE"
_COOKIE_SECURE_ENV_VAR = "SUREBET_COOKIE_SECURE"


def create_app(
    db_path: Path | str = "surebet.db",
    collector_runner: CollectorRunner | None = None,
    collector_factory: CollectorFactory = default_collector_factory,
    collector_funcs: dict[Source, CollectorFunc] | None = None,
    enable_background_runner: bool = False,
    static_dir: Path | str | None = None,
    tracked_registry: TrackedCompetitionRegistry | None = None,
) -> FastAPI:
    path = Path(db_path)
    init_db(path)

    auth_repo = AuthRepository(path)
    auth_rate_limit_repo = AuthRateLimitRepository(path)

    # Read once at process start. `registration_code` is a local variable
    # only -- it is never assigned to app.state -- so the raw secret cannot
    # later be read back off the app or accidentally serialized/logged.
    # Only a SHA-256 digest (needed for the timing-safe comparison at
    # registration time) and a plain boolean are kept as long-lived state.
    registration_code = os.environ.get(_REGISTRATION_CODE_ENV_VAR)
    registration_enabled = bool(registration_code)
    registration_code_digest = (
        hashlib.sha256(registration_code.encode("utf-8")).digest() if registration_code else None
    )
    cookie_secure = os.environ.get(_COOKIE_SECURE_ENV_VAR, "").strip().lower() == "true"

    user_preferences_repo = UserPreferencesRepository(path)
    system_settings_repo = SystemSettingsRepository(path)
    snapshot_repo = SnapshotRepository(path)
    taxonomy_repo = TaxonomyRepository(path, tracked_registry)
    user_filter_repo = UserFilterRepository(path, taxonomy_repo)
    catalog_service = CatalogService(snapshot_repo, taxonomy_repo)
    accounting_repo = AccountingRepository(path)
    accounting_service = AccountingService(accounting_repo)

    notification_repo = NotificationRepository(path)
    bot_token = os.environ.get(_TELEGRAM_TOKEN_ENV_VAR)
    telegram_client = TelegramClient(bot_token or "")
    notification_service = TelegramNotificationService(
        catalog_service=catalog_service,
        settings_repo=system_settings_repo,
        notification_repo=notification_repo,
        token_configured=lambda: bot_token is not None,
        user_preferences_repo=user_preferences_repo,
        user_filter_repo=user_filter_repo,
        taxonomy_repo=taxonomy_repo,
    )
    telegram_dispatcher = TelegramOutboxDispatcher(notification_repo, telegram_client)
    telegram_listener = (
        TelegramBotListener(
            client=telegram_client,
            repo=notification_repo,
        )
        if bot_token
        else None
    )

    async def notification_cycle(now: datetime) -> None:
        await asyncio.to_thread(notification_service.evaluate_cycle, now)
        telegram_dispatcher.wake()

    if collector_runner is not None:
        runner = collector_runner
    else:
        runner = CollectorRunner(
            snapshot_repo=snapshot_repo,
            settings_repo=system_settings_repo,
            collector_funcs=collector_funcs,
            collector_factory=collector_factory,
            taxonomy_repo=taxonomy_repo,
            notification_cycle=notification_cycle,
            auth_repo=auth_repo,
            auth_rate_limit_repo=auth_rate_limit_repo,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.db_path = path
        app.state.auth_repo = auth_repo
        app.state.auth_rate_limit_repo = auth_rate_limit_repo
        app.state.registration_enabled = registration_enabled
        app.state.registration_code_digest = registration_code_digest
        app.state.cookie_secure = cookie_secure
        app.state.user_preferences_repo = user_preferences_repo
        app.state.system_settings_repo = system_settings_repo
        app.state.snapshot_repo = snapshot_repo
        app.state.taxonomy_repo = taxonomy_repo
        app.state.user_filter_repo = user_filter_repo
        app.state.catalog_service = catalog_service
        app.state.notification_repo = notification_repo
        app.state.notification_service = notification_service
        app.state.accounting_repo = accounting_repo
        app.state.accounting_service = accounting_service
        app.state.telegram_client = telegram_client
        app.state.telegram_dispatcher = telegram_dispatcher
        app.state.telegram_listener = telegram_listener
        app.state.telegram_token_configured = bot_token is not None
        app.state.collector_runner = runner

        if enable_background_runner:
            await telegram_dispatcher.start()
            if telegram_listener is not None:
                telegram_listener.start()
            await runner.start()
        yield
        if enable_background_runner:
            await runner.stop()
            if telegram_listener is not None:
                await telegram_listener.stop()
            await telegram_dispatcher.stop()
        await telegram_client.aclose()

    app = FastAPI(
        title="Surebet Analyzer API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.db_path = path
    app.state.auth_repo = auth_repo
    app.state.auth_rate_limit_repo = auth_rate_limit_repo
    app.state.registration_enabled = registration_enabled
    app.state.registration_code_digest = registration_code_digest
    app.state.cookie_secure = cookie_secure
    app.state.user_preferences_repo = user_preferences_repo
    app.state.system_settings_repo = system_settings_repo
    app.state.snapshot_repo = snapshot_repo
    app.state.taxonomy_repo = taxonomy_repo
    app.state.user_filter_repo = user_filter_repo
    app.state.catalog_service = catalog_service
    app.state.notification_repo = notification_repo
    app.state.notification_service = notification_service
    app.state.accounting_repo = accounting_repo
    app.state.accounting_service = accounting_service
    app.state.telegram_client = telegram_client
    app.state.telegram_dispatcher = telegram_dispatcher
    app.state.telegram_token_configured = bot_token is not None
    app.state.collector_runner = runner

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API v1 routers directly on the main app so request.app.state is shared
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(opportunities_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(taxonomy_router, prefix="/api/v1")
    app.include_router(mappings_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(preferences_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(accounting_router, prefix="/api/v1")

    # Static frontend files
    dist_path = (
        Path(static_dir)
        if static_dir
        else Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
    )
    if dist_path.exists() and (dist_path / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            file_target = dist_path / full_path
            if file_target.is_file():
                return FileResponse(file_target)
            return FileResponse(dist_path / "index.html")

    return app
