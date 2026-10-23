import random
from datetime import timedelta

from surebet.notifications.cards.engine import LifecycleEngine
from surebet.notifications.cards.thresholds import close_floor_bps
from surebet.notifications.models import LifecycleStatus, OutboxActionType


def _random_walk(seed: int, length: int = 80) -> list[int]:
    rng = random.Random(seed)
    value = 240
    walk = []
    for _ in range(length):
        value = max(0, min(600, value + rng.randint(-60, 60)))
        walk.append(value)
    return walk


def test_invariants_hold_over_random_profit_walks(notif, settings, healthy) -> None:
    for seed in range(25):
        engine = LifecycleEngine()
        state = None
        previous_status = None
        last_alert_minute: int | None = None

        for minute, profit_bps in enumerate(_random_walk(seed)):
            now = notif.NOW + timedelta(minutes=minute)
            decision = engine.decide(state, notif.evaluation(profit_bps), healthy, settings, now)
            actions = decision.requested_actions

            assert sum(1 for a in actions if a is OutboxActionType.SEND_ALERT) <= 1

            if OutboxActionType.SEND_ALERT in actions:
                assert decision.state.anchor_bps == decision.state.current_profit_bps
                if last_alert_minute is not None:
                    assert (minute - last_alert_minute) * 60 >= settings.alert_cooldown_seconds
                last_alert_minute = minute

            if OutboxActionType.SEND_CLOSED in actions:
                assert previous_status is not LifecycleStatus.CLOSED
                last_alert_minute = None

            if decision.state.status is LifecycleStatus.WATCH:
                assert decision.state.current_profit_bps < settings.minimum_profit_bps

            if decision.state.below_since is not None:
                assert decision.state.current_profit_bps < close_floor_bps(
                    settings.minimum_profit_bps
                )

            previous_status = decision.state.status
            state = decision.state.model_copy(update={"message_id": 555})
