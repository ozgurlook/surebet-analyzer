from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from surebet.domain.enums import Source
from surebet.storage.db import get_connection, init_db
from surebet.storage.taxonomy import TaxonomyConflict, TaxonomyRepository
from surebet.taxonomy.models import (
    CompetitionKind,
    CompetitionMapping,
    CreateLeagueInput,
    MappingMethod,
    ReviewState,
    SourceCompetitionEvidence,
    SourceIdentityKind,
)
from surebet.taxonomy.normalizer import normalize_taxonomy_text

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> TaxonomyRepository:
    db_path = tmp_path / "taxonomy.db"
    init_db(db_path)
    return TaxonomyRepository(db_path)


def evidence(
    source: Source,
    competition_id: str = "42",
    *,
    identity_kind: SourceIdentityKind = SourceIdentityKind.PROVIDER_ID,
) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=identity_kind,
        competition_id=competition_id,
        competition_name="Süper Lig",
        country_id="TR" if identity_kind is SourceIdentityKind.PROVIDER_ID else None,
        country_name="Türkiye" if identity_kind is SourceIdentityKind.PROVIDER_ID else None,
        country_alias="Turkey" if identity_kind is SourceIdentityKind.PROVIDER_ID else None,
        kind=CompetitionKind.LEAGUE,
        gender="men",
        age_group=None,
        is_reserve=False,
    )


def test_same_provider_id_is_isolated_by_source(repo: TaxonomyRepository) -> None:
    repo.observe(Source.SOURCE_C, evidence(Source.SOURCE_C), NOW)
    repo.observe(Source.SOURCE_B, evidence(Source.SOURCE_B), NOW)

    rows = repo.list_source_competitions(limit=10, offset=0)

    assert {(row.source, row.competition_id) for row in rows} == {
        (Source.SOURCE_C, "42"),
        (Source.SOURCE_B, "42"),
    }


def test_observe_batch_persists_all_evidences_atomically(repo: TaxonomyRepository) -> None:
    ev1 = evidence(Source.SOURCE_A, competition_id="101")
    ev2 = evidence(Source.SOURCE_A, competition_id="102")
    repo.observe_batch(Source.SOURCE_A, (ev1, ev2), NOW)

    rows = repo.list_source_competitions(limit=10, offset=0)
    cids = {row.competition_id for row in rows}
    assert "101" in cids
    assert "102" in cids


def test_observe_preserves_first_seen_and_advances_last_seen(
    repo: TaxonomyRepository,
) -> None:
    first = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    second = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW + timedelta(minutes=5))

    assert first.first_seen_at == NOW
    assert second.first_seen_at == NOW
    assert second.last_seen_at == NOW + timedelta(minutes=5)


def test_observe_keeps_time_bounds_when_observations_arrive_out_of_order(
    repo: TaxonomyRepository,
) -> None:
    repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW + timedelta(minutes=5))

    observed = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)

    assert observed.first_seen_at == NOW
    assert observed.last_seen_at == NOW + timedelta(minutes=5)


