from datetime import UTC, datetime, timedelta

import pytest

from surebet.notifications.render.format import (
    escape,
    escape_attr,
    format_age,
    format_bps,
    format_countdown,
    format_kickoff,
    format_lira,
    format_points_signed,
)

# 2026-09-04 09:00 UTC == 12:00 Europe/Istanbul
NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cents", "signed", "expected"),
    # Spec section 4 renders every amount with two decimals; the plan's Task 2
    # table locked the truncated form, and the spec is the binding authority.
    [
        (100_000, False, "1.000,00 TL"),
        (123_456, False, "1.234,56 TL"),
        (54_000, False, "540,00 TL"),
        (2_400, True, "+24,00 TL"),
        (-2_450, True, "−24,50 TL"),
        (0, True, "0,00 TL"),
    ],
)
def test_format_lira(cents: int, signed: bool, expected: str) -> None:
    assert format_lira(cents, signed=signed) == expected


def test_negative_amounts_use_the_same_minus_glyph_as_point_deltas() -> None:
    """M3: one message must not show two different minus signs."""
    assert format_lira(-2_450)[0] == format_points_signed(-25)[0] == "−"
    assert "-" not in format_lira(-2_450)


def test_html_attribute_escaping_neutralizes_quotes() -> None:
    assert escape_attr('https://x/a"b') == "https://x/a&quot;b"
    assert escape('a"b') == 'a"b'  # body text escaping deliberately leaves quotes


def test_format_bps_uses_turkish_decimal_comma() -> None:
    assert format_bps(240) == "2,40"


def test_format_points_signed_renders_sign_and_unit_free_number() -> None:
    assert format_points_signed(-25) == "−0,25"
    assert format_points_signed(55) == "+0,55"


@pytest.mark.parametrize(
    ("kickoff", "expected"),
    [
        (datetime(2026, 9, 4, 18, 45, tzinfo=UTC), "Bugün 21:45"),
        (datetime(2026, 9, 5, 18, 45, tzinfo=UTC), "Yarın 21:45"),
        (datetime(2026, 9, 6, 18, 45, tzinfo=UTC), "6 Eyl 21:45"),
    ],
)
def test_format_kickoff(kickoff: datetime, expected: str) -> None:
    assert format_kickoff(kickoff, NOW) == expected


@pytest.mark.parametrize(
    ("minutes_ahead", "expected"),
    [
        (120, None),
        (61, None),
        (59, "~55 dk kaldı"),
        (35, "~35 dk kaldı"),
        (34, "~30 dk kaldı"),
        (4, "5 dk'dan az kaldı"),
        (0, None),
        (-10, None),
    ],
)
def test_format_countdown_buckets_to_five_minutes(minutes_ahead: int, expected: str | None) -> None:
    kickoff = NOW.replace(minute=0) + timedelta(minutes=minutes_ahead)
    assert format_countdown(kickoff, NOW) == expected


def test_format_age_switches_to_minutes_above_one_minute() -> None:
    assert format_age(12) == "12 sn"
    assert format_age(240) == "4 dk"


def test_escape_neutralises_html_but_keeps_quotes() -> None:
    assert escape('Ajax & "Feyenoord" <b>') == 'Ajax &amp; "Feyenoord" &lt;b&gt;'
