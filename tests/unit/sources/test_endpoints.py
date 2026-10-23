import dataclasses

import pytest

from surebet.domain.enums import Source
from surebet.sources.endpoints import (
    SourceEndpointConfig,
    SourceEndpointNotConfigured,
    endpoint_env_prefix,
    load_source_endpoints,
)
from surebet.sources.models import SourceUnavailable


def test_env_prefix_is_derived_from_the_source_value() -> None:
    assert endpoint_env_prefix(Source.SOURCE_A) == "SUREBET_SOURCE_A_"
    assert endpoint_env_prefix(Source.SOURCE_F) == "SUREBET_SOURCE_F_"


def test_missing_endpoint_is_a_source_failure_not_a_crash() -> None:
    with pytest.raises(SourceEndpointNotConfigured, match="SUREBET_SOURCE_B_ENDPOINT"):
        load_source_endpoints(Source.SOURCE_B, environ={})
    assert issubclass(SourceEndpointNotConfigured, SourceUnavailable)


def test_loads_full_wamp_feed_style_contract() -> None:
    config = load_source_endpoints(
        Source.SOURCE_C,
        environ={
            "SUREBET_SOURCE_C_ENDPOINT": "wss://feed.example.test/v2",
            "SUREBET_SOURCE_C_ORIGIN": "https://sports.example.test",
            "SUREBET_SOURCE_C_REALM": "realm-c",
            "SUREBET_SOURCE_C_OPERATOR_ID": "1001",
        },
    )

    assert config == SourceEndpointConfig(
        source=Source.SOURCE_C,
        page_url="https://sports.example.test",
        websocket_url="wss://feed.example.test/v2",
        origin="https://sports.example.test",
        realm="realm-c",
        operator_id=1001,
    )


def test_origin_and_page_url_default_to_the_endpoint_host() -> None:
    config = load_source_endpoints(
        Source.SOURCE_A,
        environ={"SUREBET_SOURCE_A_ENDPOINT": " wss://rpc.example.test/ "},
    )

    assert config.websocket_url == "wss://rpc.example.test/"
    assert config.origin == "https://rpc.example.test"
    assert config.page_url == "https://rpc.example.test"
    assert config.realm is None
    assert config.operator_id is None


def test_other_sources_keys_are_ignored() -> None:
    with pytest.raises(SourceEndpointNotConfigured):
        load_source_endpoints(
            Source.SOURCE_D,
            environ={"SUREBET_SOURCE_A_ENDPOINT": "https://api.example.test"},
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://feed.example.test/v2",
        "ws://feed.example.test/v2",
        "feed.example.test/v2",
        "wss://user@feed.example.test/v2",
        "wss:///v2",
    ],
)
def test_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError):
        load_source_endpoints(Source.SOURCE_C, environ={"SUREBET_SOURCE_C_ENDPOINT": endpoint})


def test_rejects_non_numeric_operator_id() -> None:
    with pytest.raises(ValueError, match="OPERATOR_ID"):
        load_source_endpoints(
            Source.SOURCE_C,
            environ={
                "SUREBET_SOURCE_C_ENDPOINT": "wss://feed.example.test/v2",
                "SUREBET_SOURCE_C_OPERATOR_ID": "abc",
            },
        )


def test_rejects_non_positive_operator_id() -> None:
    with pytest.raises(ValueError, match="operator_id"):
        SourceEndpointConfig(
            source=Source.SOURCE_C,
            page_url="https://feed.example.test",
            websocket_url="wss://feed.example.test/v2",
            origin="https://feed.example.test",
            operator_id=0,
        )


def test_endpoint_config_is_immutable() -> None:
    config = load_source_endpoints(
        Source.SOURCE_E,
        environ={"SUREBET_SOURCE_E_ENDPOINT": "wss://feed.example.test/v2"},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.websocket_url = "wss://other.example.test/v2"  # type: ignore[misc]
