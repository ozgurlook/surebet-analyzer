from datetime import timedelta

import pytest

from surebet.domain.enums import Source
from surebet.notifications.cards.engine import LifecycleEngine
from surebet.notifications.models import LifecycleStatus, OutboxActionType
from surebet.storage.snapshots import SourceHealthStatus

# (dakika, kâr bps, beklenen aksiyonlar) -- spec 5.4'teki referans çizgi.
REFERENCE_TIMELINE = [
    (0, 240, (OutboxActionType.SEND_CARD,)),
    (1, 231, (OutboxActionType.EDIT_CARD,)),
    (2, 215, (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_ALERT)),
    (3, 190, (OutboxActionType.EDIT_CARD,)),
    (5, 255, (OutboxActionType.EDIT_CARD,)),
    (7, 270, (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_ALERT)),
    (20, 91, (OutboxActionType.EDIT_CARD,)),
    (22, 74, (OutboxActionType.EDIT_CARD,)),
    (24, 88, (OutboxActionType.EDIT_CARD,)),
    (26, 60, (OutboxActionType.EDIT_CARD,)),
    (29, 55, (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_CLOSED)),
    (38, 140, (OutboxActionType.EDIT_CARD, OutboxActionType.SEND_REVIVED)),
]


def _run(notif, timeline, settings, healthy):
    engine = LifecycleEngine()
    state = None
    observed = []
    for minute, profit_bps, _ in timeline:
        now = notif.NOW + timedelta(minutes=minute)
        decision = engine.decide(state, notif.evaluation(profit_bps), healthy, settings, now)
        state = decision.state.model_copy(update={"message_id": 555, "last_edit_at": now})
        observed.append(decision.requested_actions)
    return state, observed


def test_reference_timeline_produces_the_designed_actions(notif, settings, healthy) -> None:
    _, observed = _run(notif, REFERENCE_TIMELINE, settings, healthy)
    assert observed == [expected for _, _, expected in REFERENCE_TIMELINE]


def test_reference_timeline_never_bumps_the_generation(notif, settings, healthy) -> None:
    state, _ = _run(notif, REFERENCE_TIMELINE, settings, healthy)
    assert state.generation == 1
    assert state.revival_count == 1
    assert state.status is LifecycleStatus.ACTIVE


def test_no_cycle_ever_emits_two_alerts(notif, settings, healthy) -> None:
    _, observed = _run(notif, REFERENCE_TIMELINE, settings, healthy)
    for actions in observed:
        assert sum(1 for action in actions if action is OutboxActionType.SEND_ALERT) <= 1


def test_dip_above_the_close_floor_never_closes_the_card(notif, settings, healthy) -> None:
    timeline = [(0, 240, ()), (5, 85, ()), (10, 85, ()), (20, 85, ()), (30, 85, ())]
    _, observed = _run(notif, timeline, settings, healthy)
    assert all(OutboxActionType.SEND_CLOSED not in actions for actions in observed)


def test_a_break_above_the_close_floor_resets_the_grace_counter(notif, settings, healthy) -> None:
    timeline = [(0, 240, ()), (1, 60, ()), (3, 85, ()), (5, 60, ()), (7, 60, ())]
    _, observed = _run(notif, timeline, settings, healthy)
    assert all(OutboxActionType.SEND_CLOSED not in actions for actions in observed)


def test_revival_after_the_window_opens_a_new_generation(notif, settings, healthy) -> None:
    engine = LifecycleEngine()
    state = None
    for minute, profit_bps in [(0, 240), (1, 50), (5, 50), (21, 140)]:
        decision = engine.decide(
            state,
            notif.evaluation(profit_bps),
            healthy,
            settings,
            notif.NOW + timedelta(minutes=minute),
        )
        state = decision.state.model_copy(update={"message_id": 555})
    assert state.generation == 2
    assert state.status is LifecycleStatus.NEW
    assert state.revival_count == 0


