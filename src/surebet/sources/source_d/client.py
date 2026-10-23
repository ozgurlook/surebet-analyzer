"""Anonymous async HTTP client for Source D public endpoints."""

import re
from typing import Any

import httpx

_IFRAME_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_INTERNAL_TOKEN_RE = re.compile(r"'internalToken':'([^']+)'")
_SESSION_TOKEN_RE = re.compile(r"'sessionToken':'([^']+)'")


class SourceDClientError(RuntimeError):
    """Failure communicating with Source D public endpoints."""


class SourceDClient:
    """Fetches anonymous JWT tokens and queries public sports APIs."""

    def __init__(
        self,
        *,
        page_url: str,
        api_base_url: str,
        timeout_seconds: float = 25.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._page_url = page_url
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._custom_client = http_client
        self._internal_token: str | None = None
        self._session_token: str | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        )

    async def refresh_tokens(self) -> tuple[str, str]:
        """Fetch the public HTML and extract fresh anonymous JWT tokens."""
        client = await self._get_client()
        should_close = self._custom_client is None

        try:
            # 1. First probe the iframe directly
            iframe_url = self._api_base_url + "/"
            res = await client.get(iframe_url)

            # If not in iframe root directly, try the main Source D page URL to find iframe src
            if res.status_code != 200 or "internalToken" not in res.text:
                page_res = await client.get(self._page_url)
                if page_res.status_code == 200:
                    match = _IFRAME_RE.search(page_res.text)
                    if match:
                        iframe_src = match.group(1)
                        if iframe_src.startswith("http"):
                            self._api_base_url = iframe_src.rstrip("/")
                        res = await client.get(self._api_base_url + "/")

            token_m = _INTERNAL_TOKEN_RE.search(res.text)
            session_m = _SESSION_TOKEN_RE.search(res.text)

            if not token_m or not session_m:
                raise SourceDClientError("could not extract anonymous tokens from Source D page")

            self._internal_token = token_m.group(1)
            self._session_token = session_m.group(1)
            return self._internal_token, self._session_token
        except httpx.HTTPError as err:
            raise SourceDClientError(f"HTTP connection error fetching tokens: {err}") from err
        finally:
            if should_close:
                await client.aclose()

    async def fetch_fixtures(self) -> list[dict[str, Any]]:
        """Fetch all upcoming pre-match football fixtures."""
        if not self._internal_token or not self._session_token:
            await self.refresh_tokens()

        client = await self._get_client()
        should_close = self._custom_client is None
        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": f"{self._api_base_url}/tr/spbkv4",
                "Origin": self._api_base_url,
                "Authorization": str(self._internal_token),
                "Session": str(self._session_token),
                "time-area": "15",
            }
            body = {
                "sport": ["1"],
                "type": ["Fixture"],
                "live": False,
                "entities": [],
            }
            url = f"{self._api_base_url}/api/eventlist/eu/events/v2/all"
            res = await client.post(url, headers=headers, json=body)
            if res.status_code == 403:
                # Token might have expired, refresh once
                await self.refresh_tokens()
                headers["Authorization"] = str(self._internal_token)
                headers["Session"] = str(self._session_token)
                res = await client.post(url, headers=headers, json=body)

            if res.status_code != 200:
                raise SourceDClientError(
                    f"fixtures endpoint returned status {res.status_code}: {res.text[:200]}"
                )

            payload = res.json()
            if isinstance(payload, dict) and "data" in payload:
                events = payload["data"]
                if isinstance(events, list):
                    return events
            return []
        except httpx.HTTPError as err:
            raise SourceDClientError(f"HTTP error fetching fixtures: {err}") from err
        finally:
            if should_close:
                await client.aclose()

    async def fetch_markets_batch(self, event_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch 1X2 pre-match markets for a batch of event IDs."""
        if not event_ids:
            return []
        if not self._internal_token or not self._session_token:
            await self.refresh_tokens()

        client = await self._get_client()
        should_close = self._custom_client is None
        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{self._api_base_url}/tr/spbkv4",
                "Origin": self._api_base_url,
                "Authorization": str(self._internal_token),
                "Session": str(self._session_token),
                "time-area": "15",
            }
            # Batch query syntax: {id1}|{id2}|...:ML0
            param_val = f"{'|'.join(event_ids)}:ML0"
            url = f"{self._api_base_url}/api/eventlist/eu/markets/all"
            res = await client.get(url, headers=headers, params={"markets": param_val})
            if res.status_code != 200:
                raise SourceDClientError(f"markets endpoint returned status {res.status_code}")

            data = res.json()
            if isinstance(data, list):
                return data
            return []
        except httpx.HTTPError as err:
            raise SourceDClientError(f"HTTP error fetching markets: {err}") from err
        finally:
            if should_close:
                await client.aclose()
