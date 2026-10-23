"""Turkish-locale formatting helpers shared by every rendered Telegram message."""

import hashlib
import html
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

_ISTANBUL = ZoneInfo("Europe/Istanbul")


class RenderedMessage(BaseModel):
    """Return type shared by the card and alert renderers."""

    model_config = ConfigDict(frozen=True)

    text: str
    fingerprint: str
    reply_markup: dict[str, Any] | None = None


_MONTHS = {
    1: "Oca",
    2: "Şub",
    3: "Mar",
    4: "Nis",
    5: "May",
    6: "Haz",
    7: "Tem",
    8: "Ağu",
    9: "Eyl",
    10: "Eki",
    11: "Kas",
    12: "Ara",
}

_MINUS = "−"  # U+2212, visually balanced against "+" in Telegram's font


def format_lira(cents: int, *, signed: bool = False) -> str:
    """Always two decimals (spec section 4), so the amount column stays aligned."""
    prefix = "+" if signed and cents > 0 else ""
    sign = _MINUS if cents < 0 else prefix
    liras, kurus = divmod(abs(cents), 100)
    grouped = f"{liras:,}".replace(",", ".")
    return f"{sign}{grouped},{kurus:02d} TL"


def format_percent(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def format_bps(bps: int) -> str:
    return format_percent(bps / 100)


def format_points_signed(bps: int) -> str:
    sign = "+" if bps >= 0 else _MINUS
    return f"{sign}{format_percent(abs(bps) / 100)}"


def format_clock(value: datetime) -> str:
    return value.astimezone(_ISTANBUL).strftime("%H:%M")


def format_kickoff(kickoff: datetime, now: datetime) -> str:
    local = kickoff.astimezone(_ISTANBUL)
    days_ahead = (local.date() - now.astimezone(_ISTANBUL).date()).days
    if days_ahead == 0:
        prefix = "Bugün"
    elif days_ahead == 1:
        prefix = "Yarın"
    else:
        prefix = f"{local.day} {_MONTHS[local.month]}"
    return f"{prefix} {local:%H:%M}"


def format_countdown(kickoff: datetime, now: datetime) -> str | None:
    """Coarse countdown, only inside the final hour.

    Bucketed to five minutes on purpose: a per-minute countdown would change the
    rendered text every collection cycle and force an edit each time.
    """
    remaining_minutes = (kickoff - now).total_seconds() / 60
    if remaining_minutes <= 0 or remaining_minutes >= 60:
        return None
    if remaining_minutes < 5:
        return "5 dk'dan az kaldı"
    return f"~{int(remaining_minutes // 5) * 5} dk kaldı"


def format_age(seconds: int) -> str:
    return f"{seconds} sn" if seconds < 60 else f"{seconds // 60} dk"


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def escape(text: str) -> str:
    """Escape body text. Never use this inside an HTML attribute -- see `escape_attr`."""
    return html.escape(text, quote=False)


def escape_attr(text: str) -> str:
    """Escape a value going inside a quoted HTML attribute.

    Feed-supplied text is untrusted; a value containing a double quote would
    otherwise close the attribute early and let the feed inject markup into a
    `parse_mode: HTML` message.
    """
    return html.escape(text, quote=True)
