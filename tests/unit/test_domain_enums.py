from surebet.domain.enums import _OPERATOR_GROUPS, OperatorGroup, Source, operator_group


def test_every_source_declares_its_settling_operator() -> None:
    """A source with no declared operator raises KeyError deep inside opportunity
    calculation, which surfaces as a 500 on /opportunities and on the taxonomy
    counts. mypy cannot catch a missing dict entry, so pin it here."""

    assert set(_OPERATOR_GROUPS) == set(Source)
    for source in Source:
        assert operator_group(source) is not None


def test_sport_and_basketball_market_types() -> None:
    from surebet.domain.enums import MarketType, Sport

    assert Sport.FOOTBALL == "football"
    assert Sport.BASKETBALL == "basketball"
    assert MarketType.BASKETBALL_MONEYLINE == "BASKETBALL_ML"
    assert MarketType.BASKETBALL_ASIAN_HANDICAP == "BASKETBALL_AH"


def test_source_f_is_independent_operator_group() -> None:
    from surebet.domain.enums import ACTIVE_COLLECTION_SOURCES

    assert Source.SOURCE_F.value == "source_f"
    assert operator_group(Source.SOURCE_F) is OperatorGroup.SOURCE_F
    assert Source.SOURCE_F in ACTIVE_COLLECTION_SOURCES
