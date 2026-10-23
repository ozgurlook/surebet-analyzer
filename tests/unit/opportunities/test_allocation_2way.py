from decimal import Decimal

import pytest

from surebet.domain.enums import Source, TwoWayOutcome
from surebet.opportunities.allocation_2way import optimal_stakes_2way


def test_pure_2way_equal_odds_allocates_exact_halves() -> None:
    odds = {TwoWayOutcome.OVER: Decimal("2.10"), TwoWayOutcome.UNDER: Decimal("2.10")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_E, TwoWayOutcome.UNDER: Source.SOURCE_A}
    budget = 100_000  # 1000 TL

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert stakes[TwoWayOutcome.OVER] == 50_000
    assert stakes[TwoWayOutcome.UNDER] == 50_000
    assert sum(stakes.values()) == budget


def test_double_sided_cashback_source_c_and_source_b_positive_on_even_odds() -> None:
    # 1.95 vs 1.95 with 10% KB on both sides produces a +2.50% worst-case difference
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("1.95")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_C, TwoWayOutcome.UNDER: Source.SOURCE_B}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert stakes[TwoWayOutcome.OVER] == 50_000
    assert stakes[TwoWayOutcome.UNDER] == 50_000
    assert sum(stakes.values()) == budget

    # Over wins:
    gross_over = int(stakes[TwoWayOutcome.OVER] * Decimal("1.95"))
    cb_source_b = (stakes[TwoWayOutcome.UNDER] * 10) // 100
    return_over = gross_over + cb_source_b
    assert return_over == 97_500 + 5_000
    assert return_over - budget == 2_500  # +25 TL (+2.50%)

    # Under wins:
    gross_under = int(stakes[TwoWayOutcome.UNDER] * Decimal("1.95"))
    cb_source_c = (stakes[TwoWayOutcome.OVER] * 10) // 100
    return_under = gross_under + cb_source_c
    assert return_under == 97_500 + 5_000
    assert return_under - budget == 2_500  # +25 TL (+2.50%)


def test_single_sided_cashback_source_c_and_source_a() -> None:
    # Source C Over @ 1.95 (10% KB), Source A Under @ 2.05 (no KB)
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("2.05")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_C, TwoWayOutcome.UNDER: Source.SOURCE_A}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert sum(stakes.values()) == budget
    # S_source_c * (1.95 - 0.10) approx= S_source_a * 2.05
    # S_source_c * 1.85 approx= S_source_a * 2.05 -> S_source_c > S_source_a
    assert stakes[TwoWayOutcome.OVER] > stakes[TwoWayOutcome.UNDER]

    # Scenario Over wins:
    payout_over = int(stakes[TwoWayOutcome.OVER] * Decimal("1.95"))
    # Source A has no KB, so 0
    return_over = payout_over

    # Scenario Under wins:
    payout_under = int(stakes[TwoWayOutcome.UNDER] * Decimal("2.05"))
    # Source C lost, so 10% KB
    cb_source_c = (stakes[TwoWayOutcome.OVER] * 10) // 100
    return_under = payout_under + cb_source_c

    # Returns should be virtually equal (within 1-2 kuruş due to integer rounding)
    assert abs(return_over - return_under) <= 5


def test_same_source_cashback_source_c_and_source_c() -> None:
    # User specifically requested source_c/source_c and source_b/source_b combinations
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("1.95")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_C, TwoWayOutcome.UNDER: Source.SOURCE_C}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert stakes[TwoWayOutcome.OVER] == 50_000
    assert stakes[TwoWayOutcome.UNDER] == 50_000
    assert sum(stakes.values()) == budget


def test_allocation_validation() -> None:
    with pytest.raises(ValueError, match="exactly 2 outcomes"):
        optimal_stakes_2way(
            {TwoWayOutcome.OVER: Decimal("2.0")},
            {TwoWayOutcome.OVER: Source.SOURCE_C},
            100_000,
        )

    with pytest.raises(ValueError, match="at least one kuruş per outcome"):
        optimal_stakes_2way(
            {TwoWayOutcome.OVER: Decimal("2.0"), TwoWayOutcome.UNDER: Decimal("2.0")},
            {TwoWayOutcome.OVER: Source.SOURCE_C, TwoWayOutcome.UNDER: Source.SOURCE_B},
            1,  # less than 2 kuruş
        )


def test_source_f_receives_10_percent_cashback() -> None:
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("2.05")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_F, TwoWayOutcome.UNDER: Source.SOURCE_A}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert sum(stakes.values()) == budget
    assert stakes[TwoWayOutcome.OVER] > stakes[TwoWayOutcome.UNDER]

    payout_over = int(stakes[TwoWayOutcome.OVER] * Decimal("1.95"))
    payout_under = int(stakes[TwoWayOutcome.UNDER] * Decimal("2.05"))
    cb_source_f = (stakes[TwoWayOutcome.OVER] * 10) // 100
    return_over = payout_over
    return_under = payout_under + cb_source_f
    assert abs(return_over - return_under) <= 5


def test_double_sided_cashback_source_f_and_source_b() -> None:
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("1.95")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_F, TwoWayOutcome.UNDER: Source.SOURCE_B}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert stakes[TwoWayOutcome.OVER] == 50_000
    assert stakes[TwoWayOutcome.UNDER] == 50_000
    assert sum(stakes.values()) == budget


def test_source_e_has_zero_cashback() -> None:
    odds = {TwoWayOutcome.OVER: Decimal("1.95"), TwoWayOutcome.UNDER: Decimal("2.05")}
    sources = {TwoWayOutcome.OVER: Source.SOURCE_E, TwoWayOutcome.UNDER: Source.SOURCE_A}
    budget = 100_000

    stakes = optimal_stakes_2way(odds, sources, budget)

    assert sum(stakes.values()) == budget
    payout_over = int(stakes[TwoWayOutcome.OVER] * Decimal("1.95"))
    payout_under = int(stakes[TwoWayOutcome.UNDER] * Decimal("2.05"))
    assert abs(payout_over - payout_under) <= 5
