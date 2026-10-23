import json

import httpx
import pytest
import respx

from surebet.notifications.models import TelegramFailureKind
from surebet.notifications.telegram import TelegramClient


@pytest.fixture
def captured_client() -> tuple[TelegramClient, list[dict]]:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 999}})

    transport = httpx.MockTransport(handler)
    client = TelegramClient("secret", http_client=httpx.AsyncClient(transport=transport))
    return client, captured


def _client_returning(status_code: int, body: dict) -> TelegramClient:
    transport = httpx.MockTransport(lambda _req: httpx.Response(status_code, json=body))
    return TelegramClient("secret", http_client=httpx.AsyncClient(transport=transport))


@respx.mock
@pytest.mark.asyncio
async def test_unrecognized_client_error_is_permanent_and_bounded() -> None:
    respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "some unrecognized error"}
        )
    )
    result = await TelegramClient("secret").send_text(chat_id=123, text="hello")
    assert result.failure_kind is TelegramFailureKind.PERMANENT
    assert result.safe_error == "Telegram HTTP 400"


@respx.mock
@pytest.mark.asyncio
async def test_send_text_returns_message_id_without_exposing_token() -> None:
    route = respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )
    result = await TelegramClient("secret").send_text(chat_id=123, text="hello")
    assert route.called
    assert result.ok is True
    assert result.message_id == 42


@respx.mock
@pytest.mark.asyncio
async def test_rate_limit_returns_retry_after_and_sanitized_error() -> None:
    respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 17},
            },
        )
    )
    result = await TelegramClient("secret").send_text(chat_id=123, text="hello")
    assert result.ok is False
    assert result.failure_kind is TelegramFailureKind.RATE_LIMITED
    assert result.retry_after_seconds == 17
    assert result.safe_error is not None
    assert "secret" not in result.safe_error


@respx.mock
@pytest.mark.asyncio
async def test_edit_message_not_found_is_classified_distinctly() -> None:
    respx.post("https://api.telegram.org/botsecret/editMessageText").mock(
        return_value=httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: message to edit not found"},
        )
    )
    result = await TelegramClient("secret").edit_text(chat_id=123, message_id=1, text="hi")
    assert result.failure_kind is TelegramFailureKind.MESSAGE_NOT_FOUND
    assert result.safe_error is not None
    assert "secret" not in result.safe_error
    assert "message to edit not found" not in result.safe_error


@respx.mock
@pytest.mark.asyncio
async def test_server_error_is_transient_and_bounded() -> None:
    respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(500, json={"ok": False, "description": "Internal error detail"})
    )
    result = await TelegramClient("secret").send_text(chat_id=123, text="hello")
    assert result.failure_kind is TelegramFailureKind.TRANSIENT
    assert result.safe_error == "Telegram HTTP 500"


@respx.mock
@pytest.mark.asyncio
async def test_timeout_is_transient() -> None:
    respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    result = await TelegramClient("secret").send_text(chat_id=123, text="hello")
    assert result.ok is False
    assert result.failure_kind is TelegramFailureKind.TRANSIENT


@respx.mock
@pytest.mark.asyncio
async def test_test_chat_sends_the_provided_text() -> None:
    route = respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    )
    result = await TelegramClient("secret").test_chat(chat_id=123, text="✅ test")
    assert route.called
    assert result.ok is True


@respx.mock
@pytest.mark.asyncio
async def test_telegram_client_reuses_persistent_http_client_and_closes() -> None:
    respx.post("https://api.telegram.org/botsecret/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )
    client = TelegramClient("secret")
    await client.send_text(chat_id=123, text="one")
    http_c1 = client._http_client
    assert http_c1 is not None
    assert not http_c1.is_closed

    # Second call reuses identical client
    await client.send_text(chat_id=123, text="two")
    assert client._http_client is http_c1

    # aclose properly shuts it down
    await client.aclose()
    assert client._http_client is None
    assert http_c1.is_closed


async def test_send_text_threads_the_reply_and_silence_flags(captured_client) -> None:
    client, captured = captured_client
    await client.send_text(chat_id=1, text="x", reply_to_message_id=42, silent=True)
    assert captured[-1]["reply_to_message_id"] == 42
    assert captured[-1]["disable_notification"] is True


async def test_send_text_omits_reply_fields_when_unset(captured_client) -> None:
    client, captured = captured_client
    await client.send_text(chat_id=1, text="x")
    assert "reply_to_message_id" not in captured[-1]
    assert captured[-1]["disable_notification"] is False


@pytest.mark.parametrize(
    "description",
    ["Forbidden: bot was blocked by the user", "Bad Request: chat not found"],
)
async def test_unreachable_chats_are_classified_separately(description: str) -> None:
    client = _client_returning(403, {"ok": False, "description": description})
    result = await client.send_text(chat_id=1, text="x")
    assert result.failure_kind is TelegramFailureKind.CHAT_UNREACHABLE


async def test_message_is_not_modified_is_treated_as_success() -> None:
    client = _client_returning(
        400, {"ok": False, "description": "Bad Request: message is not modified"}
    )
    result = await client.edit_text(chat_id=1, message_id=2, text="x")
    assert result.ok is True


async def test_send_text_and_edit_text_threads_reply_markup(captured_client) -> None:
    client, captured = captured_client
    markup = {"inline_keyboard": [[{"text": "Docs", "url": "https://example.com"}]]}
    await client.send_text(chat_id=1, text="x", reply_markup=markup)
    assert captured[-1]["reply_markup"] == markup

    await client.edit_text(chat_id=1, message_id=42, text="y", reply_markup=markup)
    assert captured[-1]["reply_markup"] == markup
    assert captured[-1]["message_id"] == 42


@respx.mock
@pytest.mark.asyncio
async def test_answer_callback_query() -> None:
    route = respx.post("https://api.telegram.org/botsecret/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    result = await TelegramClient("secret").answer_callback_query("cb_123", text="Hesaplandı")
    assert route.called
    assert result.ok is True


@respx.mock
@pytest.mark.asyncio
async def test_get_updates() -> None:
    updates_data = [{"update_id": 1, "message": {"text": "3500"}}]
    route = respx.post("https://api.telegram.org/botsecret/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": updates_data})
    )
    updates = await TelegramClient("secret").get_updates(offset=10)
    assert route.called
    assert len(updates) == 1
    assert updates[0]["message"]["text"] == "3500"
