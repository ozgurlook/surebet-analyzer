"""Anonymous async collector for Source D pre-match football events."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from surebet.domain.enums import Outcome, QuoteStatus, Source
from surebet.domain.models import Quote
from surebet.sources.endpoints import SourceEndpointConfig, load_source_endpoints
from surebet.sources.models import (
    FULL_TIME_RESULT_MARKET,
    CollectedEvent,
    CollectionBatch,
    SourceUnavailable,
)
from surebet.taxonomy.models import (
    CompetitionKind,
    SourceCompetitionEvidence,
    SourceIdentityKind,
)

from .client import SourceDClient, SourceDClientError

_BATCH_CHUNK_SIZE = 25
_MAX_CONCURRENT_REQUESTS = 6


class SourceDUnavailable(SourceUnavailable):
    """The public Source D source could not produce a collection batch."""


def _parse_datetime(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            # Handle ISO formats
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return None
    return None


def _extract_teams(event: dict[str, Any]) -> tuple[str, str] | None:
    participants = event.get("Participants")
    home_team = None
    away_team = None

    if isinstance(participants, list) and len(participants) >= 2:
        for p in participants:
            if not isinstance(p, dict):
                continue
            role = str(p.get("VenueRole", "")).lower()
            name = str(p.get("Name", "")).strip()
            if not name:
                continue
            if role == "home":
                home_team = name
            elif role == "away":
                away_team = name

        if not home_team and not away_team and len(participants) >= 2:
            home_team = str(participants[0].get("Name", "")).strip()
            away_team = str(participants[1].get("Name", "")).strip()

    if not home_team or not away_team:
        event_name = str(event.get("EventName") or event.get("BetslipLine") or "").strip()
        for sep in [" karşı ", " vs. ", " vs ", " - "]:
            if sep in event_name:
                parts = event_name.split(sep, 1)
                home_team = parts[0].strip()
                away_team = parts[1].strip()
                break

    if home_team and away_team:
        return home_team, away_team
    return None


class SourceDCollector:
    """Collects pre-match football matches and 1X2 odds from Source D."""

    def __init__(
        self,
        *,
        endpoint_config: SourceEndpointConfig | None = None,
        timeout_seconds: float = 25.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client: SourceDClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        endpoint = endpoint_config or load_source_endpoints(Source.SOURCE_D)
        if endpoint.source is not Source.SOURCE_D:
            raise ValueError("endpoint source must be SOURCE_D")

        self._endpoint_config = endpoint
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._client = client or SourceDClient(
            page_url=endpoint.page_url,
            api_base_url=endpoint.websocket_url,
            timeout_seconds=timeout_seconds,
        )

    async def collect(self) -> CollectionBatch:
        started_at = self._clock()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw_events = await self._client.fetch_fixtures()
                if not raw_events:
                    return CollectionBatch(
                        source=Source.SOURCE_D,
                        started_at=started_at,
                        finished_at=self._clock(),
                        events=(),
                    )

                # Filter valid football events with IDs
                valid_events: dict[str, dict[str, Any]] = {}
                for ev in raw_events:
                    ev_id = str(ev.get("_id", "")).strip()
                    if ev_id and not ev.get("IsLive", False):
                        valid_events[ev_id] = ev

                event_ids = list(valid_events.keys())
                chunks = [
                    event_ids[i : i + _BATCH_CHUNK_SIZE]
                    for i in range(0, len(event_ids), _BATCH_CHUNK_SIZE)
                ]

                semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)

                async def _fetch_chunk(chunk: list[str]) -> list[dict[str, Any]]:
                    async with semaphore:
                        try:
                            return await self._client.fetch_markets_batch(chunk)
                        except Exception:  # noqa: BLE001
                            return []

                results = await asyncio.gather(*(_fetch_chunk(chunk) for chunk in chunks))

                # Map event_id -> 1X2 market
                markets_by_event: dict[str, dict[str, Any]] = {}
                for chunk_result in results:
                    for market in chunk_result:
                        ev_id = str(market.get("EventId", "")).strip()
                        m_type = market.get("MarketType", {})
                        # Verify ML0 (1X2)
                        if m_type.get("_id") == "ML0" or "1X2" in str(
                            m_type.get("LineTypeName", "")
                        ):
                            markets_by_event[ev_id] = market

        except TimeoutError as err:
            raise SourceDUnavailable("Source D collection timed out") from err
        except SourceDClientError as err:
            raise SourceDUnavailable(f"Source D source error: {err}") from err
        except Exception as err:
            raise SourceDUnavailable(f"Unexpected Source D error: {err}") from err

        observed_at = self._clock()
        collected_events: list[CollectedEvent] = []

        for ev_id, ev in valid_events.items():
            event_market = markets_by_event.get(ev_id)
            if not event_market:
                continue

            teams = _extract_teams(ev)
            if not teams:
                continue
            home_team, away_team = teams

            kickoff = _parse_datetime(ev.get("StartEventDate"))
            if not kickoff:
                continue

            selections = event_market.get("Selections", [])
            if not isinstance(selections, list) or len(selections) < 3:
                continue

            # Map selections by Side: 1=HOME, 2=DRAW, 3=AWAY
            quotes_dict: dict[Outcome, Quote] = {}
            market_suspended = bool(event_market.get("IsSuspended", False))

            for sel in selections:
                side = sel.get("Side")
                outcome = None
                if side == 1:
                    outcome = Outcome.HOME
                elif side == 2:
                    outcome = Outcome.DRAW
                elif side == 3:
                    outcome = Outcome.AWAY

                if outcome is None or outcome in quotes_dict:
                    continue

                raw_price = sel.get("TrueOdds") or sel.get("DisplayOdds", {}).get("Decimal")
                try:
                    price_dec = Decimal(str(raw_price))
                    if price_dec <= Decimal("1.0"):
                        continue
                except (InvalidOperation, TypeError, ValueError):
                    continue

                is_sel_suspended = (
                    market_suspended
                    or bool(sel.get("IsDisabled", False))
                    or bool(sel.get("IsSuspended", False))
                )
                quote_status = QuoteStatus.SUSPENDED if is_sel_suspended else QuoteStatus.OPEN

                quotes_dict[outcome] = Quote(
                    source=Source.SOURCE_D,
                    event_id=ev_id,
                    outcome=outcome,
                    odds=price_dec,
                    observed_at=observed_at,
                    status=quote_status,
                )

            if len(quotes_dict) != 3:
                continue

            competition_name = str(ev.get("LeagueName") or "Unknown League").strip()
            league_id = str(ev.get("MasterLeagueId") or ev.get("LeagueId") or "").strip()
            region_name = str(ev.get("RegionName") or "").strip()

            evidence = SourceCompetitionEvidence(
                provider=Source.SOURCE_D.value,
                identity_kind=SourceIdentityKind.PROVIDER_ID
                if league_id
                else SourceIdentityKind.DERIVED_TEXT,
                competition_id=league_id or f"text:{competition_name}",
                competition_name=competition_name,
                country_name=region_name or None,
                kind=CompetitionKind.LEAGUE,
            )

            try:
                collected = CollectedEvent(
                    source=Source.SOURCE_D,
                    source_event_id=ev_id,
                    competition=competition_name,
                    home=home_team,
                    away=away_team,
                    kickoff=kickoff,
                    market_key=FULL_TIME_RESULT_MARKET,
                    quotes=quotes_dict,
                    source_competition=evidence,
                )
                collected_events.append(collected)
            except Exception:  # noqa: BLE001, S112
                continue

        finished_at = self._clock()
        return CollectionBatch(
            source=Source.SOURCE_D,
            started_at=started_at,
            finished_at=finished_at,
            events=tuple(collected_events),
        )
