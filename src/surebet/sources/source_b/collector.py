from collections.abc import Sequence

from websockets.exceptions import WebSocketException

from surebet.domain.enums import Source
from surebet.sources.endpoints import SourceEndpointConfig, load_source_endpoints
from surebet.sources.models import CollectionBatch, SourceUnavailable
from surebet.sources.wamp_feed.collector import (
    Clock,
    ConnectionFactory,
    collect_snapshot,
    default_connection_factory,
    utc_now,
)
from surebet.sources.wamp_feed.contracts import REGISTRATION_SUFFIXES
from surebet.sources.wamp_feed.models import WampFeedSourceConfig
from surebet.sources.wamp_feed.protocol import WampFeedProtocolError


class SourceBUnavailable(SourceUnavailable):
    """The Source B public source could not produce a batch."""


class SourceBProtocolError(SourceBUnavailable):
    """Source B returned an incompatible public WAMP response."""


class SourceBCollector:
    """Collect one complete Source B pre-match football 1X2 snapshot."""

    REGISTRATION_SUFFIXES = REGISTRATION_SUFFIXES

    def __init__(
        self,
        *,
        endpoint_config: SourceEndpointConfig | None = None,
        connection_factory: ConnectionFactory = default_connection_factory,
        timeout_seconds: float = 20.0,
        clock: Clock = utc_now,
        tournament_ids: Sequence[str] = (),
    ) -> None:
        endpoint = endpoint_config or load_source_endpoints(Source.SOURCE_B)
        self._config = WampFeedSourceConfig.from_endpoint(endpoint)
        self._connection_factory = connection_factory
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._tournament_ids = tuple(tournament_ids)

    async def collect(self) -> CollectionBatch:
        """Return one normalized public Source B snapshot."""

        try:
            return await collect_snapshot(
                config=self._config,
                registration_suffixes=self.REGISTRATION_SUFFIXES,
                connection_factory=self._connection_factory,
                timeout_seconds=self._timeout_seconds,
                clock=self._clock,
                tournament_ids=self._tournament_ids,
            )
        except TimeoutError:
            raise SourceBUnavailable("Source B collection timed out") from None
        except WampFeedProtocolError:
            raise SourceBProtocolError("Source B invalid public response") from None
        except (OSError, ValueError, WebSocketException):
            raise SourceBUnavailable("Source B public source unavailable") from None
