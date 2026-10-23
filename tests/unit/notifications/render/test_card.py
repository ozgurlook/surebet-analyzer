from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from surebet.domain.enums import MarketType, Outcome, Source, Sport
from surebet.notifications.cards.engine import LifecycleEngine
from surebet.notifications.models import LifecycleStatus
from surebet.notifications.render.card import CardRenderer
from surebet.storage.snapshots import SourceHealthStatus


@pytest.fixture
def renderer(notif) -> CardRenderer:
    return CardRenderer()


def test_header_always_names_the_market(notif, renderer) -> None:
    state = notif.card_state(240, LifecycleStatus.NEW, message_id=555)
    text = renderer.render(state, notif.NOW).text
    assert text.splitlines()[0] == "🟢 YENİ FIRSAT · %2,40 · 1X2"


def test_kickoff_is_absolute_and_human_readable(notif, renderer) -> None:
    text = renderer.render(notif.card_state(240), notif.NOW).text
    assert "Başlangıç: Bugün 21:00" in text


def test_countdown_appears_only_inside_the_final_hour(notif, renderer) -> None:
    state = notif.card_state(240)
    close_to_kickoff = notif.KICKOFF - timedelta(minutes=35)
    assert "~35 dk kaldı" in renderer.render(state, close_to_kickoff).text
    assert "kaldı" not in renderer.render(state, notif.NOW).text


def test_card_carries_no_outbound_links(notif, renderer) -> None:
    rendered = renderer.render(notif.card_state(240), notif.NOW)
    assert "href=" not in rendered.text
    buttons = [btn for row in rendered.reply_markup["inline_keyboard"] for btn in row]
    assert all("url" not in btn for btn in buttons)


def test_cashback_line_appears_only_when_it_changes_the_profit(notif, renderer) -> None:
    text = renderer.render(notif.card_state(240), notif.NOW).text
    assert "cashbacksiz" in text
    assert "Cashback dahil" not in text


def test_stale_banner_is_the_first_line(notif, renderer) -> None:
    stale = notif.card_state(
        240, LifecycleStatus.STALE, last_seen_at=notif.NOW - timedelta(minutes=4)
    )
    text = renderer.render(stale, notif.NOW).text
    assert text.splitlines()[0] == "⚠️ Veri 4 dk'dır güncellenmedi — oranlar bayat olabilir"


def test_team_names_are_html_escaped(notif, renderer) -> None:
    evaluated = notif.evaluation(240)
    escaped = evaluated.selected.model_copy(update={"home": "Ajax & <b>"})
    state = notif.card_state(240).model_copy(
        update={"evaluation": evaluated.model_copy(update={"selected": escaped})}
    )
    assert "Ajax &amp; &lt;b&gt;" in renderer.render(state, notif.NOW).text


def test_identical_state_renders_an_identical_fingerprint(notif, renderer) -> None:
    first = renderer.render(notif.card_state(240), notif.NOW)
    second = renderer.render(notif.card_state(240), notif.NOW)
    assert first.fingerprint == second.fingerprint
    assert renderer.render(notif.card_state(270), notif.NOW).fingerprint != first.fingerprint


def test_fingerprint_buckets_minor_profit_fluctuations(notif, renderer) -> None:
    # 1180 bps and 1185 bps fall into the same 25 bps bucket -> identical fingerprint
    card_1180 = notif.card_state(1180)
    card_1185 = notif.card_state(1185)
    assert (
        renderer.render(card_1180, notif.NOW).fingerprint
        == renderer.render(card_1185, notif.NOW).fingerprint
    )

    # 1180 bps and 1260 bps cross buckets -> different fingerprint
    card_1260 = notif.card_state(1260)
    assert (
        renderer.render(card_1180, notif.NOW).fingerprint
        != renderer.render(card_1260, notif.NOW).fingerprint
    )


def test_fingerprint_changes_on_status_transition(notif, renderer) -> None:
    card_active = notif.card_state(1180, status=LifecycleStatus.ACTIVE)
    card_watch = notif.card_state(1180, status=LifecycleStatus.WATCH)
    assert (
        renderer.render(card_active, notif.NOW).fingerprint
        != renderer.render(card_watch, notif.NOW).fingerprint
    )


def test_calculation_clock_is_rendered(notif, renderer) -> None:
    text = renderer.render(notif.card_state(240), notif.NOW).text
    assert "15:00 hesaplandı" in text
    assert "hesaplandı" in text


