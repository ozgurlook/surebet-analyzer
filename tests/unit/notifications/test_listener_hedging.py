"""Unit tests for Telegram hedging command parsing and listener interaction."""

from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.notifications.listener import (
    TelegramBotListener,
    format_hedging_response,
    parse_hedging_command,
)
from surebet.notifications.models import CardState, DualPolicyEvaluation, LifecycleStatus
from surebet.opportunities.allocation_hedging import (
    calculate_hedging_allocation,
)
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection


def test_parse_hedging_command_single_leg() -> None:
    """Parses standard single fixed leg format 'kurtar ms1 3.10 6000'."""
    cmd = parse_hedging_command("kurtar ms1 3.10 6000")
    assert cmd is not None
    assert cmd.fixed_outcome_raw == "ms1"
    assert cmd.fixed_odds == Decimal("3.10")
    assert cmd.fixed_stake_cents == 600_000
    assert cmd.counter_odds_override == {}


def test_parse_hedging_command_variations() -> None:
    """Parses different syntax variations: /kurtar, hedge, amount first, 2-way outcomes."""
    cmd1 = parse_hedging_command("/kurtar 1 3.10 6.000 TL")
    assert cmd1 is not None
    assert cmd1.fixed_outcome_raw == "1"
    assert cmd1.fixed_odds == Decimal("3.10")
    assert cmd1.fixed_stake_cents == 600_000

    cmd2 = parse_hedging_command("hedge 6000 ms1 3.10")
    assert cmd2 is not None
    assert cmd2.fixed_outcome_raw == "ms1"
    assert cmd2.fixed_odds == Decimal("3.10")
    assert cmd2.fixed_stake_cents == 600_000

    cmd3 = parse_hedging_command("kurtar üst 1.95 5000")
    assert cmd3 is not None
    assert cmd3.fixed_outcome_raw == "üst"
    assert cmd3.fixed_odds == Decimal("1.95")
    assert cmd3.fixed_stake_cents == 500_000


def test_parse_hedging_command_with_counter_odds_override() -> None:
    """Parses full command with updated counter odds: 'kurtar ms1 3.10 6000 x 3.30 ms2 2.40'."""
    cmd = parse_hedging_command("kurtar ms1 3.10 6000 x 3.30 ms2 2.40")
    assert cmd is not None
    assert cmd.fixed_outcome_raw == "ms1"
    assert cmd.fixed_odds == Decimal("3.10")
    assert cmd.fixed_stake_cents == 600_000
    assert len(cmd.counter_odds_override) == 2
    assert cmd.counter_odds_override["x"] == Decimal("3.30")
    assert cmd.counter_odds_override["ms2"] == Decimal("2.40")


def test_parse_hedging_command_invalid() -> None:
    """Returns None for invalid text or non-hedging commands."""
    assert parse_hedging_command("") is None
    assert parse_hedging_command("merhaba") is None
    assert parse_hedging_command("3500") is None
    assert parse_hedging_command("kurtar") is None
    assert parse_hedging_command("kurtar ms1") is None
    assert parse_hedging_command("kurtar ms1 abc 5000") is None


def _dummy_opportunity_3way() -> CalculatedOpportunity:
    selections = {
        Outcome.HOME: StrategySelection(
            outcome=Outcome.HOME,
            source=Source.SOURCE_B,
            odds=Decimal("3.20"),
            stake_cents=600_000,
            cashback_eligible=True,
        ),
        Outcome.DRAW: StrategySelection(
            outcome=Outcome.DRAW,
            source=Source.SOURCE_A,
            odds=Decimal("3.30"),
            stake_cents=581_818,
            cashback_eligible=False,
        ),
        Outcome.AWAY: StrategySelection(
            outcome=Outcome.AWAY,
            source=Source.SOURCE_C,
            odds=Decimal("2.40"),
            stake_cents=800_000,
            cashback_eligible=True,
        ),
    }
    return CalculatedOpportunity(
        opportunity_id="evt-1",
        matched_id="evt-1",
        market_type=MarketType.FULL_TIME_RESULT,
        home="Arsenal",
        away="Chelsea",
        competition="Premier League",
        country_id="country:GB",
        country_name="İngiltere",
        country_iso_code="GB",
        league_id="league:1",
        league_name="Premier League",
        kickoff=datetime.now(UTC),
        selections=MappingProxyType(selections),
        scenarios=MappingProxyType({}),
        calculated_at=datetime.now(UTC),
        budget_cents=1_981_818,
        include_source_a_match_sides=False,
        min_standard_profit_cents=-61_818,
        min_standard_profit_percent=-3.12,
        min_adjusted_profit_cents=10_000,
        min_adjusted_profit_percent=0.50,
        is_positive=True,
        oldest_quote_age_seconds=5,
        market_label="1X2",
    )


