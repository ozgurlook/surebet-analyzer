from __future__ import annotations

from surebet.sources.wamp_feed.state import WampFeedState


def test_full_records_are_available_in_snapshot_and_are_copy_safe() -> None:
    state = WampFeedState()
    nested = {"name": "fixture", "tags": ["safe"]}

    state.apply_full([{"entityType": "match", "id": 7, "name": "A", "nested": nested}])
    nested["tags"].append("changed")

    first = state.snapshot()
    assert first == (
        (
            {
                "entityType": "match",
                "id": 7,
                "name": "A",
                "nested": {"name": "fixture", "tags": ["safe"]},
            }
        ),
    )
    first[0]["nested"]["tags"].append("caller")  # type: ignore[index]
    assert state.snapshot()[0]["nested"]["tags"] == ["safe"]  # type: ignore[index]


def test_delta_update_merges_changed_properties_and_delete_removes_record() -> None:
    state = WampFeedState()
    state.apply_full([{"entityType": "match", "id": "7", "name": "A", "status": "open"}])

    state.apply_delta(
        [
            {
                "entityType": "match",
                "id": 7,
                "changeType": "UPDATE",
                "changedProperties": {"status": "closed"},
            }
        ]
    )
    assert state.snapshot() == (
        ({"entityType": "match", "id": "7", "name": "A", "status": "closed"}),
    )

    state.apply_delta([{"entityType": "match", "id": 7, "changeType": "deleted"}])
    assert state.snapshot() == ()


def test_delta_create_and_entity_type_namespace_are_supported() -> None:
    state = WampFeedState()
    state.apply_delta(
        [
            {"entityType": "match", "id": 1, "changeType": "create", "name": "match"},
            {"entityType": "market", "id": 1, "changeType": "created", "name": "market"},
        ]
    )

    assert {record["name"] for record in state.snapshot()} == {"match", "market"}


def test_malformed_records_and_unknown_changes_are_rejected_without_raising() -> None:
    state = WampFeedState()
    state.apply_full(
        [
            {"entityType": "match", "id": 1, "name": "kept"},
            {"entityType": "match", "id": None},
            {"entityType": "", "id": 2},
            {"id": 3},
        ]
    )
    state.apply_delta(
        [
            {"entityType": "match", "id": 1, "changeType": "mystery", "changedProperties": {}},
            {"entityType": "match", "id": 1, "changeType": "update", "changedProperties": []},
            {"entityType": "match", "id": None, "changeType": "delete"},
            "not a record",  # type: ignore[list-item]
        ]
    )

    assert state.snapshot()[0]["name"] == "kept"
    assert state.rejected_count == 7


def test_delta_cannot_change_identity_fields() -> None:
    state = WampFeedState()
    state.apply_full([{"entityType": "match", "id": 1, "name": "kept"}])

    state.apply_delta(
        [
            {
                "entityType": "match",
                "id": 1,
                "changeType": "update",
                "changedProperties": {"id": 2, "entityType": "market", "name": "ignored"},
            }
        ]
    )

    assert state.snapshot() == (({"entityType": "match", "id": 1, "name": "kept"}),)
    assert state.rejected_count == 1


def test_full_records_accept_current_uppercase_type_field() -> None:
    state = WampFeedState()

    state.apply_full([{"_type": "MATCH", "id": "fixture", "name": "current"}])

    assert state.snapshot() == (({"_type": "MATCH", "id": "fixture", "name": "current"}),)
