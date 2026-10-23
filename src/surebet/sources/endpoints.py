"""Operator-supplied provider endpoints for each source adapter.

The repository ships no provider hosts. Whoever runs a collector supplies each
source's endpoint through environment variables named after the source, for
example ``SUREBET_SOURCE_A_ENDPOINT``. A source without an endpoint fails its
collection with ``SourceEndpointNotConfigured``, which the health page shows like
any other source failure.

Keys per source (``<P>`` is ``SUREBET_<SOURCE>_``):

``<P>ENDPOINT``     WebSocket or HTTPS API base URL (required)
``<P>ORIGIN``       Origin header sent with the handshake (defaults to the endpoint host)
``<P>PAGE_URL``     Public page that hands out anonymous tokens (defaults to ``ORIGIN``)
``<P>REALM``        WAMP realm, WAMP-feed sources only
``<P>OPERATOR_ID``  Numeric operator id (WAMP-feed sources; Source A's JSON-RPC site id)
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from surebet.domain.enums import Source
from surebet.sources.models import SourceUnavailable

_ALLOWED_SCHEMES = frozenset({"https", "wss"})


class SourceEndpointNotConfigured(SourceUnavailable):
    """No endpoint has been configured for a source."""


@dataclass(frozen=True, slots=True)
class SourceEndpointConfig:
    source: Source
    page_url: str
    websocket_url: str
    origin: str
    realm: str | None = None
    operator_id: int | None = None

    def __post_init__(self) -> None:
        for name in ("page_url", "websocket_url", "origin"):
            _validate_url(name, getattr(self, name))
        if self.operator_id is not None and (
            isinstance(self.operator_id, bool) or self.operator_id <= 0
        ):
            raise ValueError("operator_id must be positive")


def _validate_url(name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a URL without surrounding whitespace")
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute https:// or wss:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{name} must not contain credentials")


def endpoint_env_prefix(source: Source) -> str:
    """Return the environment variable prefix that configures one source."""

    return f"SUREBET_{Source(source).value.upper()}_"


def load_source_endpoints(
    source: Source,
    environ: Mapping[str, str] | None = None,
) -> SourceEndpointConfig:
    """Read and validate one source's endpoint from the environment."""

    env = os.environ if environ is None else environ
    normalized = Source(source)
    prefix = endpoint_env_prefix(normalized)

    def read(key: str) -> str | None:
        value = env.get(prefix + key, "").strip()
        return value or None

    endpoint = read("ENDPOINT")
    if endpoint is None:
        raise SourceEndpointNotConfigured(f"endpoint not configured ({prefix}ENDPOINT)")
    _validate_url("endpoint", endpoint)
    origin = read("ORIGIN") or f"https://{urlsplit(endpoint).netloc}"
    operator_id_raw = read("OPERATOR_ID")
    try:
        operator_id = int(operator_id_raw) if operator_id_raw is not None else None
    except ValueError as error:
        raise ValueError(f"{prefix}OPERATOR_ID must be an integer") from error
    return SourceEndpointConfig(
        source=normalized,
        page_url=read("PAGE_URL") or origin,
        websocket_url=endpoint,
        origin=origin,
        realm=read("REALM"),
        operator_id=operator_id,
    )
