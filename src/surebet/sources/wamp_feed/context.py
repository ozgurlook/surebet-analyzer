"""Ephemeral, anonymous context and call arguments for WAMP v2."""

import secrets
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from .models import WampFeedSourceConfig

Clock = Callable[[], datetime | int | float]


@dataclass(frozen=True, slots=True)
class WampFeedContext(Mapping[str, object]):
    """Immutable connection-scoped context sent with public v2 calls."""

    v: str
    lang: str
    tz: int
    cid: str
    t: int

    def __post_init__(self) -> None:
        if not self.cid:
            raise ValueError("cid must be non-empty")

    def __getitem__(self, key: str) -> object:
        try:
            return MappingProxyType(
                {"v": self.v, "lang": self.lang, "tz": self.tz, "cid": self.cid, "t": self.t}
            )[key]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[str]:
        return iter(("v", "lang", "tz", "cid", "t"))

    def __len__(self) -> int:
        return 5


def make_context(
    config: WampFeedSourceConfig,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> WampFeedContext:
    """Create a fresh context; no identifier persists beyond this call."""

    value = clock()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock timestamp must be timezone-aware")
        timestamp_ms = int(value.astimezone(UTC).timestamp() * 1000)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp_ms = int(value * 1000) if value < 10_000_000_000 else int(value)
    else:
        raise TypeError("clock must return a datetime or Unix timestamp")
    return WampFeedContext("2", config.language, -180, secrets.token_urlsafe(18), timestamp_ms)


def _ctx(context: WampFeedContext) -> dict[str, object]:
    return dict(context)


def get_session_info_kwargs(ctx: WampFeedContext, lang: str) -> dict[str, object]:
    return {"ctx": _ctx(ctx), "lang": lang}


def configure_fonts_kwargs(ctx: WampFeedContext) -> dict[str, object]:
    return {"ctx": _ctx(ctx)}


def initial_dump_kwargs(ctx: WampFeedContext, topic: str) -> dict[str, object]:
    return {"ctx": _ctx(ctx), "topic": topic}


def market_groups_overview_kwargs(
    ctx: WampFeedContext, lang: str, live_status: str, sport_id: str
) -> dict[str, object]:
    return {"ctx": _ctx(ctx), "lang": lang, "liveStatus": live_status, "sportId": sport_id}


# Keep names close to the protocol procedure names for call sites and fixtures.
getSessionInfo = get_session_info_kwargs
configureFonts = configure_fonts_kwargs
initialDump = initial_dump_kwargs
marketGroupsOverview = market_groups_overview_kwargs
create_context = make_context
context_for_config = make_context
