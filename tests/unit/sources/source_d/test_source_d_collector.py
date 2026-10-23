from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from surebet.domain.enums import Outcome, QuoteStatus, Source
from surebet.sources.models import CollectionBatch
from surebet.sources.source_d.client import SourceDClient, SourceDClientError
from surebet.sources.source_d.collector import SourceDCollector, SourceDUnavailable


@pytest.mark.asyncio
async def test_source_d_collector_success() -> None:
    mock_client = AsyncMock(spec=SourceDClient)
    mock_client.fetch_fixtures.return_value = [
        {
            "_id": "881531765548937216",
            "EventName": "Club Brugge karşı Aston Villa",
            "LeagueName": "Şampiyonlar Ligi",
            "MasterLeagueId": "8107",
            "RegionName": "Avrupa",
            "StartEventDate": "2026-09-04T19:00:00Z",
            "IsLive": False,
            "Participants": [
                {"Name": "Club Brugge", "VenueRole": "Home"},
                {"Name": "Aston Villa", "VenueRole": "Away"},
            ],
        }
    ]
    mock_client.fetch_markets_batch.return_value = [
        {
            "EventId": "881531765548937216",
            "MarketType": {"_id": "ML0", "LineTypeName": "1X2"},
            "IsSuspended": False,
            "Selections": [
                {"Side": 1, "TrueOdds": 2.70, "IsDisabled": False, "IsSuspended": False},
                {"Side": 2, "TrueOdds": 3.78, "IsDisabled": False, "IsSuspended": False},
                {"Side": 3, "TrueOdds": 2.46, "IsDisabled": False, "IsSuspended": False},
            ],
        }
    ]

    frozen_time = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    collector = SourceDCollector(client=mock_client, clock=lambda: frozen_time)

    batch = await collector.collect()

    assert isinstance(batch, CollectionBatch)
    assert batch.source == Source.SOURCE_D
    assert len(batch.events) == 1

    ev = batch.events[0]
    assert ev.source == Source.SOURCE_D
    assert ev.source_event_id == "881531765548937216"
    assert ev.home == "Club Brugge"
    assert ev.away == "Aston Villa"
    assert ev.competition == "Şampiyonlar Ligi"
    assert ev.quotes[Outcome.HOME].odds == Decimal("2.70")
    assert ev.quotes[Outcome.DRAW].odds == Decimal("3.78")
    assert ev.quotes[Outcome.AWAY].odds == Decimal("2.46")
    assert ev.quotes[Outcome.HOME].status == QuoteStatus.OPEN

    assert ev.source_competition is not None
    assert ev.source_competition.provider == "source_d"
    assert ev.source_competition.competition_id == "8107"


@pytest.mark.asyncio
async def test_source_d_collector_handles_error() -> None:
    mock_client = AsyncMock(spec=SourceDClient)
    mock_client.fetch_fixtures.side_effect = SourceDClientError("network failure")

    collector = SourceDCollector(client=mock_client)

    with pytest.raises(SourceDUnavailable, match="network failure"):
        await collector.collect()


@pytest.mark.asyncio
async def test_source_d_collector_empty_fixtures() -> None:
    mock_client = AsyncMock(spec=SourceDClient)
    mock_client.fetch_fixtures.return_value = []

    collector = SourceDCollector(client=mock_client)
    batch = await collector.collect()

    assert batch.source == Source.SOURCE_D
    assert len(batch.events) == 0
