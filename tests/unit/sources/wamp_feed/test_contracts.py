import pytest

from surebet.sources.wamp_feed.contracts import (
    CALL_PROCEDURES,
    REGISTRATION_SUFFIXES,
    SUBSCRIBE_TOPICS,
    register_procedure,
    tournament_dump_topic,
    tournament_registration_suffix,
)


def test_bootstrap_contract_is_exact():
    assert CALL_PROCEDURES == (
        "/sports#configureFonts",
        "/sports#getSessionInfo",
        "/sports#initialDump",
        "/sports#marketGroupsOverview",
    )
    assert SUBSCRIBE_TOPICS == ("/registrationDismissed",)
    assert REGISTRATION_SUFFIXES == (
        "custom-events",
        "custom-sports",
        "disciplines/BOTH/BOTH",
        "live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
        "next-locations-by-popularity-with-live/1/180",
        "next-sports-by-popularity-with-live/180",
        "time-ranges-discipline/1/180",
    )


def test_register_procedure_builds_operator_path():
    assert register_procedure(1001, "events") == "/sports/1001/tr/events"


def test_register_procedure_accepts_the_observed_nested_public_paths():
    assert register_procedure(1001, "disciplines/BOTH/BOTH") == (
        "/sports/1001/tr/disciplines/BOTH/BOTH"
    )
    assert register_procedure(
        1002,
        "live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
    ) == (
        "/sports/1002/tr/live-matches-aggregator-groups-overview/1/all-locations/"
        "default-event-info/10/2258,2259"
    )


@pytest.mark.parametrize("suffix", ["../events", "/events", "events/../x", ""])
def test_register_procedure_rejects_path_traversal(suffix):
    with pytest.raises(ValueError):
        register_procedure(1001, suffix)


@pytest.mark.parametrize(
    "suffix", ["events//next", "events\\next", "events/with space", "events/?"]
)
def test_register_procedure_rejects_unsafe_nested_paths(suffix):
    with pytest.raises(ValueError):
        register_procedure(1001, suffix)


def test_tournament_contract_helpers():
    op_id = 1002
    t_id = "910000000000000109"
    suffix = tournament_registration_suffix(t_id)
    assert suffix == f"tournament-aggregator-main/{t_id}/default-event-info/BOTH/4"
    proc = register_procedure(op_id, suffix)
    assert proc == f"/sports/{op_id}/tr/{suffix}"

    topic = tournament_dump_topic(op_id, t_id)
    assert (
        topic == f"/sports/{op_id}/tr/tournament-aggregator-main/{t_id}/default-event-info/BOTH/4"
    )
    assert topic == proc

    # Custom language or market count
    assert tournament_dump_topic(op_id, t_id, language="en", nr_of_markets=2) == (
        f"/sports/{op_id}/en/tournament-aggregator-main/{t_id}/default-event-info/BOTH/2"
    )


@pytest.mark.parametrize("invalid_id", ["", "  ", "../escape", "3020 123", "id/test"])
def test_tournament_helpers_reject_invalid_ids(invalid_id):
    with pytest.raises(ValueError):
        tournament_registration_suffix(invalid_id)
    with pytest.raises(ValueError):
        tournament_dump_topic(1002, invalid_id)
