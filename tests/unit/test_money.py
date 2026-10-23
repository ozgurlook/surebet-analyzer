from decimal import Decimal

import pytest

from surebet.money import cents_to_decimal, decimal_to_cents, payout_cents


def test_decimal_to_cents_is_exact() -> None:
    assert decimal_to_cents(Decimal("123.45")) == 12_345


def test_payout_cents_rounds_down() -> None:
    assert payout_cents(1_001, Decimal("1.3333")) == 1_334


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity"), 1.2])
def test_decimal_to_cents_rejects_invalid_money(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        decimal_to_cents(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("stake", "odds"),
    [(-1, Decimal("2.0")), (100, Decimal(1)), (100, Decimal("NaN")), (100, Decimal("2.12345"))],
)
def test_payout_cents_rejects_invalid_inputs(stake: int, odds: Decimal) -> None:
    with pytest.raises(ValueError):
        payout_cents(stake, odds)


def test_cents_to_decimal_allows_signed_profit_but_rejects_bool() -> None:
    assert cents_to_decimal(-125) == Decimal("-1.25")
    with pytest.raises(TypeError):
        cents_to_decimal(True)  # type: ignore[arg-type]