def test_lira_thousands_separator_not_corrupted_by_percentage_formatting(notif, renderer) -> None:
    eval_obj = notif.evaluation(240)
    selected_modified = eval_obj.selected.model_copy(
        update={
            "min_standard_profit_cents": 500_000,
            "min_standard_profit_percent": 2.4,
            "min_adjusted_profit_cents": 600_000,
            "min_adjusted_profit_percent": 3.0,
        }
    )
    draw_only_modified = eval_obj.selected.model_copy(
        update={
            "min_adjusted_profit_cents": 500_000,
            "min_adjusted_profit_percent": 2.5,
        }
    )
    eval_modified = eval_obj.model_copy(
        update={
            "selected": selected_modified,
            "draw_only": draw_only_modified,
            "uses_source_a_match_sides": True,
        }
    )
    state = notif.card_state(240).model_copy(update={"evaluation": eval_modified})
    text = renderer.render(state, notif.NOW).text
    assert "cashbacksiz +5.000,00 TL (%2,40)" in text
    assert "Kaynak A MS1/MS2 açık: +6.000,00 TL (%3,00)" in text
    assert "Kaynak A yalnız X: +5.000,00 TL (%2,50)" in text
    assert "Kaynak A avantajı: +0,50 puan" in text
    assert "5,000 TL" not in text
    assert "6,000 TL" not in text


def test_source_a_usage_reports_none_when_no_source_a_selections(notif, renderer) -> None:
    eval_obj = notif.evaluation(240)
    selections_no_source_a = {
        outcome: sel.model_copy(update={"source": Source.SOURCE_C})
        for outcome, sel in eval_obj.selected.selections.items()
    }
    selected = eval_obj.selected.model_copy(update={"selections": selections_no_source_a})
    state = notif.card_state(240).model_copy(
        update={"evaluation": eval_obj.model_copy(update={"selected": selected})}
    )
    text = renderer.render(state, notif.NOW).text
    assert "Kaynak A kullanımı: Yok" in text


def test_cashback_line_omitted_when_standard_equals_adjusted(notif, renderer) -> None:
    eval_obj = notif.evaluation(240)
    selected = eval_obj.selected.model_copy(
        update={
            "min_standard_profit_cents": 24_000,
            "min_adjusted_profit_cents": 24_000,
        }
    )
    state = notif.card_state(240).model_copy(
        update={"evaluation": eval_obj.model_copy(update={"selected": selected})}
    )
    text = renderer.render(state, notif.NOW).text
    assert "cashbacksiz" not in text


@pytest.mark.parametrize(
    ("status", "expected_prefix"),
    [
        (LifecycleStatus.NEW, "🟢 YENİ FIRSAT"),
        (LifecycleStatus.ACTIVE, "🟢 Aktif"),
        (LifecycleStatus.WATCH, "🟠 Eşiğe yakın"),
        (LifecycleStatus.STALE, "⚪ Veri bekleniyor"),
        (LifecycleStatus.CLOSED, "🔴 Kapandı"),
    ],
)
def test_all_lifecycle_status_headers(notif, renderer, status, expected_prefix) -> None:
    state = notif.card_state(240, status=status)
    text = renderer.render(state, notif.NOW).text
    # If STALE, stale banner is line 0, header is line 1; otherwise header is line 0
    header_line = text.splitlines()[1] if status is LifecycleStatus.STALE else text.splitlines()[0]
    assert header_line.startswith(expected_prefix)


@pytest.mark.parametrize(
    ("market_type", "expected_label"),
    [
        (MarketType.FULL_TIME_RESULT, "1X2"),
        (MarketType.TOTAL_GOALS_25, "2.5 Alt/Üst"),
    ],
)
def test_market_type_labels(notif, renderer, market_type, expected_label) -> None:
    state = notif.card_state(240, market_type=market_type)
    text = renderer.render(state, notif.NOW).text
    header = text.splitlines()[0]
    assert header.endswith(f"· {expected_label}")


def _with_selection(notif, outcome, **updates):
    """Rebuild a card state with one selection field overridden."""
    eval_obj = notif.evaluation(240)
    selections = dict(eval_obj.selected.selections)
    selections[outcome] = selections[outcome].model_copy(update=updates)
    selected = eval_obj.selected.model_copy(update={"selections": MappingProxyType(selections)})
    return notif.card_state(240).model_copy(
        update={"evaluation": eval_obj.model_copy(update={"selected": selected})}
    )


def test_odds_are_normalized_to_two_decimals(notif, renderer) -> None:
    """M4: `1.850` reads as one thousand eight hundred fifty in a Turkish message."""
    state = _with_selection(notif, Outcome.HOME, odds=Decimal("1.850"))
    text = renderer.render(state, notif.NOW).text
    assert "@1.85 " in text
    assert "1.850" not in text


def test_a_quote_bearing_event_id_never_reaches_the_card_text(notif, renderer) -> None:
    """I6: `event_id` comes verbatim from an untrusted sportsbook feed."""
    state = _with_selection(notif, Outcome.DRAW, event_id='1" onmouseover="alert(1)')
    text = renderer.render(state, notif.NOW).text
    assert "onmouseover" not in text


def test_amounts_always_carry_two_decimals(notif, renderer) -> None:
    """M2: spec section 4 shows `1.000,00 TL`, and it keeps the column aligned."""
    text = renderer.render(notif.card_state(240), notif.NOW).text
    assert "Bütçe: 10.000,00 TL" in text
    assert "10.000 TL" not in text


