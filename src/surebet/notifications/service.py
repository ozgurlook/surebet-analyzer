"""Post-collection-cycle coordinator: builds evaluations and reconciles lifecycle state.

Runs synchronously in the collector's cycle callback. Never awaits Telegram I/O --
delivery is the separate `TelegramOutboxDispatcher`'s job.

Evaluates every deliverable user's Telegram opportunities from ONE common catalog
snapshot per cycle. The snapshot is built once, broadest (`tracked_only=False`);
each user's tracked-only preference and saved Opportunities-scope taxonomy
filters are then applied as an in-memory filter over that same snapshot. This is
equivalent to calling `CatalogService.build(tracked_only=True)` per user for the
matched events that survive the filter -- `match_events` only ever clusters
events that already share a resolved `league_id`, so excluding untracked-league
events from the source batches before matching (which is what `tracked_only=True`
does at the batch stage) can never change which tracked-league events match each
other. Filtering the broad snapshot's `matched_events` by `league_id` membership
after the fact therefore reproduces the same matched set without paying for a
second `CatalogService.build()` call per user.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta

from surebet.catalog.service import CatalogService
from surebet.domain.enums import MarketType, Source
from surebet.matching.models import MatchedEvent
from surebet.storage.notifications import NotificationRepository, TelegramNotificationSettings
from surebet.storage.settings import (
    SystemSettings,
    SystemSettingsRepository,
    UserPreferencesRepository,
)
from surebet.storage.snapshots import SourceHealthStatus
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.storage.user_filters import FilterScope, UserFilterRepository

from .cards.engine import LifecycleEngine
from .cards.thresholds import (
    CARD_RETENTION_SECONDS,
    MIN_EDIT_INTERVAL_SECONDS,
    OUTBOX_RETENTION_SECONDS,
    REVIVAL_WINDOW_SECONDS,
    meets_threshold,
)
from .evaluator import Calculator, evaluate_event_by_market
from .models import (
    CardState,
    DualPolicyEvaluation,
    LifecycleStatus,
    OutboxActionDraft,
    OutboxActionType,
)
from .render.alerts import AlertRenderer
from .render.card import CardRenderer
from .render.format import RenderedMessage

logger = logging.getLogger("surebet.notifications.service")


def _wanted_ids(saved: frozenset[str]) -> set[str] | None:
    """An empty saved filter set means "no narrowing", matching the Opportunities route."""
    return set(saved) if saved else None


MAX_KICKOFF_HORIZON = timedelta(days=2)


def _is_within_kickoff_horizon(kickoff: datetime, now: datetime, horizon: timedelta) -> bool:
    kickoff_utc = (
        kickoff.astimezone(UTC) if kickoff.tzinfo is not None else kickoff.replace(tzinfo=UTC)
    )
    now_utc = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return kickoff_utc <= now_utc + horizon


class TelegramNotificationService:
    """Reconciles current Telegram opportunity lifecycles, once per user, per collection cycle."""

    def __init__(
        self,
        catalog_service: CatalogService,
        settings_repo: SystemSettingsRepository,
        notification_repo: NotificationRepository,
        *,
        token_configured: Callable[[], bool],
        user_preferences_repo: UserPreferencesRepository,
        user_filter_repo: UserFilterRepository,
        taxonomy_repo: TaxonomyRepository,
        engine: LifecycleEngine | None = None,
        card_renderer: CardRenderer | None = None,
        alert_renderer: AlertRenderer | None = None,
        calculator: Calculator | None = None,
        max_kickoff_horizon: timedelta = MAX_KICKOFF_HORIZON,
    ) -> None:
        self._catalog_service = catalog_service
        self._settings_repo = settings_repo
        self._notification_repo = notification_repo
        self._token_configured = token_configured
        self._user_preferences_repo = user_preferences_repo
        self._user_filter_repo = user_filter_repo
        self._taxonomy_repo = taxonomy_repo
        self._engine = engine or LifecycleEngine()
        self._card_renderer = card_renderer
        self._alert_renderer = alert_renderer or AlertRenderer()
        self._calculator = calculator
        self._max_kickoff_horizon = max_kickoff_horizon

    def evaluate_cycle(self, now: datetime) -> None:
        if not self._token_configured():
            return

        deliverable_users = self._notification_repo.list_enabled_settings()
        if not deliverable_users:
            return

        system_settings = self._settings_repo.get()

        # Built exactly once per cycle, broadest possible (`tracked_only=False`):
        # per-user tracked-only/taxonomy restrictions are applied in memory below,
        # never by calling `build()` again for a different user.
        snapshot = self._catalog_service.build(now, tracked_only=False)
        health: dict[Source, SourceHealthStatus] = {
            source: source_health.status for source, source_health in snapshot.source_health.items()
        }
        tracked_league_ids = self._taxonomy_repo.tracked_league_ids()

        card_renderer = self._card_renderer or CardRenderer()

        # One global sweep per cycle (not per user) keeps the card table from
        # growing without bound; everything it removes is long past reviving.
        self._notification_repo.purge_retired_cards(now - timedelta(seconds=CARD_RETENTION_SECONDS))
        self._notification_repo.purge_completed_outbox(
            now - timedelta(seconds=OUTBOX_RETENTION_SECONDS)
        )

        evaluation_cache: dict[tuple[str, int], dict[MarketType, DualPolicyEvaluation]] = {}

        for telegram in deliverable_users:
            try:
                self._evaluate_user_cycle(
                    telegram,
                    snapshot.matched_events,
                    health,
                    system_settings,
                    tracked_league_ids,
                    card_renderer,
                    now,
                    evaluation_cache,
                )
            except Exception:
                logger.exception(
                    "Telegram evaluation failed for user_id=%s; other users unaffected",
                    telegram.user_id,
                )

    def _evaluate_user_cycle(
        self,
        telegram: TelegramNotificationSettings,
        matched_events: Iterable[MatchedEvent],
        health: Mapping[Source, SourceHealthStatus],
        system_settings: SystemSettings,
        tracked_league_ids: frozenset[str],
        card_renderer: CardRenderer,
        now: datetime,
        evaluation_cache: dict[tuple[str, int], dict[MarketType, DualPolicyEvaluation]]
        | None = None,
    ) -> None:
        preferences = self._user_preferences_repo.get(telegram.user_id)
        saved_filters = self._user_filter_repo.get(telegram.user_id, FilterScope.OPPORTUNITIES)
        wanted_countries = _wanted_ids(saved_filters.country_ids)
        wanted_leagues = _wanted_ids(saved_filters.league_ids)

        # Alerts follow the same locked scope as the rest of the product: only
        # the curated tracked competitions are ever evaluated.
        candidate_events = [
            event
            for event in matched_events
            if event.league_id in tracked_league_ids
            and (wanted_countries is None or event.country_id in wanted_countries)
            and (wanted_leagues is None or event.league_id in wanted_leagues)
            and _is_within_kickoff_horizon(event.kickoff, now, self._max_kickoff_horizon)
        ]

        cache = evaluation_cache if evaluation_cache is not None else {}
        evaluations: dict[str, DualPolicyEvaluation] = {}
        budget_cents = preferences.default_budget_cents
        for event in candidate_events:
            cache_key = (event.matched_id, budget_cents)
            if cache_key in cache:
                per_market = cache[cache_key]
            else:
                per_market = evaluate_event_by_market(
                    event,
                    budget_cents,
                    now,
                    system_settings.staleness_limit_seconds,
                    **({"calculator": self._calculator} if self._calculator is not None else {}),
                )
                cache[cache_key] = per_market
            for evaluated in per_market.values():
                if evaluated.market_type is MarketType.FULL_TIME_RESULT and not telegram.notify_1x2:
                    continue
                if evaluated.market_type is MarketType.TOTAL_GOALS_25 and not telegram.notify_ou25:
                    continue
                evaluations[evaluated.card_key] = evaluated

        self._reconcile(telegram, evaluations, health, card_renderer, now)

    def _reconcile(
        self,
        settings: TelegramNotificationSettings,
        evaluations: Mapping[str, DualPolicyEvaluation],  # keyed by card_key
        health: Mapping[Source, SourceHealthStatus],
        card_renderer: CardRenderer,
        now: datetime,
    ) -> None:
        assert settings.chat_id is not None
        existing = self._notification_repo.list_current_cards(
            settings.user_id,
            retired_before=now - timedelta(seconds=REVIVAL_WINDOW_SECONDS),
        )
        transitions: list[tuple[CardState, tuple[OutboxActionDraft, ...]]] = []

        for key in set(evaluations) | set(existing):
            evaluation = evaluations.get(key)
            prior = existing.get(key)

            if evaluation is not None and not _is_within_kickoff_horizon(
                evaluation.selected.kickoff, now, self._max_kickoff_horizon
            ):
                evaluation = None

            market_type = (
                evaluation.market_type
                if evaluation is not None
                else (prior.market_type if prior is not None else None)
            )
            if market_type is MarketType.FULL_TIME_RESULT and not settings.notify_1x2:
                if prior is not None and prior.status is not LifecycleStatus.CLOSED:
                    closed_state = prior.model_copy(
                        update={
                            "status": LifecycleStatus.CLOSED,
                            "closed_at": now,
                            "last_seen_at": now,
                        }
                    )
                    transitions.append((closed_state, ()))
                continue
            if market_type is MarketType.TOTAL_GOALS_25 and not settings.notify_ou25:
                if prior is not None and prior.status is not LifecycleStatus.CLOSED:
                    closed_state = prior.model_copy(
                        update={
                            "status": LifecycleStatus.CLOSED,
                            "closed_at": now,
                            "last_seen_at": now,
                        }
                    )
                    transitions.append((closed_state, ()))
                continue

            if prior is None and (
                evaluation is None
                or not meets_threshold(evaluation.selected, settings.minimum_profit_bps)
            ):
                continue

            decision = self._engine.decide(prior, evaluation, health, settings, now)
            if not decision.requested_actions and decision.state == prior:
                # Nothing to say and nothing to record: rendering and
                # re-UPSERTing this card would be pure per-cycle waste.
                continue
            is_muted = bool(
                settings.chat_id
                and self._notification_repo.is_card_muted(settings.chat_id, key, now)
            )
            is_fav = bool(
                settings.chat_id and self._notification_repo.is_favorite(settings.chat_id, key)
            )
            rendered = card_renderer.render(decision.state, now, is_muted=is_muted, is_fav=is_fav)
            state = decision.state.model_copy(update={"rendered_fingerprint": rendered.fingerprint})
            drafts = self._drafts(
                prior, state, decision.requested_actions, rendered, now, is_muted=is_muted
            )
            transitions.append((state, drafts))

        self._notification_repo.apply_transitions(transitions)

    def _drafts(
        self,
        prior: CardState | None,
        state: CardState,
        actions: tuple[OutboxActionType, ...],
        rendered: RenderedMessage,
        now: datetime,
        *,
        is_muted: bool = False,
    ) -> tuple[OutboxActionDraft, ...]:
        drafts: list[OutboxActionDraft] = []
        status_changed = prior is None or prior.status is not state.status
        undelivered = state.message_id is None

        for action in actions:
            if is_muted and action in (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_ALERT):
                continue
            if action in (OutboxActionType.SEND_CARD, OutboxActionType.EDIT_CARD):
                # Only edit when the user would actually see a difference.
                if (
                    not undelivered
                    and prior is not None
                    and prior.rendered_fingerprint == rendered.fingerprint
                ):
                    continue
                if undelivered:
                    action = OutboxActionType.SEND_CARD
                due = (
                    now
                    if undelivered or status_changed or state.last_edit_at is None
                    else max(
                        now,
                        state.last_edit_at + timedelta(seconds=MIN_EDIT_INTERVAL_SECONDS),
                    )
                )
                drafts.append(
                    OutboxActionDraft(
                        action=action,
                        text=rendered.text,
                        fingerprint=rendered.fingerprint,
                        next_attempt_at=due,
                        message_id=state.message_id,
                        reply_markup=rendered.reply_markup,
                    )
                )
                continue

            if action is OutboxActionType.SEND_ALERT:
                assert prior is not None
                message = self._alert_renderer.render_change(state, prior.anchor_bps)
                silent = False
            elif action is OutboxActionType.SEND_REVIVED:
                message = self._alert_renderer.render_revived(state)
                silent = True
            else:
                message = self._alert_renderer.render_closed(state)
                silent = False

            drafts.append(
                OutboxActionDraft(
                    action=action,
                    text=message.text,
                    fingerprint=message.fingerprint,
                    next_attempt_at=now,
                    message_id=state.message_id,
                    silent=silent,
                    reply_markup=message.reply_markup,
                )
            )
        return tuple(drafts)
