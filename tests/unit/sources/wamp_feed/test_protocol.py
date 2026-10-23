import json
import traceback
from decimal import Decimal

import pytest

from surebet.sources.wamp_feed.protocol import (
    WampFeedProtocolError,
    decode_frame,
    encode_hello,
)


def test_encode_hello_contains_anonymous_wamp_roles() -> None:
    """Removing a caller/callee/pub/sub role would break anonymous bootstrap."""
    message = json.loads(encode_hello("www.example.test"))

    assert message == [
        1,
        "www.example.test",
        {"roles": {"caller": {}, "callee": {}, "publisher": {}, "subscriber": {}}},
    ]


def test_decode_frame_rejects_non_array_without_echoing_the_payload() -> None:
    """Accepting object frames could make the client route malformed WAMP messages."""
    with pytest.raises(WampFeedProtocolError, match="array") as caught:
        decode_frame('{"private_value":"must-not-appear"}')

    assert "must-not-appear" not in str(caught.value)


def test_decode_frame_rejects_unknown_message_type() -> None:
    """Routing an unknown WAMP message type could hide a changed public protocol."""
    with pytest.raises(WampFeedProtocolError, match="unknown WAMP message type 999"):
        decode_frame("[999,1]")


def test_decode_frame_rejects_oversized_bytes() -> None:
    """Accepting frames above the bound could exhaust memory from a public endpoint."""
    oversized = b"[" + (b"0" * (5 * 1024 * 1024)) + b"]"

    with pytest.raises(WampFeedProtocolError, match="size limit"):
        decode_frame(oversized)


def test_decode_frame_preserves_numeric_decimal_values() -> None:
    """Parsing wire numbers as floats would alter the odds passed to the record parser."""
    frame = decode_frame('[50,1,{"odds":2.10},[]]')

    assert frame[2] == {"odds": Decimal("2.10")}


def test_decode_frame_exception_traceback_omits_raw_frame_contents() -> None:
    """Chaining the JSON decoder failure would retain a raw public frame in tracebacks."""
    raw_frame = json.dumps({"private_value": "must-" + "not-appear"})[:-1]
    with pytest.raises(WampFeedProtocolError) as caught:
        decode_frame(raw_frame)

    assert "must-" + "not-appear" not in "".join(traceback.format_exception(caught.value))
