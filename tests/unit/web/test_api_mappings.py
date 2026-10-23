"""Tests for the authenticated, CSRF-protected mapping administration APIs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.domain.enums import Source
from surebet.storage.auth import AuthRepository
from surebet.storage.db import get_connection, init_db
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import (
    CompetitionKind,
    CompetitionMapping,
    MappingMethod,
    ReviewState,
    SourceCompetitionEvidence,
    SourceIdentityKind,
)
from surebet.web.app import create_app

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)
STALE = NOW - timedelta(hours=1)

TEXT_ID = "text:süper lig"


def _evidence(
    source: Source,
    competition_id: str,
    *,
    competition_name: str = "Süper Lig",
    identity_kind: SourceIdentityKind = SourceIdentityKind.PROVIDER_ID,
    country: tuple[str, str, str] = ("TR", "Türkiye", "Turkey"),
    kind: CompetitionKind = CompetitionKind.LEAGUE,
) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=identity_kind,
        competition_id=competition_id,
        competition_name=competition_name,
        country_id=country[0],
        country_name=country[1],
        country_alias=country[2],
        kind=kind,
        gender="men",
        age_group=None,
        is_reserve=False,
    )


class Fixture:
    """Seeded database plus an authenticated client and its CSRF token."""

    def __init__(
        self,
        client: TestClient,
        csrf_token: str,
        repo: TaxonomyRepository,
        tr_league_id: str,
        de_league_id: str,
    ) -> None:
        self.client = client
        self.csrf_token = csrf_token
        self.repo = repo
        self.tr_league_id = tr_league_id
        self.de_league_id = de_league_id

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf_token}

    def unmatched(self) -> dict[tuple[str, str], dict]:
        response = self.client.get("/api/v1/mappings/unmatched")
        assert response.status_code == 200, response.text
        return {(row["source"], row["competition_id"]): row for row in response.json()}

    def mapped(self) -> dict[tuple[str, str], dict]:
        response = self.client.get("/api/v1/mappings/mapped")
        assert response.status_code == 200, response.text
        return {(row["source"], row["competition_id"]): row for row in response.json()}


@pytest.fixture
def api(tmp_path: Path) -> Fixture:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _user, session = auth.register_user("mappings-tester", "correct-horse-battery-staple")

    repo = TaxonomyRepository(db_path)
    # Structured observations auto-map and create the canonical league targets.
    repo.observe(Source.SOURCE_C, _evidence(Source.SOURCE_C, "42"), NOW)
    repo.observe(
        Source.SOURCE_A,
        _evidence(
            Source.SOURCE_A,
            "de-1",
            competition_name="Bundesliga",
            country=("DE", "Germany", "Deutschland"),
        ),
        NOW,
    )
    # Text-derived identities enter the review queue and never auto-verify.
    repo.observe(
        Source.SOURCE_B,
        _evidence(Source.SOURCE_B, "42", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    repo.observe(
        Source.SOURCE_C,
        _evidence(Source.SOURCE_C, TEXT_ID, identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    repo.observe(
        Source.SOURCE_C,
        _evidence(Source.SOURCE_C, "pending", identity_kind=SourceIdentityKind.DERIVED_TEXT),
        NOW,
    )
    repo.observe(
        Source.SOURCE_A,
        _evidence(
            Source.SOURCE_A,
            "cup",
            competition_name="Türkiye Kupası",
            identity_kind=SourceIdentityKind.DERIVED_TEXT,
            kind=CompetitionKind.CUP,
        ),
        NOW,
    )

    verified = repo.build_verified_index()
    tr_league_id = verified[(Source.SOURCE_C, "42")].league.league_id
    de_league_id = verified[(Source.SOURCE_A, "de-1")].league.league_id

    repo.save_mapping(
        CompetitionMapping(
            source=Source.SOURCE_C,
            competition_id="pending",
            league_id=tr_league_id,
            method=MappingMethod.SUGGESTION,
            confidence=0.8,
            review_state=ReviewState.PENDING,
            note=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    app = create_app(db_path=db_path)
    client = TestClient(app)
    client.cookies.set("session_token", session.token)
    return Fixture(client, session.csrf_token, repo, tr_league_id, de_league_id)


def _mutations(api: Fixture) -> list[tuple[str, str, dict, dict]]:
    """Every mutation route with a body that would succeed if it were authorized."""
    version = NOW.isoformat()
    return [
        ("POST", "/api/v1/mappings/source_c/pending/approve", {"expected_updated_at": version}, {}),
        (
            "PUT",
            "/api/v1/mappings/source_b/42/link",
            {"league_id": api.tr_league_id, "expected_updated_at": version},
            {},
        ),
        (
            "POST",
            "/api/v1/mappings/source_b/42/league",
            {
                "region_id": "country:TR",
                "display_name": "Süper Lig 2",
                "kind": "league",
                "expected_updated_at": version,
            },
            {},
        ),
        (
            "PATCH",
            f"/api/v1/leagues/{api.tr_league_id}",
            {"display_name": "Yeni Ad", "expected_updated_at": version},
            {},
        ),
        ("DELETE", "/api/v1/mappings/source_c/pending", None, {"expected_updated_at": version}),
    ]


def test_unmatched_requires_authenticated_session(api: Fixture) -> None:
    api.client.cookies.clear()

    assert api.client.get("/api/v1/mappings/unmatched").status_code == 401


def test_mapped_requires_authenticated_session(api: Fixture) -> None:
    api.client.cookies.clear()

    assert api.client.get("/api/v1/mappings/mapped").status_code == 401


def test_every_mutation_requires_authenticated_session(api: Fixture) -> None:
    mutations = _mutations(api)
    api.client.cookies.clear()

    for method, url, body, params in mutations:
        response = api.client.request(method, url, json=body, params=params, headers=api.headers())
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


def test_every_mutation_requires_csrf_token(api: Fixture) -> None:
    for method, url, body, params in _mutations(api):
        response = api.client.request(method, url, json=body, params=params)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"

    # No mutation leaked through without a CSRF token.
    assert api.repo.build_verified_index().keys() == {
        (Source.SOURCE_C, "42"),
        (Source.SOURCE_A, "de-1"),
    }


def test_unmatched_returns_normalized_review_items_without_provider_payload(
    api: Fixture,
) -> None:
    response = api.client.get("/api/v1/mappings/unmatched")

    assert response.status_code == 200
    rows = response.json()
    assert [(row["source"], row["competition_id"]) for row in rows] == [
        ("source_a", "cup"),
        ("source_b", "42"),
        ("source_c", "pending"),
        ("source_c", TEXT_ID),
    ]

    pending = rows[2]
    assert set(pending) == {
        "source",
        "competition_id",
        "identity_kind",
        "competition_name",
        "country_id",
        "country_name",
        "country_alias",
        "season",
        "kind",
        "gender",
        "age_group",
        "is_reserve",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
        "method",
        "confidence",
        "review_state",
        "target",
        "conflict_reasons",
    }
    assert pending["review_state"] == "pending"
    assert pending["method"] == "suggestion"
    assert pending["confidence"] == 0.8
    assert pending["target"]["league_id"] == api.tr_league_id
    assert pending["target"]["display_name"] == "Süper Lig"
    assert datetime.fromisoformat(pending["updated_at"]) == NOW
    assert "derived_text_identity" in pending["conflict_reasons"]

    unmapped = rows[3]
    assert unmapped["review_state"] is None
    assert unmapped["target"] is None
    assert unmapped["confidence"] is None
    # Without a mapping the source observation itself is the optimistic version.
    assert unmapped["updated_at"] == unmapped["last_seen_at"]


def test_unmatched_pagination_is_bounded(api: Fixture) -> None:
    first = api.client.get("/api/v1/mappings/unmatched", params={"limit": 2, "offset": 0})
    second = api.client.get("/api/v1/mappings/unmatched", params={"limit": 2, "offset": 2})

    assert [row["competition_id"] for row in first.json()] == ["cup", "42"]
    assert [row["competition_id"] for row in second.json()] == ["pending", TEXT_ID]

    # An oversized limit is clamped instead of reaching the repository as invalid input.
    assert api.client.get("/api/v1/mappings/unmatched", params={"limit": 100000}).status_code == 200
    assert api.client.get("/api/v1/mappings/unmatched", params={"limit": 0}).status_code == 422
    assert api.client.get("/api/v1/mappings/unmatched", params={"offset": -1}).status_code == 422


def test_mapped_returns_only_approved_mappings_with_targets_and_is_bounded(
    api: Fixture,
) -> None:
    response = api.client.get("/api/v1/mappings/mapped")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert {(row["source"], row["competition_id"]) for row in rows} == {
        ("source_c", "42"),
        ("source_a", "de-1"),
    }
    assert all(row["review_state"] == "approved" for row in rows)
    assert all(row["target"] is not None for row in rows)
    assert all(row["method"] == "verified_source_id" for row in rows)
    assert ("source_c", "pending") not in {(row["source"], row["competition_id"]) for row in rows}

    first = api.client.get("/api/v1/mappings/mapped", params={"limit": 1, "offset": 0})
    second = api.client.get("/api/v1/mappings/mapped", params={"limit": 1, "offset": 1})
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(first.json()) == len(second.json()) == 1
    assert first.json()[0]["competition_id"] != second.json()[0]["competition_id"]
    assert api.client.get("/api/v1/mappings/mapped", params={"limit": 0}).status_code == 422
    assert api.client.get("/api/v1/mappings/mapped", params={"offset": -1}).status_code == 422


def test_approve_suggestion_verifies_the_pending_mapping(api: Fixture) -> None:
    response = api.client.post(
        "/api/v1/mappings/source_c/pending/approve",
        json={"expected_updated_at": NOW.isoformat(), "note": "kontrol edildi"},
        headers=api.headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_state"] == "approved"
    assert body["league_id"] == api.tr_league_id
    assert body["note"] == "kontrol edildi"
    assert (Source.SOURCE_C, "pending") in api.repo.build_verified_index()


def test_approve_suggestion_records_the_authenticated_username_as_audit_actor(
    api: Fixture,
) -> None:
    """Mapping review is a shared, multi-user operation (Task 10): the audit
    trail must attribute each mutation to the real authenticated user, not
    the singleton-era hardcoded "single_user" literal."""
    response = api.client.post(
        "/api/v1/mappings/source_c/pending/approve",
        json={"expected_updated_at": NOW.isoformat(), "note": "kontrol edildi"},
        headers=api.headers(),
    )
    assert response.status_code == 200, response.text

    with get_connection(api.repo.db_path) as conn:
        row = conn.execute(
            "SELECT actor FROM taxonomy_audit WHERE action='approve_suggestion' "
            "ORDER BY audit_id DESC LIMIT 1;"
        ).fetchone()

    assert row is not None
    assert row["actor"] == "mappings-tester"


def test_link_existing_league_creates_a_manual_mapping(api: Fixture) -> None:
    item = api.unmatched()[("source_b", "42")]

    response = api.client.put(
        "/api/v1/mappings/source_b/42/link",
        json={"league_id": api.tr_league_id, "expected_updated_at": item["updated_at"]},
        headers=api.headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "manual"
    assert body["review_state"] == "approved"
    verified = api.repo.build_verified_index()
    assert verified[(Source.SOURCE_B, "42")].league.league_id == api.tr_league_id
    # Source C and Source B share the provider ID but never share a mapping.
    assert verified[(Source.SOURCE_C, "42")].method is MappingMethod.VERIFIED_SOURCE_ID


def test_create_and_link_creates_a_canonical_league(api: Fixture) -> None:
    item = api.unmatched()[("source_c", TEXT_ID)]

    response = api.client.post(
        f"/api/v1/mappings/source_c/{TEXT_ID}/league",
        json={
            "region_id": "country:TR",
            "display_name": "Süper Lig Yedek",
            "kind": "league",
            "gender": "men",
            "is_reserve": False,
            "expected_updated_at": item["updated_at"],
        },
        headers=api.headers(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["method"] == "manual"
    resolved = api.repo.build_verified_index()[(Source.SOURCE_C, TEXT_ID)]
    assert resolved.league.league_id == body["league_id"]
    assert resolved.league.display_name == "Süper Lig Yedek"
    assert resolved.region.region_id == "country:TR"


def test_rename_league_updates_the_canonical_display_name(api: Fixture) -> None:
    target = api.unmatched()[("source_c", "pending")]["target"]

    response = api.client.patch(
        f"/api/v1/leagues/{api.tr_league_id}",
        json={"display_name": "Trendyol Süper Lig", "expected_updated_at": target["updated_at"]},
        headers=api.headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Trendyol Süper Lig"
    assert body["league_id"] == api.tr_league_id
    assert body["updated_at"] != target["updated_at"]
    assert (
        api.repo.build_verified_index()[(Source.SOURCE_C, "42")].league.display_name
        == "Trendyol Süper Lig"
    )


def test_delete_removes_a_manual_mapping_and_is_source_scoped(api: Fixture) -> None:
    linked = api.client.put(
        "/api/v1/mappings/source_b/42/link",
        json={
            "league_id": api.tr_league_id,
            "expected_updated_at": api.unmatched()[("source_b", "42")]["updated_at"],
        },
        headers=api.headers(),
    ).json()

    mapped = api.mapped()[("source_b", "42")]
    assert mapped["method"] == "manual"
    assert mapped["target"]["league_id"] == api.tr_league_id

    response = api.client.delete(
        "/api/v1/mappings/source_b/42",
        params={"expected_updated_at": linked["updated_at"]},
        headers=api.headers(),
    )

    assert response.status_code == 204, response.text
    verified = api.repo.build_verified_index()
    assert (Source.SOURCE_B, "42") not in verified
    assert (Source.SOURCE_C, "42") in verified


def test_delete_reverts_an_approved_suggestion_to_the_pending_queue(api: Fixture) -> None:
    approved = api.client.post(
        "/api/v1/mappings/source_c/pending/approve",
        json={"expected_updated_at": NOW.isoformat()},
        headers=api.headers(),
    ).json()

    assert api.mapped()[("source_c", "pending")]["method"] == "suggestion"

    response = api.client.delete(
        "/api/v1/mappings/source_c/pending",
        params={"expected_updated_at": approved["updated_at"]},
        headers=api.headers(),
    )

    assert response.status_code == 204, response.text
    assert ("source_c", "pending") not in api.mapped()
    pending = api.unmatched()[("source_c", "pending")]
    assert pending["review_state"] == "pending"
    assert pending["method"] == "suggestion"
    assert (Source.SOURCE_C, "pending") not in api.repo.build_verified_index()


def test_missing_identifiers_return_404(api: Fixture) -> None:
    version = NOW.isoformat()

    assert (
        api.client.post(
            "/api/v1/mappings/source_c/nope/approve",
            json={"expected_updated_at": version},
            headers=api.headers(),
        ).status_code
        == 404
    )
    assert (
        api.client.put(
            "/api/v1/mappings/source_c/nope/link",
            json={"league_id": api.tr_league_id, "expected_updated_at": version},
            headers=api.headers(),
        ).status_code
        == 404
    )
    assert (
        api.client.put(
            f"/api/v1/mappings/source_c/{TEXT_ID}/link",
            json={
                "league_id": "league:11111111-1111-4111-8111-111111111111",
                "expected_updated_at": version,
            },
            headers=api.headers(),
        ).status_code
        == 404
    )
    assert (
        api.client.post(
            f"/api/v1/mappings/source_c/{TEXT_ID}/league",
            json={
                "region_id": "country:ZZ",
                "display_name": "Yeni",
                "kind": "league",
                "expected_updated_at": version,
            },
            headers=api.headers(),
        ).status_code
        == 404
    )
    assert (
        api.client.patch(
            "/api/v1/leagues/league:11111111-1111-4111-8111-111111111111",
            json={"display_name": "Yeni", "expected_updated_at": version},
            headers=api.headers(),
        ).status_code
        == 404
    )
    assert (
        api.client.delete(
            "/api/v1/mappings/source_c/nope",
            params={"expected_updated_at": version},
            headers=api.headers(),
        ).status_code
        == 404
    )


def test_stale_expected_updated_at_returns_409(api: Fixture) -> None:
    stale = STALE.isoformat()

    assert (
        api.client.post(
            "/api/v1/mappings/source_c/pending/approve",
            json={"expected_updated_at": stale},
            headers=api.headers(),
        ).status_code
        == 409
    )
    assert (
        api.client.put(
            "/api/v1/mappings/source_b/42/link",
            json={"league_id": api.tr_league_id, "expected_updated_at": stale},
            headers=api.headers(),
        ).status_code
        == 409
    )
    assert (
        api.client.post(
            f"/api/v1/mappings/source_c/{TEXT_ID}/league",
            json={
                "region_id": "country:TR",
                "display_name": "Yeni",
                "kind": "league",
                "expected_updated_at": stale,
            },
            headers=api.headers(),
        ).status_code
        == 409
    )
    assert (
        api.client.patch(
            f"/api/v1/leagues/{api.tr_league_id}",
            json={"display_name": "Yeni", "expected_updated_at": stale},
            headers=api.headers(),
        ).status_code
        == 409
    )
    # Nothing changed.
    assert api.repo.build_verified_index().keys() == {
        (Source.SOURCE_C, "42"),
        (Source.SOURCE_A, "de-1"),
    }


def test_country_conflict_is_rejected_with_422(api: Fixture) -> None:
    item = api.unmatched()[("source_c", TEXT_ID)]

    linked = api.client.put(
        f"/api/v1/mappings/source_c/{TEXT_ID}/link",
        json={"league_id": api.de_league_id, "expected_updated_at": item["updated_at"]},
        headers=api.headers(),
    )
    created = api.client.post(
        f"/api/v1/mappings/source_c/{TEXT_ID}/league",
        json={
            "region_id": "country:DE",
            "display_name": "Süper Lig",
            "kind": "league",
            "expected_updated_at": item["updated_at"],
        },
        headers=api.headers(),
    )

    assert linked.status_code == 422, linked.text
    assert created.status_code == 422, created.text
    assert (Source.SOURCE_C, TEXT_ID) not in api.repo.build_verified_index()


def test_competition_kind_conflict_is_rejected_with_422(api: Fixture) -> None:
    item = api.unmatched()[("source_a", "cup")]

    response = api.client.put(
        "/api/v1/mappings/source_a/cup/link",
        json={"league_id": api.tr_league_id, "expected_updated_at": item["updated_at"]},
        headers=api.headers(),
    )

    assert response.status_code == 422, response.text
    assert (Source.SOURCE_A, "cup") not in api.repo.build_verified_index()


def test_qualifier_gender_conflict_is_rejected_with_422(api: Fixture) -> None:
    item = api.unmatched()[("source_c", TEXT_ID)]

    response = api.client.post(
        f"/api/v1/mappings/source_c/{TEXT_ID}/league",
        json={
            "region_id": "country:TR",
            "display_name": "Kadınlar Ligi",
            "kind": "league",
            "gender": "women",
            "expected_updated_at": item["updated_at"],
        },
        headers=api.headers(),
    )

    assert response.status_code == 422, response.text
    assert (Source.SOURCE_C, TEXT_ID) not in api.repo.build_verified_index()


def test_approving_a_non_pending_mapping_is_rejected_with_422(api: Fixture) -> None:
    response = api.client.post(
        "/api/v1/mappings/source_c/42/approve",
        json={"expected_updated_at": NOW.isoformat()},
        headers=api.headers(),
    )

    assert response.status_code == 422, response.text


def test_removing_an_automatic_mapping_is_rejected_with_422(api: Fixture) -> None:
    response = api.client.delete(
        "/api/v1/mappings/source_c/42",
        params={"expected_updated_at": NOW.isoformat()},
        headers=api.headers(),
    )

    assert response.status_code == 422, response.text
    assert (Source.SOURCE_C, "42") in api.repo.build_verified_index()


def test_unknown_source_is_rejected(api: Fixture) -> None:
    response = api.client.post(
        "/api/v1/mappings/not-a-source/1/approve",
        json={"expected_updated_at": NOW.isoformat()},
        headers=api.headers(),
    )

    assert response.status_code == 422, response.text
