import json
from pathlib import Path

import pytest

from surebet.domain.enums import Source
from surebet.taxonomy.tracked import TrackedCompetitionRegistry


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": "test",
        "competitions": [
            {
                "key": "es-la-liga",
                "region_id": "country:ES",
                "display_name": "İspanya La Liga",
                "sources": {"source_a": "7103", "source_c": "3042", "source_b": "3042"},
            }
        ],
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "tracked.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_packaged_registry_loads_every_tracked_competition() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    keys = [entry.key for entry in registry.competitions]
    assert len(keys) == 14
    assert len(set(keys)) == 14
    assert "es-la-liga" in keys
    assert "bask-euroleague" in keys


def test_resolve_returns_entry_for_each_source_identifier() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    source_a = registry.resolve(Source.SOURCE_A, "7103")
    source_c = registry.resolve(Source.SOURCE_C, "910000000000000103")
    source_b = registry.resolve(Source.SOURCE_B, "910000000000000103")

    assert source_a is not None
    assert source_a.key == "es-la-liga"
    assert source_a.region_id == "country:ES"
    assert source_c is source_a
    assert source_b is source_a


def test_resolve_keeps_identifiers_namespaced_by_source() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    assert registry.resolve(Source.SOURCE_A, "910000000000000103") is None
    assert registry.resolve(Source.SOURCE_C, "7103") is None


def test_resolve_returns_none_for_untracked_identifier() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    assert registry.resolve(Source.SOURCE_A, "not-tracked") is None


def test_entry_may_omit_a_source() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    nations_b = next(
        entry for entry in registry.competitions if entry.key == "uefa-nations-league-b"
    )

    assert Source.SOURCE_A not in nations_b.sources
    assert Source.SOURCE_C not in nations_b.sources
    assert nations_b.sources[Source.SOURCE_D] == "8119"


def test_uefa_conference_league_includes_source_a() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    conference = next(
        entry for entry in registry.competitions if entry.key == "uefa-conference-league"
    )

    assert conference.sources[Source.SOURCE_A] == "7110"
    assert conference.sources[Source.SOURCE_C] == "910000000000000110"
    assert conference.sources[Source.SOURCE_B] == "910000000000000110"
    assert conference.sources[Source.SOURCE_D] == "8110"


def test_duplicate_key_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    competitions = list(payload["competitions"])  # type: ignore[arg-type]
    competitions.append(dict(competitions[0]))
    payload["competitions"] = competitions

    with pytest.raises(ValueError, match="duplicate tracked competition key"):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_duplicate_source_identifier_is_rejected(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "a",
                "region_id": "country:ES",
                "display_name": "A",
                "sources": {"source_a": "7103"},
            },
            {
                "key": "b",
                "region_id": "country:IT",
                "display_name": "B",
                "sources": {"source_a": "7103"},
            },
        ]
    )

    with pytest.raises(ValueError, match="duplicate tracked identifier"):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_unknown_region_is_rejected(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "a",
                "region_id": "country:ZZZ",
                "display_name": "A",
                "sources": {"source_a": "7103"},
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown region"):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_blank_key_is_rejected(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "  ",
                "region_id": "country:ES",
                "display_name": "A",
                "sources": {"source_a": "7103"},
            }
        ]
    )

    with pytest.raises(ValueError):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_entry_without_any_source_is_rejected(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "a",
                "region_id": "country:ES",
                "display_name": "A",
                "sources": {},
            }
        ]
    )

    with pytest.raises(ValueError, match="at least one source"):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_unknown_source_name_is_rejected(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "a",
                "region_id": "country:ES",
                "display_name": "A",
                "sources": {"not-a-real-source": "7103"},
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown source for a: not-a-real-source"):
        TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))


def test_sources_mapping_is_genuinely_immutable() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    comp = registry.resolve(Source.SOURCE_A, "7103")

    assert comp is not None
    assert comp.sources[Source.SOURCE_C] == "910000000000000103"
    assert Source.SOURCE_B in comp.sources
    with pytest.raises(TypeError):
        comp.sources[Source.SOURCE_A] = "HACKED"  # type: ignore[index]


def test_uefa_champions_league_includes_source_b_and_source_e() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    ucl = next(entry for entry in registry.competitions if entry.key == "uefa-champions-league")

    assert ucl.sources[Source.SOURCE_B] == "910000000000000107"
    assert ucl.sources[Source.SOURCE_E] == "910000000000000107"
    assert ucl.sources[Source.SOURCE_F] == "910000000000000107"


def test_uefa_europa_league_includes_source_b_and_source_e() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    uel = next(entry for entry in registry.competitions if entry.key == "uefa-europa-league")

    assert uel.sources[Source.SOURCE_B] == "910000000000000108"
    assert uel.sources[Source.SOURCE_E] == "910000000000000108"
    assert uel.sources[Source.SOURCE_C] == "910000000000000108"


def test_get_source_tournament_ids_returns_configured_ids() -> None:
    from surebet.taxonomy.tracked import load_tracked_tournament_ids

    registry = TrackedCompetitionRegistry.load_packaged()
    source_b_ids = registry.get_source_tournament_ids(Source.SOURCE_B)
    assert len(source_b_ids) >= 10
    assert "910000000000000109" in source_b_ids  # Nations League
    assert "910000000000000101" in source_b_ids  # Super Lig

    loaded_ids = load_tracked_tournament_ids(Source.SOURCE_B)
    assert loaded_ids == source_b_ids


def test_resolve_returns_entry_for_source_alias(tmp_path: Path) -> None:
    payload = _payload(
        competitions=[
            {
                "key": "uefa-champions-league",
                "region_id": "region:international",
                "display_name": "UEFA Şampiyonlar Ligi",
                "sources": {"source_b": "910000000000000107", "source_a": "7107"},
                "aliases": {"source_b": ["910000000000000117"]},
            }
        ]
    )
    registry = TrackedCompetitionRegistry.load_path(_write(tmp_path, payload))
    entry_primary = registry.resolve(Source.SOURCE_B, "910000000000000107")
    entry_alias = registry.resolve(Source.SOURCE_B, "910000000000000117")
    assert entry_primary is not None
    assert entry_alias is entry_primary
    assert entry_alias.key == "uefa-champions-league"


def test_packaged_registry_resolves_uefa_champions_league_parent_alias() -> None:
    registry = TrackedCompetitionRegistry.load_packaged()

    for source in (Source.SOURCE_B, Source.SOURCE_C, Source.SOURCE_E, Source.SOURCE_F):
        primary = registry.resolve(source, "910000000000000107")
        parent = registry.resolve(source, "910000000000000117")
        assert primary is not None, f"primary resolution failed for {source}"
        assert parent is not None, f"parent alias resolution failed for {source}"
        assert parent is primary
        assert parent.key == "uefa-champions-league"
