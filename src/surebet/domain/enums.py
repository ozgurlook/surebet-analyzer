from enum import StrEnum


class Source(StrEnum):
    SOURCE_A = "source_a"
    SOURCE_B = "source_b"
    SOURCE_C = "source_c"
    SOURCE_D = "source_d"
    SOURCE_E = "source_e"
    SOURCE_F = "source_f"


# Sources collected by default. Source C mirrors Source B's prices, so it stays off.
ACTIVE_COLLECTION_SOURCES: tuple[Source, ...] = (
    Source.SOURCE_A,
    Source.SOURCE_B,
    Source.SOURCE_D,
    Source.SOURCE_E,
    Source.SOURCE_F,
)


class OperatorGroup(StrEnum):
    """Sources that settle a bet independently of one another.

    Source C and Source B are two brands on one shared WAMP backend and publish the same
    prices, so a bet spread across them is one bet with one counterparty, not a hedge.
    Source E runs on the same WAMP feed protocol but is a separate operator with its
    own prices, so it settles independently and gets its own group.
    """

    SOURCE_B_C = "source_b_c"
    SOURCE_A = "source_a"
    SOURCE_D = "source_d"
    SOURCE_E = "source_e"
    SOURCE_F = "source_f"


_OPERATOR_GROUPS = {
    Source.SOURCE_C: OperatorGroup.SOURCE_B_C,
    Source.SOURCE_B: OperatorGroup.SOURCE_B_C,
    Source.SOURCE_A: OperatorGroup.SOURCE_A,
    Source.SOURCE_D: OperatorGroup.SOURCE_D,
    Source.SOURCE_E: OperatorGroup.SOURCE_E,
    Source.SOURCE_F: OperatorGroup.SOURCE_F,
}


def operator_group(source: Source) -> OperatorGroup:
    """Return the settling operator behind one source brand."""

    return _OPERATOR_GROUPS[source]


class Outcome(StrEnum):
    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"


class TwoWayOutcome(StrEnum):
    OVER = "OVER"
    UNDER = "UNDER"


class Sport(StrEnum):
    FOOTBALL = "football"
    BASKETBALL = "basketball"


class MarketType(StrEnum):
    FULL_TIME_RESULT = "1X2"
    TOTAL_GOALS_25 = "OU25"
    BASKETBALL_MONEYLINE = "BASKETBALL_ML"
    BASKETBALL_ASIAN_HANDICAP = "BASKETBALL_AH"


class QuoteStatus(StrEnum):
    OPEN = "open"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class OpportunityClass(StrEnum):
    STANDARD = "standard_arbitrage"
    BONUS_QUALIFIED = "bonus_qualified"
    BONUS_CONDITIONAL = "bonus_conditional"
    NOT_PROFITABLE = "not_profitable"