def test_stale_sources_freeze_the_anchor_and_never_close(notif, settings, healthy) -> None:
    engine = LifecycleEngine()
    unhealthy = dict(healthy)
    unhealthy[Source.SOURCE_C] = SourceHealthStatus.DEGRADED

    decision = engine.decide(None, notif.evaluation(240), healthy, settings, notif.NOW)
    state = decision.state.model_copy(update={"message_id": 555})

    for minute in range(1, 12):
        decision = engine.decide(
            state,
            notif.evaluation(10),
            unhealthy,
            settings,
            notif.NOW + timedelta(minutes=minute),
        )
        state = decision.state.model_copy(update={"message_id": 555})
        assert OutboxActionType.SEND_ALERT not in decision.requested_actions
        assert OutboxActionType.SEND_CLOSED not in decision.requested_actions

    assert state.status is LifecycleStatus.STALE
    assert state.anchor_bps == 240
    assert state.below_since is None


def test_alerts_are_withheld_until_the_card_has_been_delivered(notif, settings, healthy) -> None:
    engine = LifecycleEngine()
    decision = engine.decide(None, notif.evaluation(240), healthy, settings, notif.NOW)
    undelivered = decision.state  # message_id is still None
    follow_up = engine.decide(
        undelivered, notif.evaluation(180), healthy, settings, notif.NOW + timedelta(minutes=1)
    )
    assert OutboxActionType.SEND_ALERT not in follow_up.requested_actions
    assert follow_up.state.anchor_bps == 240


def test_a_kickoff_that_has_passed_closes_the_card(notif, settings, healthy) -> None:
    engine = LifecycleEngine()
    decision = engine.decide(None, notif.evaluation(240), healthy, settings, notif.NOW)
    state = decision.state.model_copy(update={"message_id": 555})
    closed = engine.decide(state, None, healthy, settings, notif.NOW + timedelta(hours=7))
    assert closed.state.status is LifecycleStatus.CLOSED
    assert OutboxActionType.SEND_CLOSED in closed.requested_actions


@pytest.mark.parametrize("profit_bps", [240, 100, 99, 80, 79, 0])
def test_state_is_never_closed_twice(profit_bps: int, notif, settings, healthy) -> None:
    engine = LifecycleEngine()
    decision = engine.decide(None, notif.evaluation(240), healthy, settings, notif.NOW)
    state = decision.state.model_copy(update={"message_id": 555})
    for minute in range(1, 30):
        decision = engine.decide(
            state,
            notif.evaluation(profit_bps),
            healthy,
            settings,
            notif.NOW + timedelta(minutes=minute),
        )
        closed_twice = (
            state.status is LifecycleStatus.CLOSED
            and OutboxActionType.SEND_CLOSED in decision.requested_actions
        )
        assert not closed_twice
        state = decision.state.model_copy(update={"message_id": 555})


def test_an_outage_freezes_the_close_grace_counter(notif, settings, healthy) -> None:
    """I4: unobserved minutes must not count toward CLOSE_GRACE_SECONDS.

    Spec 5.3 freezes `below_since` during STALE. A card that dipped below the
    floor one minute before the outage must not close on the single healthy
    observation that follows a 19-minute blackout.
    """
    engine = LifecycleEngine()
    unhealthy = dict(healthy)
    unhealthy[Source.SOURCE_C] = SourceHealthStatus.DEGRADED

    def step(state, profit_bps, minute, health):
        decision = engine.decide(
            state,
            notif.evaluation(profit_bps),
            health,
            settings,
            notif.NOW + timedelta(minutes=minute),
        )
        return decision, decision.state.model_copy(update={"message_id": 555})

    _, state = step(None, 240, 0, healthy)
    _, state = step(state, 60, 1, healthy)  # below the close floor
    assert state.below_since == notif.NOW + timedelta(minutes=1)

    _, state = step(state, 60, 2, unhealthy)  # source goes down
    assert state.status is LifecycleStatus.STALE
    assert state.below_since is None

    # 19 unobserved minutes later the source recovers, still below the floor.
    decision, state = step(state, 60, 20, healthy)
    assert OutboxActionType.SEND_CLOSED not in decision.requested_actions
    assert state.status is LifecycleStatus.WATCH
    assert state.below_since == notif.NOW + timedelta(minutes=20)

    # Only a full grace window of HEALTHY observations closes the card.
    decision, state = step(state, 60, 23, healthy)
    assert OutboxActionType.SEND_CLOSED in decision.requested_actions
