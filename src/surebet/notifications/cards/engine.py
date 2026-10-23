"""Pure Telegram card lifecycle state machine. No SQL, no HTTP.

One card tracks one match in one market. Profit movement is measured against a
single anchor -- the level last reported to the user -- so a decrease and an
increase can never be emitted for the same cycle.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta

from surebet.domain.enums import Source
from surebet.storage.notifications import TelegramNotificationSettings
from surebet.storage.snapshots import SourceHealthStatus

from ..models import (
    CardState,
    DualPolicyEvaluation,
    LifecycleDecision,
    LifecycleStatus,
    OutboxActionType,
    card_key,
)
from .thresholds import (
    CLOSE_GRACE_SECONDS,
    REVIVAL_WINDOW_SECONDS,
    close_floor_bps,
    meets_threshold,
    profit_basis_points,
    should_alert,
)


class LifecycleEngine:
    """Decides the next card state and the outbox actions for one match+market."""

    def decide(
        self,
        prior: CardState | None,
        evaluation: DualPolicyEvaluation | None,
        health: Mapping[Source, SourceHealthStatus],
        settings: TelegramNotificationSettings,
        now: datetime,
    ) -> LifecycleDecision:
        if prior is None:
            if evaluation is None:
                raise ValueError("cannot open a card without a current evaluation")
            return self._open(evaluation, settings, now, generation=1)

        if prior.status is LifecycleStatus.CLOSED:
            return self._from_closed(prior, evaluation, settings, now)

        if not self._sources_healthy(prior, evaluation, health):
            return self._stale(prior, evaluation, now)

        if evaluation is not None and meets_threshold(
            evaluation.selected, settings.minimum_profit_bps
        ):
            return self._qualifying(prior, evaluation, settings, now)

        return self._below(prior, evaluation, settings, now)

    # -- state builders -------------------------------------------------

    def _open(
        self,
        evaluation: DualPolicyEvaluation,
        settings: TelegramNotificationSettings,
        now: datetime,
        *,
        generation: int,
        chat_id: int | None = None,
    ) -> LifecycleDecision:
        chat = chat_id if chat_id is not None else settings.chat_id
        assert chat is not None
        profit_bps = profit_basis_points(evaluation.selected)
        state = CardState(
            user_id=settings.user_id,
            chat_id=chat,
            card_key=card_key(evaluation.matched_id, evaluation.market_type),
            matched_id=evaluation.matched_id,
            market_type=evaluation.market_type,
            generation=generation,
            status=LifecycleStatus.NEW,
            message_id=None,
            evaluation=evaluation,
            first_profit_bps=profit_bps,
            current_profit_bps=profit_bps,
            peak_profit_bps=profit_bps,
            anchor_bps=profit_bps,
            last_alert_at=None,
            below_since=None,
            closed_at=None,
            revival_count=0,
            first_seen_at=now,
            last_seen_at=now,
            last_edit_at=None,
            rendered_fingerprint=None,
        )
        return LifecycleDecision(state=state, requested_actions=(OutboxActionType.SEND_CARD,))

    def _from_closed(
        self,
        prior: CardState,
        evaluation: DualPolicyEvaluation | None,
        settings: TelegramNotificationSettings,
        now: datetime,
    ) -> LifecycleDecision:
        qualifies = evaluation is not None and meets_threshold(
            evaluation.selected, settings.minimum_profit_bps
        )
        if not qualifies:
            return LifecycleDecision(state=prior, requested_actions=())

        assert evaluation is not None
        if evaluation.selected.kickoff <= now:
            return LifecycleDecision(state=prior, requested_actions=())

        within_window = prior.closed_at is not None and now - prior.closed_at <= timedelta(
            seconds=REVIVAL_WINDOW_SECONDS
        )
        if not within_window:
            return self._open(
                evaluation,
                settings,
                now,
                generation=prior.generation + 1,
                chat_id=prior.chat_id,
            )

        profit_bps = profit_basis_points(evaluation.selected)
        revived = prior.model_copy(
            update={
                "status": LifecycleStatus.ACTIVE,
                "evaluation": evaluation,
                "current_profit_bps": profit_bps,
                "peak_profit_bps": max(prior.peak_profit_bps, profit_bps),
                "anchor_bps": profit_bps,
                "below_since": None,
                "closed_at": None,
                "revival_count": prior.revival_count + 1,
                "last_seen_at": now,
            }
        )
        return LifecycleDecision(
            state=revived,
            requested_actions=(OutboxActionType.EDIT_CARD, OutboxActionType.SEND_REVIVED),
        )

    def _stale(
        self, prior: CardState, evaluation: DualPolicyEvaluation | None, now: datetime
    ) -> LifecycleDecision:
        """Frozen data must never move the anchor, the grace counter, or close a card.

        `below_since` is an absolute timestamp, so merely carrying it across the
        outage would let unobserved wall-clock minutes count toward
        CLOSE_GRACE_SECONDS and close a card on the first healthy observation
        after a blackout. Clearing it restarts the counter at the first FRESH
        below-floor observation, which is what "frozen" has to mean for a
        counter (spec section 5.3).

        `last_seen_at` likewise stops advancing: it is what the staleness banner
        counts from, and a banner that resets every cycle would forever claim
        the data is one minute old.
        """
        state = prior.model_copy(
            update={
                "status": LifecycleStatus.STALE,
                "evaluation": evaluation if evaluation is not None else prior.evaluation,
                "below_since": None,
            }
        )
        return LifecycleDecision(state=state, requested_actions=(OutboxActionType.EDIT_CARD,))

    def _qualifying(
        self,
        prior: CardState,
        evaluation: DualPolicyEvaluation,
        settings: TelegramNotificationSettings,
        now: datetime,
    ) -> LifecycleDecision:
        profit_bps = profit_basis_points(evaluation.selected)
        deliverable = prior.message_id is not None
        alerting = deliverable and should_alert(
            current_bps=profit_bps,
            anchor_bps=prior.anchor_bps,
            decrease_threshold_bps=settings.decrease_threshold_bps,
            increase_threshold_bps=settings.increase_threshold_bps,
            last_alert_at=prior.last_alert_at,
            cooldown_seconds=settings.alert_cooldown_seconds,
            now=now,
        )

        actions: list[OutboxActionType] = [OutboxActionType.EDIT_CARD]
        if alerting:
            actions.append(OutboxActionType.SEND_ALERT)

        state = prior.model_copy(
            update={
                "status": LifecycleStatus.ACTIVE,
                "evaluation": evaluation,
                "current_profit_bps": profit_bps,
                "peak_profit_bps": max(prior.peak_profit_bps, profit_bps),
                "anchor_bps": profit_bps if alerting else prior.anchor_bps,
                "last_alert_at": now if alerting else prior.last_alert_at,
                "below_since": None,
                "last_seen_at": now,
            }
        )
        return LifecycleDecision(state=state, requested_actions=tuple(actions))

    def _below(
        self,
        prior: CardState,
        evaluation: DualPolicyEvaluation | None,
        settings: TelegramNotificationSettings,
        now: datetime,
    ) -> LifecycleDecision:
        """Below the user threshold: watch, and close only after sustained collapse.

        `evaluation is None` with healthy sources means the market stopped producing a
        candidate at all -- treated as a below-floor observation, not a data gap.
        """
        next_evaluation = evaluation if evaluation is not None else prior.evaluation
        profit_bps = (
            profit_basis_points(evaluation.selected)
            if evaluation is not None
            else prior.current_profit_bps
        )
        floor = close_floor_bps(settings.minimum_profit_bps)
        below_floor = evaluation is None or profit_bps < floor
        below_since = (prior.below_since or now) if below_floor else None

        kickoff_passed = next_evaluation.selected.kickoff <= now
        grace_elapsed = below_since is not None and now - below_since >= timedelta(
            seconds=CLOSE_GRACE_SECONDS
        )

        if kickoff_passed or grace_elapsed:
            closed = prior.model_copy(
                update={
                    "status": LifecycleStatus.CLOSED,
                    "evaluation": next_evaluation,
                    "current_profit_bps": profit_bps,
                    "below_since": below_since,
                    "closed_at": now,
                    "last_seen_at": now if evaluation is not None else prior.last_seen_at,
                }
            )
            return LifecycleDecision(
                state=closed,
                requested_actions=(OutboxActionType.EDIT_CARD, OutboxActionType.SEND_CLOSED),
            )

        watching = prior.model_copy(
            update={
                "status": LifecycleStatus.WATCH,
                "evaluation": next_evaluation,
                "current_profit_bps": profit_bps,
                "below_since": below_since,
                "last_seen_at": now if evaluation is not None else prior.last_seen_at,
            }
        )
        return LifecycleDecision(state=watching, requested_actions=(OutboxActionType.EDIT_CARD,))

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _sources_healthy(
        prior: CardState,
        evaluation: DualPolicyEvaluation | None,
        health: Mapping[Source, SourceHealthStatus],
    ) -> bool:
        relevant = (
            evaluation.relevant_sources
            if evaluation is not None
            else prior.evaluation.relevant_sources
        )
        return all(health.get(source) is SourceHealthStatus.HEALTHY for source in relevant)