def test_structured_observation_auto_maps_but_text_identity_stays_unmatched(
    repo: TaxonomyRepository,
) -> None:
    structured = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    text = repo.observe(
        Source.SOURCE_C,
        evidence(Source.SOURCE_C, "text:super-lig", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )

    verified = repo.build_verified_index()

    assert (structured.source, structured.competition_id) in verified
    assert (text.source, text.competition_id) not in verified
    assert [row.competition_id for row in repo.list_unmatched(limit=10, offset=0)] == [
        "text:super-lig"
    ]


def test_structured_observation_with_unknown_kind_auto_maps(repo: TaxonomyRepository) -> None:
    incomplete = evidence(Source.SOURCE_C, "unknown-kind").model_copy(
        update={"kind": CompetitionKind.UNKNOWN}
    )

    observed = repo.observe(Source.SOURCE_C, incomplete, NOW)
    resolved = repo.build_verified_index()[(Source.SOURCE_C, observed.competition_id)]

    assert resolved.method is MappingMethod.VERIFIED_SOURCE_ID
    assert resolved.region.region_id == "country:TR"
    assert resolved.league.display_name == "Süper Lig"
    assert resolved.league.kind is CompetitionKind.UNKNOWN
    assert repo.list_unmatched() == ()


def test_unknown_kind_with_unresolved_country_stays_unmatched(
    repo: TaxonomyRepository,
) -> None:
    unresolved = evidence(Source.SOURCE_B, "unresolved-country").model_copy(
        update={
            "kind": CompetitionKind.UNKNOWN,
            "country_id": "not-a-country",
            "country_name": "Atlantis",
            "country_alias": None,
        }
    )

    observed = repo.observe(Source.SOURCE_B, unresolved, NOW)

    assert (Source.SOURCE_B, observed.competition_id) not in repo.build_verified_index()
    assert repo.list_unmatched() == (observed,)


def test_pending_suggestion_is_not_resolved_until_approved(
    repo: TaxonomyRepository,
) -> None:
    resolved = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    target = repo.build_verified_index()[(Source.SOURCE_A, resolved.competition_id)]
    source_row = repo.observe(
        Source.SOURCE_C,
        evidence(Source.SOURCE_C, "pending", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    pending = repo.save_mapping(
        CompetitionMapping(
            source=Source.SOURCE_C,
            competition_id=source_row.competition_id,
            league_id=target.league.league_id,
            method=MappingMethod.SUGGESTION,
            confidence=0.8,
            review_state=ReviewState.PENDING,
            note=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    assert (Source.SOURCE_C, "pending") not in repo.build_verified_index()

    approved = repo.approve_suggestion(
        Source.SOURCE_C,
        "pending",
        expected_updated_at=pending.updated_at,
        actor="alice",
    )

    assert approved.review_state is ReviewState.APPROVED
    assert repo.build_verified_index()[(Source.SOURCE_C, "pending")].league == target.league


def test_automatic_and_pending_mapping_writes_are_audited(
    repo: TaxonomyRepository,
) -> None:
    resolved = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    target = repo.build_verified_index()[(Source.SOURCE_A, resolved.competition_id)]
    source_row = repo.observe(
        Source.SOURCE_C,
        evidence(Source.SOURCE_C, "pending-audit", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    repo.save_mapping(
        CompetitionMapping(
            source=Source.SOURCE_C,
            competition_id=source_row.competition_id,
            league_id=target.league.league_id,
            method=MappingMethod.SUGGESTION,
            confidence=0.8,
            review_state=ReviewState.PENDING,
            note=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    with get_connection(repo.db_path) as conn:
        audit_rows = conn.execute(
            "SELECT action, actor, before_json, after_json FROM taxonomy_audit ORDER BY audit_id;"
        ).fetchall()

    assert [(row["action"], row["actor"]) for row in audit_rows] == [
        ("auto_map", "system"),
        ("save_mapping", "system"),
    ]
    assert audit_rows[0]["before_json"] is None
    assert audit_rows[0]["after_json"] is not None
    assert audit_rows[1]["before_json"] is None
    assert audit_rows[1]["after_json"] is not None


def test_manual_link_takes_precedence_and_can_be_removed(
    repo: TaxonomyRepository,
) -> None:
    target_row = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    target = repo.build_verified_index()[(Source.SOURCE_A, target_row.competition_id)]
    source_row = repo.observe(
        Source.SOURCE_B,
        evidence(Source.SOURCE_B, "manual", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )

    mapping = repo.link_manual(
        Source.SOURCE_B,
        "manual",
        target.league.league_id,
        expected_updated_at=source_row.last_seen_at,
        actor="alice",
        note="checked",
    )

    assert mapping.method is MappingMethod.MANUAL
    assert repo.build_verified_index()[(Source.SOURCE_B, "manual")].league == target.league

    repo.remove_manual_mapping(
        Source.SOURCE_B,
        "manual",
        expected_updated_at=mapping.updated_at,
        actor="alice",
    )

    assert (Source.SOURCE_B, "manual") not in repo.build_verified_index()


def test_create_link_rename_and_audit_are_transactional(
    repo: TaxonomyRepository,
) -> None:
    source_row = repo.observe(
        Source.SOURCE_C,
        evidence(Source.SOURCE_C, "new", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    mapping = repo.create_and_link(
        Source.SOURCE_C,
        "new",
        "country:TR",
        CreateLeagueInput(
            display_name="1. Lig",
            kind=CompetitionKind.LEAGUE,
            gender="men",
            age_group=None,
            is_reserve=False,
        ),
        expected_updated_at=source_row.last_seen_at,
        actor="alice",
    )
    with get_connection(repo.db_path) as conn:
        version = datetime.fromisoformat(
            conn.execute(
                "SELECT updated_at FROM canonical_leagues WHERE league_id = ?;",
                (mapping.league_id,),
            ).fetchone()[0]
        )

    renamed = repo.rename_league(mapping.league_id, "TFF 1. Lig", version, actor="alice")

    assert renamed.display_name == "TFF 1. Lig"
    with get_connection(repo.db_path) as conn:
        rows = conn.execute(
            "SELECT action, actor FROM taxonomy_audit ORDER BY audit_id;"
        ).fetchall()
        actions = [row[0] for row in rows]
        alias_count = conn.execute(
            "SELECT COUNT(*) FROM league_aliases WHERE league_id = ?;",
            (mapping.league_id,),
        ).fetchone()[0]
    assert actions[-2:] == ["create_and_link", "rename_league"]
    # The mapping-review actor is the real authenticated username threaded in
    # by the caller, not the old singleton-era "single_user" literal.
    assert [row[1] for row in rows[-2:]] == ["alice", "alice"]
    assert alias_count == 1


def test_original_alias_resolves_to_renamed_league_without_creating_a_duplicate(
    repo: TaxonomyRepository,
) -> None:
    first = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    original = repo.build_verified_index()[(Source.SOURCE_A, first.competition_id)]
    with get_connection(repo.db_path) as conn:
        version = datetime.fromisoformat(
            conn.execute(
                "SELECT updated_at FROM canonical_leagues WHERE league_id = ?;",
                (original.league.league_id,),
            ).fetchone()[0]
        )
    repo.rename_league(original.league.league_id, "Trendyol Super Lig", version, actor="alice")

    second = repo.observe(Source.SOURCE_B, evidence(Source.SOURCE_B, "source_b-42"), NOW)

    resolved = repo.build_verified_index()[(Source.SOURCE_B, second.competition_id)]
    assert resolved.league.league_id == original.league.league_id
    assert resolved.method is MappingMethod.EXACT_ALIAS
    assert len(repo.list_leagues(region_ids=("country:TR",))) == 1


def test_manual_link_rejects_country_and_qualifier_conflicts(
    repo: TaxonomyRepository,
) -> None:
    structured = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    neutral_text = evidence(
        Source.SOURCE_B, "germany", identity_kind=SourceIdentityKind.DERIVED_TEXT
    ).model_copy(update={"kind": CompetitionKind.UNKNOWN, "gender": None, "is_reserve": None})
    text = repo.observe(Source.SOURCE_B, neutral_text, NOW)
    german = repo.create_and_link(
        Source.SOURCE_B,
        text.competition_id,
        "country:DE",
        CreateLeagueInput(
            display_name="Süper Lig",
            kind=CompetitionKind.CUP,
            gender="women",
            age_group=None,
            is_reserve=False,
        ),
        expected_updated_at=text.last_seen_at,
        actor="alice",
    )
    with get_connection(repo.db_path) as conn:
        mapping_version = datetime.fromisoformat(
            conn.execute(
                "SELECT updated_at FROM competition_mappings WHERE source=? AND competition_id=?;",
                (structured.source.value, structured.competition_id),
            ).fetchone()[0]
        )

    with pytest.raises(ValueError, match="conflict"):
        repo.link_manual(
            structured.source,
            structured.competition_id,
            german.league_id,
            expected_updated_at=mapping_version,
            actor="alice",
        )


def test_stale_expected_timestamp_raises_typed_conflict(
    repo: TaxonomyRepository,
) -> None:
    source_row = repo.observe(
        Source.SOURCE_C,
        evidence(Source.SOURCE_C, "stale", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )

    with pytest.raises(TaxonomyConflict):
        repo.link_manual(
            Source.SOURCE_C,
            "stale",
            "league:00000000-0000-4000-8000-000000000000",
            expected_updated_at=source_row.last_seen_at - timedelta(seconds=1),
            actor="alice",
        )


def test_reads_expose_optimistic_versions_for_review_screens(
    repo: TaxonomyRepository,
) -> None:
    resolved = repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A), NOW)
    target = repo.build_verified_index()[(Source.SOURCE_A, resolved.competition_id)]

    mapping = repo.get_mapping(Source.SOURCE_A, resolved.competition_id)
    league_state = repo.get_league(target.league.league_id)

    assert mapping is not None
    assert mapping.league_id == target.league.league_id
    assert mapping.updated_at == NOW
    assert league_state is not None
    assert league_state.league == target.league
    assert league_state.updated_at == NOW

    renamed = repo.rename_league(
        target.league.league_id,
        "Trendyol Süper Lig",
        expected_updated_at=league_state.updated_at,
        actor="alice",
    )
    reread = repo.get_league(target.league.league_id)

    assert reread is not None
    assert reread.league.display_name == renamed.display_name
    assert reread.updated_at > league_state.updated_at


def test_reads_return_none_for_unknown_identifiers(repo: TaxonomyRepository) -> None:
    assert repo.get_mapping(Source.SOURCE_C, "missing") is None
    assert repo.get_league("league:11111111-1111-4111-8111-111111111111") is None


def test_region_seeding_is_idempotent_and_preserves_manual_label(tmp_path: Path) -> None:
    db_path = tmp_path / "seed.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE canonical_regions SET display_name = ? WHERE region_id = ?;",
            ("Türkiye Cumhuriyeti", "country:TR"),
        )

    init_db(db_path)

    with get_connection(db_path) as conn:
        display_name = conn.execute(
            "SELECT display_name FROM canonical_regions WHERE region_id = ?;",
            ("country:TR",),
        ).fetchone()[0]
    assert display_name == "Türkiye Cumhuriyeti"


def mapping_updated_at(repo: TaxonomyRepository, source: Source, competition_id: str) -> datetime:
    """Read a mapping's optimistic-concurrency version straight from the database."""
    with get_connection(repo.db_path) as conn:
        row = conn.execute(
            "SELECT updated_at FROM competition_mappings WHERE source=? AND competition_id=?;",
            (source.value, competition_id),
        ).fetchone()
    assert row is not None, f"no mapping for {source.value}/{competition_id}"
    return datetime.fromisoformat(row["updated_at"])


def insert_league(repo: TaxonomyRepository, region_id: str, display_name: str) -> str:
    """Create a canonical league directly, standing in for pre-existing data."""
    league_id = f"league:{uuid4()}"
    stamp = NOW.isoformat()
    with get_connection(repo.db_path) as conn:
        conn.execute(
            "INSERT INTO canonical_leagues "
            "(league_id, region_id, display_name, normalized_key, kind, gender, age_group, "
            "is_reserve, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 1, ?, ?);",
            (
                league_id,
                region_id,
                display_name,
                normalize_taxonomy_text(display_name),
                CompetitionKind.UNKNOWN.value,
                stamp,
                stamp,
            ),
        )
    return league_id


def force_mapping(
    repo: TaxonomyRepository,
    source: Source,
    competition_id: str,
    league_id: str,
    method: MappingMethod,
    *,
    updated_at: datetime = NOW,
) -> None:
    """Overwrite a mapping directly, standing in for a database written before this feature."""
    stamp = updated_at.isoformat()
    with get_connection(repo.db_path) as conn:
        conn.execute(
            "INSERT INTO competition_mappings "
            "(source, competition_id, league_id, method, confidence, review_state, note, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 1.0, ?, NULL, ?, ?) "
            "ON CONFLICT(source, competition_id) DO UPDATE SET "
            "league_id=excluded.league_id, method=excluded.method, "
            "updated_at=excluded.updated_at;",
            (
                source.value,
                competition_id,
                league_id,
                method.value,
                ReviewState.APPROVED.value,
                stamp,
                stamp,
            ),
        )


def test_tracked_sources_converge_on_one_canonical_league(repo: TaxonomyRepository) -> None:
    source_a = evidence(Source.SOURCE_A, "7103").model_copy(
        update={"competition_name": "La Liga", "country_name": "İspanya"}
    )
    source_c = evidence(Source.SOURCE_C, "910000000000000103").model_copy(
        update={"competition_name": "İspanya La liga 2026/2027", "country_name": "İspanya"}
    )

    repo.observe(Source.SOURCE_A, source_a, NOW)
    repo.observe(Source.SOURCE_C, source_c, NOW)

    index = repo.build_verified_index()
    source_a = index[(Source.SOURCE_A, "7103")]
    every = index[(Source.SOURCE_C, "910000000000000103")]

    assert source_a.league.league_id == every.league.league_id
    assert source_a.method is MappingMethod.TRACKED_REGISTRY
    assert every.method is MappingMethod.TRACKED_REGISTRY
    assert source_a.league.display_name == "İspanya La Liga"
    assert source_a.region.region_id == "country:ES"


def test_source_c_and_source_b_keep_separate_rows_on_one_league(repo: TaxonomyRepository) -> None:
    source_c = evidence(Source.SOURCE_C, "910000000000000103")
    source_b = evidence(Source.SOURCE_B, "910000000000000103")

    repo.observe(Source.SOURCE_C, source_c, NOW)
    repo.observe(Source.SOURCE_B, source_b, NOW)

    index = repo.build_verified_index()

    assert (
        index[(Source.SOURCE_C, "910000000000000103")].league.league_id
        == index[(Source.SOURCE_B, "910000000000000103")].league.league_id
    )


def test_untracked_competition_sharing_a_name_stays_on_its_own_league(
    repo: TaxonomyRepository,
) -> None:
    source_a = evidence(Source.SOURCE_A, "7103").model_copy(
        update={"competition_name": "La Liga", "country_name": "İspanya"}
    )
    repo.observe(Source.SOURCE_A, source_a, NOW)
    tracked_league = repo.build_verified_index()[(Source.SOURCE_A, "7103")].league.league_id

    untracked = evidence(Source.SOURCE_A, "999").model_copy(
        update={
            "competition_name": "La Liga",
            "country_name": "İspanya",
            "country_id": None,
            "country_alias": None,
        }
    )
    repo.observe(Source.SOURCE_A, untracked, NOW)

    resolved = repo.build_verified_index()[(Source.SOURCE_A, "999")]
    assert resolved.method is MappingMethod.VERIFIED_SOURCE_ID
    assert resolved.league.league_id != tracked_league


def test_tracked_link_repoints_a_preexisting_auto_verified_mapping(
    repo: TaxonomyRepository,
) -> None:
    """A database mapped before this feature existed must converge on re-observation."""
    source_a = evidence(Source.SOURCE_A, "7103").model_copy(
        update={"competition_name": "La Liga", "country_name": "İspanya"}
    )
    observed = repo.observe(Source.SOURCE_A, source_a, NOW)
    stale_league = insert_league(repo, "country:ES", "Stale Auto League")
    force_mapping(repo, Source.SOURCE_A, "7103", stale_league, MappingMethod.VERIFIED_SOURCE_ID)

    repo.observe(Source.SOURCE_A, source_a, NOW)

    resolved = repo.build_verified_index()[(Source.SOURCE_A, "7103")]
    assert resolved.method is MappingMethod.TRACKED_REGISTRY
    assert resolved.league.league_id != stale_league
    assert resolved.league.display_name == "İspanya La Liga"
    assert observed.competition_id == "7103"


def test_tracked_repoint_keeps_mapping_and_audit_timestamps_monotonic(
    repo: TaxonomyRepository,
) -> None:
    newer = NOW + timedelta(minutes=5)
    tracked = evidence(Source.SOURCE_A, "7103").model_copy(
        update={"competition_name": "La Liga", "country_name": "Ä°spanya"}
    )
    repo.observe(Source.SOURCE_A, tracked, newer)
    registry_league = repo.build_verified_index()[(Source.SOURCE_A, "7103")].league.league_id
    stale_league = insert_league(repo, "country:ES", "Stale Auto League")
    force_mapping(
        repo,
        Source.SOURCE_A,
        "7103",
        stale_league,
        MappingMethod.VERIFIED_SOURCE_ID,
        updated_at=newer,
    )
    before = repo.get_mapping(Source.SOURCE_A, "7103")
    assert before is not None

    repo.observe(Source.SOURCE_A, tracked, NOW)

    after = repo.get_mapping(Source.SOURCE_A, "7103")
    assert after is not None
    assert after.league_id == registry_league
    assert after.method is MappingMethod.TRACKED_REGISTRY
    assert after.created_at == before.created_at
    assert after.updated_at == newer
    with get_connection(repo.db_path) as conn:
        audit_row = conn.execute(
            "SELECT occurred_at FROM taxonomy_audit WHERE action='link_tracked' "
            "ORDER BY audit_id DESC LIMIT 1;"
        ).fetchone()
    assert audit_row is not None
    assert datetime.fromisoformat(audit_row["occurred_at"]) == newer


def test_tracked_link_never_overwrites_a_manual_mapping(repo: TaxonomyRepository) -> None:
    tracked = evidence(Source.SOURCE_A, "7103").model_copy(
        update={
            "competition_name": "La Liga",
            "country_name": "İspanya",
            "country_id": None,
            "country_alias": None,
        }
    )
    repo.observe(Source.SOURCE_A, tracked, NOW)

    operator_league = insert_league(repo, "country:ES", "Operator Choice")
    repo.link_manual(
        Source.SOURCE_A,
        "7103",
        operator_league,
        mapping_updated_at(repo, Source.SOURCE_A, "7103"),
        actor="alice",
    )

    repo.observe(Source.SOURCE_A, tracked, NOW)

    resolved = repo.build_verified_index()[(Source.SOURCE_A, "7103")]
    assert resolved.method is MappingMethod.MANUAL
    assert resolved.league.league_id == operator_league


def test_tracked_league_ids_include_current_registry_id_with_manual_mapping(
    repo: TaxonomyRepository,
) -> None:
    tracked = evidence(Source.SOURCE_A, "7103").model_copy(
        update={
            "competition_name": "La Liga",
            "country_name": "Ä°spanya",
            "country_id": None,
            "country_alias": None,
        }
    )
    repo.observe(Source.SOURCE_A, tracked, NOW)
    operator_league = insert_league(repo, "country:ES", "Operator Choice")
    repo.link_manual(
        Source.SOURCE_A,
        "7103",
        operator_league,
        mapping_updated_at(repo, Source.SOURCE_A, "7103"),
        actor="alice",
    )

    assert repo.tracked_league_ids() == frozenset({operator_league})


def test_approved_current_registry_pairs_include_manual_mapping(
    repo: TaxonomyRepository,
) -> None:
    tracked = evidence(Source.SOURCE_A, "7103").model_copy(
        update={
            "competition_name": "La Liga",
            "country_name": "Ã„Â°spanya",
            "country_id": None,
            "country_alias": None,
        }
    )
    repo.observe(Source.SOURCE_A, tracked, NOW)
    operator_league = insert_league(repo, "country:ES", "Operator Choice")
    repo.link_manual(
        Source.SOURCE_A,
        "7103",
        operator_league,
        mapping_updated_at(repo, Source.SOURCE_A, "7103"),
        actor="alice",
    )

    pairs = repo.approved_current_registry_pairs()

    assert isinstance(pairs, frozenset)
    assert pairs == frozenset({(Source.SOURCE_A, "7103")})


def test_tracked_league_ids_exclude_historical_registry_method_for_removed_pair(
    repo: TaxonomyRepository,
) -> None:
    # This provider id is current for SOURCE_C/SOURCE_B, but not for SOURCE_A. Treating ids
    # without their source namespace would incorrectly retain this historical row.
    removed_pair = evidence(Source.SOURCE_A, "910000000000000103")
    repo.observe(Source.SOURCE_A, removed_pair, NOW)
    league_id = repo.build_verified_index()[
        (Source.SOURCE_A, "910000000000000103")
    ].league.league_id
    force_mapping(
        repo,
        Source.SOURCE_A,
        "910000000000000103",
        league_id,
        MappingMethod.TRACKED_REGISTRY,
    )

    assert repo.tracked_league_ids() == frozenset()


def test_tracked_league_ids_exclude_non_approved_current_mapping(
    repo: TaxonomyRepository,
) -> None:
    repo.observe(Source.SOURCE_A, evidence(Source.SOURCE_A, "7103"), NOW)
    with get_connection(repo.db_path) as conn:
        conn.execute(
            "UPDATE competition_mappings SET review_state=? WHERE source=? AND competition_id=?;",
            (ReviewState.PENDING.value, Source.SOURCE_A.value, "7103"),
        )

    assert repo.tracked_league_ids() == frozenset()


def test_tracked_entry_never_observed_creates_nothing(repo: TaxonomyRepository) -> None:
    repo.observe(Source.SOURCE_C, evidence(Source.SOURCE_C, "not-tracked"), NOW)

    index = repo.build_verified_index()

    assert (Source.SOURCE_A, "7103") not in index
    assert all(item.league.display_name != "İspanya La Liga" for item in index.values())


def test_repeated_tracked_observations_are_idempotent(repo: TaxonomyRepository) -> None:
    source_a = evidence(Source.SOURCE_A, "7103").model_copy(
        update={"competition_name": "La Liga", "country_name": "İspanya"}
    )

    repo.observe(Source.SOURCE_A, source_a, NOW)
    first = repo.build_verified_index()[(Source.SOURCE_A, "7103")].league.league_id
    repo.observe(Source.SOURCE_A, source_a, NOW)
    second = repo.build_verified_index()[(Source.SOURCE_A, "7103")].league.league_id

    assert first == second
    with get_connection(repo.db_path) as conn:
        leagues = conn.execute("SELECT COUNT(*) AS n FROM canonical_leagues;").fetchone()["n"]
    assert leagues == 1


def test_untracked_observation_still_uses_auto_map(repo: TaxonomyRepository) -> None:
    other = evidence(Source.SOURCE_C, "not-tracked")

    repo.observe(Source.SOURCE_C, other, NOW)

    resolved = repo.build_verified_index()[(Source.SOURCE_C, "not-tracked")]
    assert resolved.method is MappingMethod.VERIFIED_SOURCE_ID
