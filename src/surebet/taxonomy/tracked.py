"""Operator-curated registry of tracked competitions, keyed by provider id."""

import functools
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Self

from pydantic import Field, StrictStr, field_serializer, field_validator

from surebet.domain.enums import Source
from surebet.domain.models import DomainModel, _FrozenMapping

from .normalizer import CountryDictionary


class _TrackedData(DomainModel):
    key: StrictStr
    region_id: StrictStr
    display_name: StrictStr
    sources: Mapping[StrictStr, StrictStr]
    aliases: Mapping[StrictStr, tuple[StrictStr, ...] | list[StrictStr]] = Field(
        default_factory=dict
    )

    @field_validator("key", "region_id", "display_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tracked competition fields must be nonempty")
        return value


class _RegistryData(DomainModel):
    version: StrictStr
    competitions: tuple[_TrackedData, ...]


class TrackedCompetition(DomainModel):
    """One curated competition and its provider identifier on each source."""

    key: StrictStr
    region_id: StrictStr
    display_name: StrictStr
    sources: Mapping[Source, StrictStr]
    aliases: Mapping[Source, tuple[StrictStr, ...]] = Field(default_factory=dict)

    @field_validator("sources")
    @classmethod
    def freeze_sources(cls, value: Mapping[Source, str]) -> Mapping[Source, str]:
        return _FrozenMapping(value)

    @field_validator("aliases")
    @classmethod
    def freeze_aliases(
        cls, value: Mapping[Source, tuple[str, ...] | list[str]]
    ) -> Mapping[Source, tuple[str, ...]]:
        return _FrozenMapping({k: tuple(v) for k, v in value.items()})

    @field_serializer("sources")
    def serialize_sources(self, value: Mapping[Source, str]) -> dict[Source, str]:
        return dict(value)

    @field_serializer("aliases")
    def serialize_aliases(
        self, value: Mapping[Source, tuple[str, ...]]
    ) -> dict[Source, tuple[str, ...]]:
        return dict(value)


class TrackedCompetitionRegistry:
    """Immutable index from (source, competition id) to a tracked competition."""

    __slots__ = ("_by_identifier", "competitions", "version")

    version: str
    competitions: tuple[TrackedCompetition, ...]
    _by_identifier: Mapping[tuple[Source, str], TrackedCompetition]

    def __init__(
        self,
        *,
        version: str,
        competitions: tuple[TrackedCompetition, ...],
        by_identifier: Mapping[tuple[Source, str], TrackedCompetition],
    ) -> None:
        self.version = version
        self.competitions = competitions
        self._by_identifier = MappingProxyType(dict(by_identifier))

    @classmethod
    def load_packaged(cls) -> Self:
        resource = resources.files("surebet.taxonomy").joinpath("tracked.tr.json")
        return cls.from_json(resource.read_text(encoding="utf-8"))

    @classmethod
    def load_path(cls, path: Path) -> Self:
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_json(cls, raw: str) -> Self:
        """Build a registry from the same JSON document shape as `tracked.tr.json`."""
        return cls._build(raw)

    @classmethod
    def _build(cls, raw: str) -> Self:
        data = _RegistryData.model_validate_json(raw)
        country_dictionary = CountryDictionary.load_packaged()
        known_regions = {
            region.region_id
            for region in (*country_dictionary.countries, *country_dictionary.special_regions)
        }

        competitions: list[TrackedCompetition] = []
        by_identifier: dict[tuple[Source, str], TrackedCompetition] = {}
        seen_keys: set[str] = set()

        for entry in data.competitions:
            if entry.key in seen_keys:
                raise ValueError(f"duplicate tracked competition key: {entry.key}")
            seen_keys.add(entry.key)
            if entry.region_id not in known_regions:
                raise ValueError(f"unknown region for {entry.key}: {entry.region_id}")
            if not entry.sources:
                raise ValueError(f"{entry.key} must declare at least one source")

            sources: dict[Source, str] = {}
            for name, identifier in entry.sources.items():
                try:
                    source = Source(name)
                except ValueError as error:
                    raise ValueError(f"unknown source for {entry.key}: {name}") from error
                sources[source] = identifier

            aliases: dict[Source, tuple[str, ...]] = {}
            for name, alias_list in entry.aliases.items():
                try:
                    source = Source(name)
                except ValueError as error:
                    raise ValueError(f"unknown source alias for {entry.key}: {name}") from error
                aliases[source] = tuple(alias_list)

            competition = TrackedCompetition(
                key=entry.key,
                region_id=entry.region_id,
                display_name=entry.display_name,
                sources=sources,
                aliases=aliases,
            )
            for source, identifier in sources.items():
                index_key = (source, identifier)
                if index_key in by_identifier:
                    raise ValueError(
                        f"duplicate tracked identifier for {source.value}: {identifier}"
                    )
                by_identifier[index_key] = competition

            for source, alias_list in aliases.items():
                for alias_id in alias_list:
                    index_key = (source, alias_id)
                    if index_key in by_identifier:
                        raise ValueError(
                            f"duplicate tracked identifier for {source.value}: {alias_id}"
                        )
                    by_identifier[index_key] = competition

            competitions.append(competition)

        return cls(
            version=data.version,
            competitions=tuple(competitions),
            by_identifier=by_identifier,
        )

    def resolve(self, source: Source, competition_id: str) -> TrackedCompetition | None:
        return self._by_identifier.get((source, competition_id))

    def get_source_tournament_ids(self, source: Source) -> tuple[str, ...]:
        """Return the unique tournament/competition IDs configured for this source."""
        return tuple(comp.sources[source] for comp in self.competitions if source in comp.sources)


@functools.lru_cache(maxsize=16)
def load_tracked_tournament_ids(source: Source) -> tuple[str, ...]:
    """Return packaged tracked tournament/competition IDs for a given source."""
    registry = TrackedCompetitionRegistry.load_packaged()
    return registry.get_source_tournament_ids(source)
