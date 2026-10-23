from datetime import UTC, datetime

from surebet.domain.enums import Source
from surebet.sources.wamp_feed.context import (
    WampFeedContext,
    configure_fonts_kwargs,
    get_session_info_kwargs,
    initial_dump_kwargs,
    make_context,
    market_groups_overview_kwargs,
)
from surebet.sources.wamp_feed.models import WampFeedSourceConfig
from tests.conftest import example_endpoint


def test_context_is_ephemeral_and_uses_injected_clock():
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C))
    now = datetime(2026, 8, 24, 12, 34, 56, 789000, tzinfo=UTC)
    first = make_context(config, clock=lambda: now)
    second = make_context(config, clock=lambda: now)

    assert isinstance(first, WampFeedContext)
    assert dict(first) == {
        "v": "2",
        "lang": "tr",
        "tz": -180,
        "cid": first["cid"],
        "t": 1787574896789,
    }
    assert first["cid"] and first["cid"] != second["cid"]
    assert first["t"] == 1787574896789


def test_context_cannot_be_mutated():
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C))
    context = make_context(config, clock=lambda: datetime.now(UTC))
    try:
        context["v"] = "1"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("context must be immutable")


def test_v2_call_helpers_have_explicit_kwargs():
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C))
    context = make_context(config, clock=lambda: datetime.now(UTC))

    assert get_session_info_kwargs(context, "en") == {"ctx": dict(context), "lang": "en"}
    assert configure_fonts_kwargs(context) == {"ctx": dict(context)}
    assert initial_dump_kwargs(context, "prematch") == {
        "ctx": dict(context),
        "topic": "prematch",
    }
    assert market_groups_overview_kwargs(context, "tr", True, 180) == {
        "ctx": dict(context),
        "lang": "tr",
        "liveStatus": True,
        "sportId": 180,
    }
