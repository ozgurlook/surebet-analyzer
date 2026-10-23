"""Exact Decimal display math and integer-kuruş boundary helpers."""

from decimal import ROUND_DOWN, Decimal
from fractions import Fraction

CENT = Decimal("0.01")


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def validate_odds(value: object) -> Decimal:
    """Validate the shared Decimal odds contract and return the original value."""
    odds = _decimal(value, "odds")
    if odds <= 1:
        raise ValueError("odds must be strictly greater than 1")
    exponent = odds.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -4:
        raise ValueError("odds may have at most four fractional digits")
    return odds


def decimal_to_cents(value: Decimal) -> int:
    """Convert a Decimal amount to kuruş, conservatively rounding down."""
    amount = _decimal(value, "value")
    if amount < 0:
        raise ValueError("value must be nonnegative")
    return int((amount.quantize(CENT, rounding=ROUND_DOWN) * 100).to_integral_exact())


def cents_to_decimal(value: int) -> Decimal:
    """Convert an integer kuruş amount to a two-place Decimal."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("value must be an integer")
    return (Decimal(value) / 100).quantize(CENT)


def payout_cents(stake_cents: int, odds: Decimal) -> int:
    """Return a stake's payout in kuruş, rounding down to the kuruş."""
    if isinstance(stake_cents, bool) or not isinstance(stake_cents, int):
        raise TypeError("stake_cents must be an integer")
    if stake_cents < 0:
        raise ValueError("stake_cents must be nonnegative")
    value = validate_odds(odds)
    payout = Fraction(stake_cents) * Fraction(value)
    return payout.numerator // payout.denominator


def odds_scaled(odds: Decimal, scale: int = 10_000) -> int:
    """Scale validated Decimal odds without introducing binary-float error."""
    value = validate_odds(odds)
    if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
        raise ValueError("scale must be a positive integer")
    scaled = Fraction(value) * scale
    if scaled.denominator != 1:
        raise ValueError("odds cannot be represented exactly at the requested scale")
    return scaled.numerator
