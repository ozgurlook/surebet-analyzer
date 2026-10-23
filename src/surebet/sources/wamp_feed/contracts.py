import re

CALL_PROCEDURES = (
    "/sports#configureFonts",
    "/sports#getSessionInfo",
    "/sports#initialDump",
    "/sports#marketGroupsOverview",
)
REGISTRATION_SUFFIXES = (
    "custom-events",
    "custom-sports",
    "disciplines/BOTH/BOTH",
    "live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
    "next-locations-by-popularity-with-live/1/180",
    "next-sports-by-popularity-with-live/180",
    "time-ranges-discipline/1/180",
)
SUBSCRIBE_TOPICS = ("/registrationDismissed",)


def register_procedure(operator_id: int, suffix: str) -> str:
    """Build one observed public registration URI from safe nested segments.

    The WAMP feed uses a few fixed nested paths with comma-separated numeric IDs.
    The caller supplies only collector-owned constants; validation still rejects
    empty segments, traversal, separators, and characters outside that contract.
    """

    if not isinstance(operator_id, int) or isinstance(operator_id, bool) or operator_id <= 0:
        raise ValueError("operator_id must be positive")
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("suffix must be a safe public path")
    segments = suffix.split("/")
    if any(
        re.fullmatch(r"[A-Za-z0-9#_-]+(?:,[A-Za-z0-9#_-]+)*", segment) is None
        for segment in segments
    ):
        raise ValueError("suffix must be a safe public path")
    return f"/sports/{operator_id}/tr/{suffix}"


def tournament_registration_suffix(tournament_id: str, *, nr_of_markets: int = 4) -> str:
    """Build registration suffix for tournament aggregator topic."""
    if not isinstance(tournament_id, str) or not tournament_id:
        raise ValueError("tournament_id must be non-empty")
    if re.fullmatch(r"[A-Za-z0-9_-]+", tournament_id) is None:
        raise ValueError("tournament_id must contain only safe alphanumeric characters")
    if nr_of_markets <= 0:
        raise ValueError("nr_of_markets must be positive")
    return f"tournament-aggregator-main/{tournament_id}/default-event-info/BOTH/{nr_of_markets}"


def tournament_dump_topic(
    operator_id: int,
    tournament_id: str,
    *,
    language: str = "tr",
    nr_of_markets: int = 4,
) -> str:
    """Build public WAMP initialDump topic for a specific tournament."""
    if not isinstance(operator_id, int) or isinstance(operator_id, bool) or operator_id <= 0:
        raise ValueError("operator_id must be positive")
    suffix = tournament_registration_suffix(tournament_id, nr_of_markets=nr_of_markets)
    return f"/sports/{operator_id}/{language}/{suffix}"
