from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from surebet.domain.enums import Source
from surebet.sources.endpoints import SourceEndpointConfig

# State reducer snapshots are sanitized plain mappings rather than protocol
# frame objects.  The parser owns their semantic normalization.
WampFeedV2Record = Mapping[str, object]

# Sources whose public feed speaks the WAMP v2 protocol.
WAMP_FEED_SOURCES = (Source.SOURCE_B, Source.SOURCE_C, Source.SOURCE_E, Source.SOURCE_F)
_NOT_WAMP_FEED = "source does not use the WAMP feed protocol"


@dataclass(frozen=True)
class WampFeedSourceConfig:
    source: Source
    websocket_url: str
    origin: str
    realm: str
    operator_id: int
    language: str = "tr"

    def __post_init__(self) -> None:
        try:
            source = self.source if isinstance(self.source, Source) else Source(self.source)
        except (TypeError, ValueError) as exc:
            raise ValueError(_NOT_WAMP_FEED) from exc
        if source not in WAMP_FEED_SOURCES:
            raise ValueError(_NOT_WAMP_FEED)
        if not isinstance(self.websocket_url, str) or not self.websocket_url.strip():
            raise ValueError("websocket_url must be non-empty")
        if not isinstance(self.origin, str) or not self.origin.strip():
            raise ValueError("origin must be non-empty")
        if not isinstance(self.realm, str) or not self.realm.strip():
            raise ValueError("realm must be non-empty")
        if (
            not isinstance(self.operator_id, int)
            or isinstance(self.operator_id, bool)
            or self.operator_id <= 0
        ):
            raise ValueError("operator_id must be positive")
        if source is not self.source:
            object.__setattr__(self, "source", source)

    @classmethod
    def from_endpoint(cls, endpoint: SourceEndpointConfig) -> "WampFeedSourceConfig":
        if endpoint.source not in WAMP_FEED_SOURCES:
            raise ValueError(_NOT_WAMP_FEED)
        if endpoint.realm is None or endpoint.operator_id is None:
            raise ValueError("WAMP feed endpoint contract is incomplete")
        return cls(
            source=endpoint.source,
            websocket_url=endpoint.websocket_url,
            origin=endpoint.origin,
            realm=endpoint.realm,
            operator_id=endpoint.operator_id,
        )


class WampFeedEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    format: str
    message_type: str = Field(alias="messageType")
    records: tuple[Mapping[str, object], ...]
    version: str

    @model_validator(mode="before")
    @classmethod
    def validate_payload(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("payload must be a mapping")  # noqa: TRY004
        if "records" not in value or not isinstance(value["records"], list):
            raise ValueError("records must be a list of mappings")
        if any(not isinstance(record, Mapping) for record in value["records"]):
            raise ValueError("records must be a list of mappings")
        return {**value, "records": tuple(dict(record) for record in value["records"])}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "WampFeedEnvelope":
        return cls.model_validate(payload)
