"""Conservative local normalization for canonical competition taxonomy."""

import unicodedata
from collections.abc import Mapping
from importlib import resources
from types import MappingProxyType
from typing import Literal, Self

from pydantic import StrictStr, field_validator

from surebet.domain.models import DomainModel

from .models import CanonicalRegion, RegionClass


def normalize_taxonomy_text(value: str) -> str:
    """Build a comparison key without removing semantic qualifier words."""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("i\u0307", "i")
    words = "".join(
        " " if character.isspace() or unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(words.split())


_TURKISH_ALPHABET = "ABC\u00c7DEFG\u011eHI\u0130JKLMNO\u00d6PRS\u015eTU\u00dcVYZ"

_TURKISH_LETTER_RANK: dict[str, int] = {
    letter: rank for rank, letter in enumerate(_TURKISH_ALPHABET)
}
# Lowercase forms, kept distinct where Turkish casing is distinct: dotless "\u0131" folds
# to "I", dotted "i" folds to "\u0130" -- they must NOT collapse onto the same rank.
_TURKISH_LOWER_TO_UPPER = {
    "a": "A",
    "b": "B",
    "c": "C",
    "\u00e7": "\u00c7",
    "d": "D",
    "e": "E",
    "f": "F",
    "g": "G",
    "\u011f": "\u011e",
    "h": "H",
    "\u0131": "I",
    "i": "\u0130",
    "j": "J",
    "k": "K",
    "l": "L",
    "m": "M",
    "n": "N",
    "o": "O",
    "\u00f6": "\u00d6",
    "p": "P",
    "r": "R",
    "s": "S",
    "\u015f": "\u015e",
    "t": "T",
    "u": "U",
    "\u00fc": "\u00dc",
    "v": "V",
    "y": "Y",
    "z": "Z",
}
for _lower, _upper in _TURKISH_LOWER_TO_UPPER.items():
    _TURKISH_LETTER_RANK[_lower] = _TURKISH_LETTER_RANK[_upper]

_TURKISH_FALLBACK_BASE = len(_TURKISH_ALPHABET)


def turkish_sort_key(text: str) -> tuple[int, ...]:
    """Build a Turkish-alphabet collation key, one rank per character.

    Turkish letters (A B C \u00c7 D E F G \u011e H I \u0130 J K L M N O \u00d6 P R S \u015e T U \u00dc V Y Z) are
    ranked by their position in that order, case-insensitively -- except the dotless
    "I"/"\u0131" and dotted "\u0130"/"i" fold to their own respective ranks, since Turkish
    treats them as distinct letters. This keeps API ordering consistent with the
    frontend's ``Intl.Collator("tr-TR", { sensitivity: "base" })`` contract.

    Characters outside the Turkish alphabet (digits, punctuation, other-language
    letters) fall back to their ordinal value, offset to sort after every Turkish
    letter rank -- consistent, but not itself Turkish-aware.
    """
    key: list[int] = []
    for character in text:
        rank = _TURKISH_LETTER_RANK.get(character)
        if rank is not None:
            key.append(rank)
            continue
        lowered = character.lower()
        fallback_character = lowered[0] if lowered else character
        key.append(_TURKISH_FALLBACK_BASE + ord(fallback_character))
    return tuple(key)


class _CountryData(DomainModel):
    iso_code: StrictStr
    display_name: StrictStr
    aliases: tuple[StrictStr, ...]


class _SpecialRegionData(DomainModel):
    region_id: StrictStr
    region_class: Literal["international", "unknown"]
    display_name: StrictStr
    aliases: tuple[StrictStr, ...]


class _DictionaryData(DomainModel):
    version: StrictStr
    countries: tuple[_CountryData, ...]
    special_regions: tuple[_SpecialRegionData, ...]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dictionary version must be nonempty")
        return value


class CountryDictionary:
    """Immutable alias index loaded from the packaged Turkish country data."""

    __slots__ = ("_aliases", "countries", "special_regions", "version")

    version: str
    countries: tuple[CanonicalRegion, ...]
    special_regions: tuple[CanonicalRegion, ...]
    _aliases: Mapping[str, CanonicalRegion]

    def __init__(
        self,
        *,
        version: str,
        countries: tuple[CanonicalRegion, ...],
        special_regions: tuple[CanonicalRegion, ...],
        aliases: Mapping[str, CanonicalRegion],
    ) -> None:
        self.version = version
        self.countries = countries
        self.special_regions = special_regions
        self._aliases = MappingProxyType(dict(aliases))

    @classmethod
    def load_packaged(cls) -> Self:
        resource = resources.files("surebet.taxonomy").joinpath("countries.tr.json")
        data = _DictionaryData.model_validate_json(resource.read_text(encoding="utf-8"))

        countries = tuple(
            CanonicalRegion(
                region_id=f"country:{entry.iso_code}",
                region_class=RegionClass.COUNTRY,
                iso_code=entry.iso_code,
                display_name=entry.display_name,
                normalized_key=normalize_taxonomy_text(entry.display_name),
                active=True,
            )
            for entry in data.countries
        )
        special_regions = tuple(
            CanonicalRegion(
                region_id=entry.region_id,
                region_class=RegionClass(entry.region_class),
                iso_code=None,
                display_name=entry.display_name,
                normalized_key=normalize_taxonomy_text(entry.display_name),
                active=True,
            )
            for entry in data.special_regions
        )

        aliases: dict[str, CanonicalRegion] = {}
        country_ids: set[str] = set()
        for country_entry, region in zip(data.countries, countries, strict=True):
            if region.region_id in country_ids:
                raise ValueError(f"duplicate country in dictionary: {region.region_id}")
            country_ids.add(region.region_id)
            cls._add_aliases(
                aliases,
                region,
                (
                    country_entry.iso_code,
                    country_entry.display_name,
                    *country_entry.aliases,
                ),
            )
        for special_entry, region in zip(data.special_regions, special_regions, strict=True):
            cls._add_aliases(aliases, region, (special_entry.display_name, *special_entry.aliases))

        return cls(
            version=data.version,
            countries=countries,
            special_regions=special_regions,
            aliases=aliases,
        )

    @staticmethod
    def _add_aliases(
        index: dict[str, CanonicalRegion],
        region: CanonicalRegion,
        aliases: tuple[str, ...],
    ) -> None:
        for alias in aliases:
            key = normalize_taxonomy_text(alias)
            if not key:
                raise ValueError(f"blank alias for {region.region_id}")
            existing = index.get(key)
            if existing is not None and existing.region_id != region.region_id:
                raise ValueError(
                    f"country alias {alias!r} is ambiguous between "
                    f"{existing.region_id} and {region.region_id}"
                )
            index[key] = region

    def resolve(self, label: str) -> CanonicalRegion | None:
        return self._aliases.get(normalize_taxonomy_text(label))
