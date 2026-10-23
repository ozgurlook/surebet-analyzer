import pytest

from surebet.domain.enums import Source
from surebet.sources.wamp_feed.models import WampFeedEnvelope, WampFeedSourceConfig
from tests.conftest import example_endpoint, example_host


@pytest.mark.parametrize(
    ("source", "operator_id"),
    [
        (Source.SOURCE_C, 1001),
        (Source.SOURCE_B, 1002),
        (Source.SOURCE_E, 1003),
        (Source.SOURCE_F, 1004),
    ],
)
def test_source_config_accepts_wamp_feed_endpoint(source: Source, operator_id: int) -> None:
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(source))

    assert config.source is source
    assert config.websocket_url == f"wss://{example_host(source)}/v2"
    assert config.origin == f"https://{example_host(source)}"
    assert config.realm == f"realm-{source.value}"
    assert config.operator_id == operator_id


@pytest.mark.parametrize("source", [Source.SOURCE_A, Source.SOURCE_D])
def test_source_config_rejects_other_protocols(source: Source) -> None:
    with pytest.raises(ValueError, match="WAMP feed"):
        WampFeedSourceConfig.from_endpoint(example_endpoint(source))


def test_source_config_rejects_invalid_values():
    with pytest.raises(ValueError):
        WampFeedSourceConfig(Source.SOURCE_A, "ws", "origin", "realm", 1)
    with pytest.raises(ValueError):
        WampFeedSourceConfig(Source.SOURCE_C, "", "origin", "realm", 1)
    with pytest.raises(ValueError):
        WampFeedSourceConfig(Source.SOURCE_C, "ws", "origin", "", 1)
    with pytest.raises(ValueError):
        WampFeedSourceConfig(Source.SOURCE_C, "ws", "origin", "realm", 0)


def test_source_config_is_immutable():
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C))
    with pytest.raises((AttributeError, TypeError)):
        config.operator_id = 1


def test_envelope_requires_records_tuple_of_mappings():
    with pytest.raises(ValueError, match="records"):
        WampFeedEnvelope.from_payload(
            {"format": "json", "messageType": "x", "records": {}, "version": "1"}
        )


def test_envelope_maps_message_type_and_normalizes_records():
    envelope = WampFeedEnvelope.from_payload(
        {"format": "json", "messageType": "EVENT", "records": [{"id": 1}], "version": "1"}
    )
    assert envelope.message_type == "EVENT"
    assert envelope.records == ({"id": 1},)
