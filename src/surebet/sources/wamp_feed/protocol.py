"""Safe JSON framing for the anonymous WAMP connection."""

import json
from decimal import Decimal
from typing import Final

MAX_FRAME_BYTES: Final = 5 * 1024 * 1024
_KNOWN_MESSAGE_TYPES: Final = frozenset({1, 2, 3, 6, 8, 32, 33, 36, 48, 50, 64, 65, 68, 70})


class WampFeedProtocolError(ValueError):
    """A public WAMP feed endpoint returned an incompatible WAMP message."""


def encode_hello(realm: str) -> str:
    """Encode an anonymous WAMP HELLO for all roles used by the collector."""
    if not isinstance(realm, str) or not realm:
        raise ValueError("realm must be non-empty")
    return json.dumps(
        [
            1,
            realm,
            {"roles": {"caller": {}, "callee": {}, "publisher": {}, "subscriber": {}}},
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_frame(raw: str | bytes) -> list[object]:
    """Decode a bounded, expected JSON WAMP frame without exposing its contents."""
    if not isinstance(raw, (str, bytes)):
        raise WampFeedProtocolError("WAMP frame must be text or bytes")
    try:
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    except UnicodeError:
        raise WampFeedProtocolError("WAMP frame must be valid UTF-8") from None
    if len(raw_bytes) > MAX_FRAME_BYTES:
        raise WampFeedProtocolError("WAMP frame exceeds size limit")
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except (TypeError, UnicodeError, json.JSONDecodeError):
        raise WampFeedProtocolError("invalid WAMP JSON frame") from None
    if not isinstance(payload, list):
        raise WampFeedProtocolError("WAMP frame must be a JSON array")
    if not payload:
        raise WampFeedProtocolError("WAMP frame must contain a message type")
    message_type = payload[0]
    if not isinstance(message_type, int) or isinstance(message_type, bool):
        raise WampFeedProtocolError("WAMP message type must be an integer")
    if message_type not in _KNOWN_MESSAGE_TYPES:
        raise WampFeedProtocolError(f"unknown WAMP message type {message_type}")
    return payload
