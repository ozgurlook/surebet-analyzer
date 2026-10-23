import httpx
import pytest

from surebet.sources.source_d.client import SourceDClient


@pytest.mark.asyncio
async def test_refresh_tokens_success() -> None:
    html_content = (
        "<html><head><script>"
        "window.APP_USER_DATA = {'login':{'internalToken':'test_internal_jwt','sessionToken':'test_session_jwt'}};"
        "</script></head><body></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html_content)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SourceDClient(
        page_url="https://source-d.example.test/sports",
        api_base_url="https://source-d.example.test",
        http_client=mock_client,
    )

    internal, session = await client.refresh_tokens()
    assert internal == "test_internal_jwt"
    assert session == "test_session_jwt"


@pytest.mark.asyncio
async def test_fetch_fixtures_success() -> None:
    html_content = (
        "<html><head><script>"
        "window.APP_USER_DATA = {'login':{'internalToken':'tok1','sessionToken':'tok2'}};"
        "</script></head><body></body></html>"
    )
    fixtures_data = {
        "data": [
            {
                "_id": "1001",
                "EventName": "Arsenal vs Chelsea",
                "LeagueName": "Premier League",
                "StartEventDate": "2026-09-04T15:00:00Z",
                "Participants": [
                    {"Name": "Arsenal", "VenueRole": "Home"},
                    {"Name": "Chelsea", "VenueRole": "Away"},
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=html_content)
        if "/api/eventlist/eu/events/v2/all" in str(request.url):
            assert request.headers.get("Authorization") == "tok1"
            assert request.headers.get("Session") == "tok2"
            return httpx.Response(200, json=fixtures_data)
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SourceDClient(
        page_url="https://source-d.example.test/sports",
        api_base_url="https://source-d.example.test",
        http_client=mock_client,
    )

    fixtures = await client.fetch_fixtures()
    assert len(fixtures) == 1
    assert fixtures[0]["_id"] == "1001"


@pytest.mark.asyncio
async def test_fetch_markets_batch_success() -> None:
    html_content = (
        "<html><head><script>"
        "window.APP_USER_DATA = {'login':{'internalToken':'tok1','sessionToken':'tok2'}};"
        "</script></head><body></body></html>"
    )
    markets_data = [
        {
            "EventId": "1001",
            "MarketType": {"_id": "ML0", "LineTypeName": "1X2"},
            "Selections": [
                {"Side": 1, "TrueOdds": 2.10, "IsDisabled": False},
                {"Side": 2, "TrueOdds": 3.40, "IsDisabled": False},
                {"Side": 3, "TrueOdds": 3.20, "IsDisabled": False},
            ],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=html_content)
        if "/api/eventlist/eu/markets/all" in str(request.url):
            assert request.url.params.get("markets") == "1001:ML0"
            return httpx.Response(200, json=markets_data)
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SourceDClient(
        page_url="https://source-d.example.test/sports",
        api_base_url="https://source-d.example.test",
        http_client=mock_client,
    )

    markets = await client.fetch_markets_batch(["1001"])
    assert len(markets) == 1
    assert markets[0]["EventId"] == "1001"