def test_stale_banner_ages_with_the_data_not_with_the_cycle(
    notif, renderer, settings, healthy
) -> None:
    """M1: a banner built by the engine must not read "1 dk" forever.

    The golden banner test above hand-builds `last_seen_at`; this one drives the
    real engine through an outage in which the calculation still yields a
    candidate every cycle -- the case that used to freeze the banner at one
    minute.
    """
    engine = LifecycleEngine()
    unhealthy = dict(healthy)
    unhealthy[Source.SOURCE_C] = SourceHealthStatus.DEGRADED

    state = engine.decide(None, notif.evaluation(240), healthy, settings, notif.NOW).state
    state = state.model_copy(update={"message_id": 555})
    for minute in range(1, 5):
        now = notif.NOW + timedelta(minutes=minute)
        state = engine.decide(state, notif.evaluation(240), unhealthy, settings, now).state

    text = renderer.render(state, notif.NOW + timedelta(minutes=4)).text
    assert text.splitlines()[0] == "⚠️ Veri 4 dk'dır güncellenmedi — oranlar bayat olabilir"


def test_card_renders_reply_markup_with_calculator(notif, renderer) -> None:
    state = notif.card_state(240)
    rendered = renderer.render(state, notif.NOW)
    assert rendered.reply_markup is not None
    keyboard = rendered.reply_markup.get("inline_keyboard")
    assert isinstance(keyboard, list)
    assert len(keyboard) == 2  # calc/hedge/refresh row + mute/fav/help row

    # Row 1 (calc + hedge + refresh)
    row_calc = keyboard[-2]
    assert len(row_calc) == 3
    assert row_calc[0]["text"] == "🧮 Dağılım Hesapla"
    assert row_calc[0]["callback_data"] == f"calc:{state.card_key}"
    assert row_calc[1]["text"] == "🛡️ Kurtar"
    assert row_calc[1]["callback_data"] == f"hedge_prompt:{state.card_key}"
    assert row_calc[2]["text"] == "🔄 Güncel Oranlar"
    assert row_calc[2]["callback_data"] == f"refresh:{state.card_key}"

    # Last row is mute, favorite, and help
    last_row = keyboard[-1]
    assert len(last_row) == 3
    assert last_row[0]["text"] == "🔕 2 Saat Sustur"
    assert last_row[0]["callback_data"] == f"mute:{state.card_key}"
    assert last_row[1]["text"] == "📌 Takibe Al"
    assert last_row[1]["callback_data"] == f"fav:{state.card_key}"
    assert last_row[2]["text"] == "💡 Komutlar"
    assert last_row[2]["callback_data"] == f"help:{state.card_key}"


def test_card_renders_muted_and_favorited_buttons(notif, renderer) -> None:
    state = notif.card_state(240)
    rendered = renderer.render(state, notif.NOW, is_muted=True, is_fav=True)
    keyboard = rendered.reply_markup["inline_keyboard"]
    last_row = keyboard[-1]
    assert last_row[0]["text"] == "🔔 Susturmayı Kaldır"
    assert last_row[0]["callback_data"] == f"unmute:{state.card_key}"
    assert last_row[1]["text"] == "⭐ Takipte"
    assert last_row[1]["callback_data"] == f"fav:{state.card_key}"


def test_card_renders_euroleague_basketball_header_and_handicap_lines(notif, renderer) -> None:
    sel_home = notif.selection(Outcome.HOME, Source.SOURCE_A, "1.95")
    sel_away = notif.selection(Outcome.AWAY, Source.SOURCE_B, "1.88")
    opp = notif.opportunity(352, market_type=MarketType.BASKETBALL_ASIAN_HANDICAP).model_copy(
        update={
            "sport": Sport.BASKETBALL,
            "competition": "Euroleague",
            "league_name": "Euroleague",
            "market_label": "Asya Handikap (-4.5 / +4.5)",
            "home": "Fenerbahçe Beko",
            "away": "Panathinaikos",
            "selections": MappingProxyType({Outcome.HOME: sel_home, Outcome.AWAY: sel_away}),
        }
    )
    eval_obj = notif.evaluation(352, market_type=MarketType.BASKETBALL_ASIAN_HANDICAP).model_copy(
        update={"selected": opp}
    )
    state = notif.card_state(352, market_type=MarketType.BASKETBALL_ASIAN_HANDICAP).model_copy(
        update={"evaluation": eval_obj}
    )
    rendered = renderer.render(state, notif.NOW)
    text = rendered.text

    assert "🏀 [EUROLEAGUE SUREBET]" in text
    assert "Asya Handikap (-4.5 / +4.5)" in text
    assert "1 (Ev)" in text
    assert "2 (Dep)" in text
    assert "⚡ %10 KB" in text
