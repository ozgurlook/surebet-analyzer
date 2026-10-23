from datetime import timedelta

from surebet.notifications.models import LifecycleStatus
from surebet.notifications.render.alerts import AlertRenderer
from surebet.notifications.render.format import content_fingerprint


def test_decrease_alert_shows_both_levels_and_the_signed_gap(notif) -> None:
    state = notif.card_state(215, peak_profit_bps=240)
    text = AlertRenderer().render_change(state, previous_anchor_bps=240).text
    assert text == "🟡 %2,40 → %2,15   (−0,25 puan)"


def test_increase_alert_uses_the_rising_icon(notif) -> None:
    text = AlertRenderer().render_change(notif.card_state(270), previous_anchor_bps=215).text
    assert text == "📈 %2,15 → %2,70   (+0,55 puan)"


def test_revived_alert_names_the_current_level(notif) -> None:
    state = notif.card_state(140, revival_count=1)
    assert AlertRenderer().render_revived(state).text == "♻️ Yeniden açıldı · %1,40"


def test_closed_alert_reports_duration_peak_and_last_level(notif) -> None:
    state = notif.card_state(
        55,
        LifecycleStatus.CLOSED,
        peak_profit_bps=270,
        last_seen_at=notif.NOW + timedelta(minutes=29),
        closed_at=notif.NOW + timedelta(minutes=29),
    )
    text = AlertRenderer().render_closed(state).text
    assert text == "🔴 Kapandı · 29 dk aktif · zirve %2,70 · son %0,55"


def test_closed_alert_uses_the_closing_time_not_the_last_evaluation(notif) -> None:
    state = notif.card_state(
        55,
        LifecycleStatus.CLOSED,
        peak_profit_bps=270,
        last_seen_at=notif.NOW + timedelta(minutes=5),
        closed_at=notif.NOW + timedelta(minutes=29),
    )
    assert "29 dk aktif" in AlertRenderer().render_closed(state).text


def test_closed_alert_falls_back_to_last_seen_at_when_closed_at_is_none(notif) -> None:
    state = notif.card_state(
        60,
        LifecycleStatus.CLOSED,
        peak_profit_bps=220,
        last_seen_at=notif.NOW + timedelta(minutes=14),
        closed_at=None,
    )
    text = AlertRenderer().render_closed(state).text
    assert text == "🔴 Kapandı · 14 dk aktif · zirve %2,20 · son %0,60"


def test_alert_renderer_produces_consistent_fingerprint(notif) -> None:
    state = notif.card_state(140)
    rendered = AlertRenderer().render_revived(state)
    assert rendered.fingerprint == content_fingerprint(rendered.text)
    assert len(rendered.fingerprint) == 64