def _dummy_card_state(opp: CalculatedOpportunity) -> CardState:
    now = datetime.now(UTC)
    eval_obj = DualPolicyEvaluation(
        matched_id=opp.matched_id,
        market_type=opp.market_type,
        selected=opp,
        draw_only=opp,
        uses_source_a_match_sides=False,
        relevant_sources=frozenset({Source.SOURCE_B, Source.SOURCE_A, Source.SOURCE_C}),
    )
    return CardState(
        user_id=1,
        chat_id=12345,
        card_key="evt-1:1x2",
        matched_id=opp.matched_id,
        market_type=opp.market_type,
        generation=1,
        status=LifecycleStatus.ACTIVE,
        message_id=100,
        evaluation=eval_obj,
        first_profit_bps=100,
        current_profit_bps=100,
        peak_profit_bps=100,
        anchor_bps=100,
        last_alert_at=None,
        below_since=None,
        closed_at=None,
        revival_count=0,
        first_seen_at=now,
        last_seen_at=now,
        last_edit_at=None,
        rendered_fingerprint="fp1",
    )


def test_format_hedging_response_renders_html_and_buttons() -> None:
    """Renders HTML response with profit or minimized loss and counter buttons."""
    opp = _dummy_opportunity_3way()
    result = calculate_hedging_allocation(
        fixed_outcome=Outcome.HOME,
        fixed_odds=Decimal("3.10"),
        fixed_stake_cents=600_000,
        fixed_source=Source.SOURCE_B,
        counter_odds={Outcome.DRAW: Decimal("3.30"), Outcome.AWAY: Decimal("2.40")},
        counter_sources={Outcome.DRAW: Source.SOURCE_A, Outcome.AWAY: Source.SOURCE_C},
        apply_cashback=True,
    )

    text, reply_markup = format_hedging_response(opp, result)
    assert "🛡️ <b>Bahis Kurtarma & Dengeleme (Hedge)</b>" in text
    assert "Arsenal - Chelsea" in text
    assert "MS1" in text
    assert "6.000,00 TL" in text
    assert "Risk Analizi" in text
    assert "çıplak risk" in text.lower()

    # Counter legs are named in the text; the keyboard only offers recalculation
    assert "Kaynak A" in text
    assert "Kaynak C" in text
    flat_buttons = [btn for row in reply_markup.get("inline_keyboard", []) for btn in row]
    assert [b["callback_data"].split(":")[0] for b in flat_buttons] == ["hedge_prompt", "calc"]
    assert all("url" not in b for b in flat_buttons)


@pytest.mark.asyncio
async def test_listener_handles_hedging_reply_message() -> None:
    """TelegramBotListener handles 'kurtar ms1 3.10 6000' replying to a card."""
    opp = _dummy_opportunity_3way()
    card_state = _dummy_card_state(opp)

    mock_client = MagicMock()
    mock_client.send_text = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_card_by_message_id.return_value = card_state

    listener = TelegramBotListener(
        client=mock_client,
        repo=mock_repo,
    )

    msg = {
        "message_id": 999,
        "chat": {"id": 12345},
        "text": "kurtar ms1 3.10 6000",
        "reply_to_message": {"message_id": 100},
    }

    await listener._handle_message(msg)

    assert mock_client.send_text.called
    kwargs = mock_client.send_text.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["reply_to_message_id"] == 999
    assert "🛡️ <b>Bahis Kurtarma & Dengeleme (Hedge)</b>" in kwargs["text"]
    assert "Arsenal - Chelsea" in kwargs["text"]
    assert "6.000,00 TL" in kwargs["text"]
    reply_markup = kwargs.get("reply_markup")
    assert reply_markup is not None
    assert "inline_keyboard" in reply_markup


