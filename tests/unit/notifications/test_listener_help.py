"""Unit tests for interactive Telegram help menu and topic-based guides."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from surebet.notifications.listener import (
    HELP_TOPICS,
    TelegramBotListener,
    render_help_menu,
    render_help_topic,
    resolve_help_topic_id,
)
from surebet.notifications.telegram import TelegramResult


def test_help_topics_defined() -> None:
    expected_topics = {"stake", "hedge", "fav", "tips"}
    assert set(HELP_TOPICS.keys()) == expected_topics
    for topic in HELP_TOPICS.values():
        assert topic.title
        assert topic.summary
        assert len(topic.examples) > 0
        assert topic.syntax


def test_render_help_menu() -> None:
    text, markup = render_help_menu()
    assert "Surebet Arbitraj Asistanı" in text
    assert "Yardım & Kullanım Rehberi" in text
    assert "inline_keyboard" in markup

    buttons = [b for row in markup["inline_keyboard"] for b in row]
    callbacks = [b["callback_data"] for b in buttons]
    assert "help_topic:stake" in callbacks
    assert "help_topic:hedge" in callbacks
    assert "help_topic:domain" not in callbacks
    assert "help_topic:fav" in callbacks
    assert "help_topic:tips" in callbacks


def test_render_help_topic_each_has_back_button() -> None:
    for topic_id in HELP_TOPICS:
        text, markup = render_help_topic(topic_id)
        assert text
        assert "inline_keyboard" in markup
        callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert "help_menu" in callbacks


def test_render_help_topic_hedge_content() -> None:
    text, _markup = render_help_topic("hedge")
    assert "Kurtarma & Oran Düşüşü" in text
    assert "kurtar ms1" in text
    assert "kurtar 1" in text
    assert "Çoklu Bacak" in text or "ortak" in text.lower() or "bacak" in text.lower()


def test_resolve_help_topic_id() -> None:
    assert resolve_help_topic_id("kurtar") == "hedge"
    assert resolve_help_topic_id("hedge") == "hedge"
    assert resolve_help_topic_id("hesapla") == "stake"
    assert resolve_help_topic_id("butce") == "stake"
    assert resolve_help_topic_id("bütçe") == "stake"
    assert resolve_help_topic_id("domain") is None
    assert resolve_help_topic_id("fav") == "fav"
    assert resolve_help_topic_id("favoriler") == "fav"
    assert resolve_help_topic_id("ipucu") == "tips"
    assert resolve_help_topic_id("tips") == "tips"
    assert resolve_help_topic_id("bilinmeyen") is None


@pytest.mark.asyncio
async def test_listener_handles_help_message_main_menu() -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_message(
        {
            "chat": {"id": 123},
            "text": "/help",
            "message_id": 10,
        }
    )

    client.send_text.assert_awaited_once()
    call_kwargs = client.send_text.await_args.kwargs
    assert call_kwargs["chat_id"] == 123
    assert "Yardım & Kullanım Rehberi" in call_kwargs["text"]
    assert "inline_keyboard" in call_kwargs["reply_markup"]


@pytest.mark.asyncio
async def test_listener_handles_help_message_with_topic_arg() -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_message(
        {
            "chat": {"id": 123},
            "text": "/yardim kurtar",
            "message_id": 10,
        }
    )

    client.send_text.assert_awaited_once()
    call_kwargs = client.send_text.await_args.kwargs
    assert "Kurtarma & Oran Düşüşü" in call_kwargs["text"]
    assert "kurtar ms1" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_callback_help_topic() -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.edit_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb_help_1",
            "data": "help_topic:hedge",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )

    client.answer_callback_query.assert_awaited_once_with("cb_help_1")
    client.edit_text.assert_awaited_once()
    call_kwargs = client.edit_text.await_args.kwargs
    assert call_kwargs["chat_id"] == 123
    assert call_kwargs["message_id"] == 555
    assert "Kurtarma & Oran Düşüşü" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_callback_help_menu() -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.edit_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb_help_2",
            "data": "help_menu",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )

    client.answer_callback_query.assert_awaited_once_with("cb_help_2")
    client.edit_text.assert_awaited_once()
    call_kwargs = client.edit_text.await_args.kwargs
    assert "Yardım & Kullanım Rehberi" in call_kwargs["text"]
    assert "inline_keyboard" in call_kwargs["reply_markup"]
