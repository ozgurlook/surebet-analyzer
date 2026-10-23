"""Minimal async Telegram Bot API client with sanitized, typed failures."""

from typing import Any

import httpx

from .models import TelegramFailureKind, TelegramResult

_BASE_URL = "https://api.telegram.org"
_TIMEOUT_SECONDS = 5.0
_MESSAGE_NOT_FOUND_MARKERS = ("message to edit not found", "message to delete not found")
_UNREACHABLE_MARKERS = (
    "bot was blocked by the user",
    "chat not found",
    "user is deactivated",
    "bot was kicked",
)
_NOT_MODIFIED_MARKER = "message is not modified"


class TelegramClient:
    """Sends and edits plain-text HTML messages. Never logs the token or a URL containing it."""

    def __init__(self, token: str, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
            self._owns_client = True
        return self._http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created and owned internally."""
        if self._owns_client and self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        silent: bool = False,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramResult:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
            # The card may have been deleted by the user; the alert is still worth sending.
            payload["allow_sending_without_reply"] = True
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramResult:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("editMessageText", payload)

    async def edit_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramResult:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("editMessageReplyMarkup", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> TelegramResult:
        payload: dict[str, object] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        return await self._call("answerCallbackQuery", payload)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        url = f"{_BASE_URL}/bot{self._token}/getUpdates"
        try:
            client = await self._get_client()
            response = await client.post(url, json=payload, timeout=float(timeout + 5))
            if response.status_code == 200:
                body: dict[str, Any] = response.json()
                if body.get("ok"):
                    result = body.get("result")
                    if isinstance(result, list):
                        return result
        except Exception:  # noqa: BLE001
            return []
        return []

    async def test_chat(self, chat_id: int, text: str) -> TelegramResult:
        return await self.send_text(chat_id, text)

    async def _call(self, method: str, payload: dict[str, object]) -> TelegramResult:
        url = f"{_BASE_URL}/bot{self._token}/{method}"
        try:
            client = await self._get_client()
            response = await client.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            return TelegramResult(
                ok=False, failure_kind=TelegramFailureKind.TRANSIENT, safe_error="Telegram timeout"
            )
        except httpx.HTTPError:
            return TelegramResult(
                ok=False,
                failure_kind=TelegramFailureKind.TRANSIENT,
                safe_error="Telegram network error",
            )

        return self._parse_response(response)

    def _parse_response(self, response: httpx.Response) -> TelegramResult:
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {}

        if response.status_code == 200 and body.get("ok"):
            result = body.get("result") or {}
            message_id = result.get("message_id") if isinstance(result, dict) else None
            return TelegramResult(ok=True, message_id=message_id)

        if response.status_code == 429:
            parameters = body.get("parameters")
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            return TelegramResult(
                ok=False,
                failure_kind=TelegramFailureKind.RATE_LIMITED,
                retry_after_seconds=int(retry_after) if retry_after is not None else None,
                safe_error="Telegram rate limited",
            )

        description = str(body.get("description") or "").lower()
        if _NOT_MODIFIED_MARKER in description:
            # Nothing to change is a successful outcome, not a failure to retry.
            return TelegramResult(ok=True)
        if any(marker in description for marker in _MESSAGE_NOT_FOUND_MARKERS):
            return TelegramResult(
                ok=False,
                failure_kind=TelegramFailureKind.MESSAGE_NOT_FOUND,
                safe_error="Telegram message not found",
            )
        if any(marker in description for marker in _UNREACHABLE_MARKERS):
            return TelegramResult(
                ok=False,
                failure_kind=TelegramFailureKind.CHAT_UNREACHABLE,
                safe_error="Bot sohbetten engellenmiş veya sohbet bulunamıyor",
            )

        failure_kind = (
            TelegramFailureKind.TRANSIENT
            if response.status_code >= 500
            else TelegramFailureKind.PERMANENT
        )
        return TelegramResult(
            ok=False,
            failure_kind=failure_kind,
            safe_error=f"Telegram HTTP {response.status_code}",
        )