@pytest.mark.asyncio
async def test_listener_handles_hedge_prompt_callback() -> None:
    """TelegramBotListener handles 'hedge_prompt:<card_key>' callback query."""
    opp = _dummy_opportunity_3way()
    card_state = _dummy_card_state(opp)

    mock_client = MagicMock()
    mock_client.send_text = AsyncMock()
    mock_client.answer_callback_query = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_card_by_key.return_value = card_state

    listener = TelegramBotListener(client=mock_client, repo=mock_repo)

    cb = {
        "id": "cb-123",
        "data": "hedge_prompt:evt-1:1x2",
        "message": {"message_id": 888, "chat": {"id": 12345}},
    }

    await listener._handle_callback_query(cb)

    assert mock_client.answer_callback_query.called
    assert mock_client.send_text.called
    kwargs = mock_client.send_text.call_args.kwargs
    assert "kurtar ms1 3.10 6000" in kwargs["text"]
    keyboard = kwargs["reply_markup"]["inline_keyboard"]
    leg_texts = [btn["text"] for row in keyboard for btn in row]
    assert any("MS1" in t for t in leg_texts)
    assert any("X" in t for t in leg_texts)
    assert any("MS2" in t for t in leg_texts)


def test_parse_hedging_command_multiple_fixed_legs() -> None:
    """Parses command with multiple fixed legs: 'kurtar ms1 3.10 4000 x 3.30 3500'."""
    cmd = parse_hedging_command("kurtar ms1 3.10 4000 x 3.30 3500")
    assert cmd is not None
    assert len(cmd.fixed_legs) == 2

    assert cmd.fixed_legs[0].outcome_raw == "ms1"
    assert cmd.fixed_legs[0].odds == Decimal("3.10")
    assert cmd.fixed_legs[0].stake_cents == 400_000

    assert cmd.fixed_legs[1].outcome_raw == "x"
    assert cmd.fixed_legs[1].odds == Decimal("3.30")
    assert cmd.fixed_legs[1].stake_cents == 350_000

    assert cmd.counter_odds_override == {}


def test_parse_hedging_command_multiple_fixed_legs_with_counter_override() -> None:
    """Parses multiple fixed legs plus counter override: 'kurtar ms1 3.10 4000 x 3.30 3500 ms2 2.50'."""
    cmd = parse_hedging_command("kurtar ms1 3.10 4000 x 3.30 3500 ms2 2.50")
    assert cmd is not None
    assert len(cmd.fixed_legs) == 2
    assert cmd.counter_odds_override == {"ms2": Decimal("2.50")}


@pytest.mark.asyncio
async def test_listener_handles_multiple_fixed_legs_reply() -> None:
    """TelegramBotListener handles multi-leg hedging reply."""
    opp = _dummy_opportunity_3way()
    card_state = _dummy_card_state(opp)

    mock_client = MagicMock()
    mock_client.send_text = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_card_by_message_id.return_value = card_state

    listener = TelegramBotListener(
        client=mock_client,
        repo=mock_repo,
    )

    msg = {
        "message_id": 999,
        "chat": {"id": 12345},
        "text": "kurtar ms1 3.10 4000 x 3.30 3500",
        "reply_to_message": {"message_id": 100},
    }

    await listener._handle_message(msg)

    assert mock_client.send_text.called
    kwargs = mock_client.send_text.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["reply_to_message_id"] == 999
    text = kwargs["text"]
    assert "Oynanmış Bacaklar" in text
    assert "MS1" in text and "4.000,00 TL" in text
    assert "X" in text and "3.500,00 TL" in text
    assert "MS2" in text
    assert "-7.500,00 TL" in text  # Naked risk

    reply_markup = kwargs.get("reply_markup")
    assert reply_markup is not None
    flat_buttons = [btn for row in reply_markup.get("inline_keyboard", []) for btn in row]
    # The remaining MS2 leg is named in the text; buttons never link out
    assert "Kaynak C" in text
    assert all("url" not in b for b in flat_buttons)
