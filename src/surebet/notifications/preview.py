"""Print every Telegram message shape from a fixed fixture, for eyeballing the copy."""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

import typer

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection

from .models import CardState, DualPolicyEvaluation, LifecycleStatus, card_key
from .render.alerts import AlertRenderer
from .render.card import CardRenderer

app = typer.Typer(help="Örnek Telegram bildirim mesajlarını yazdırır.")

_NOW = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 9, 4, 18, 45, tzinfo=UTC)


def _opportunity(profit_bps: int) -> CalculatedOpportunity:
    budget = 1_000_000
    return CalculatedOpportunity.model_construct(
        opportunity_id="event-1:1X2",
        matched_id="event-1",
        market_type=MarketType.FULL_TIME_RESULT,
        market_label="Maç Sonucu 1X2",
        competition="Süper Lig",
        country_id="country:TR",
        country_name="Türkiye",
        country_iso_code="TR",
        league_id="league:1",
        league_name="Süper Lig",
        home="Galatasaray",
        away="Fenerbahçe",
        kickoff=_KICKOFF,
        budget_cents=budget,
        include_source_a_match_sides=False,
        selections=MappingProxyType(
            {
                Outcome.HOME: StrategySelection(
                    outcome=Outcome.HOME,
                    source=Source.SOURCE_A,
                    odds=Decimal("1.85"),
                    stake_cents=540_000,
                    cashback_eligible=False,
                    event_id="12345",
                ),
                Outcome.DRAW: StrategySelection(
                    outcome=Outcome.DRAW,
                    source=Source.SOURCE_C,
                    odds=Decimal("3.60"),
                    stake_cents=280_000,
                    cashback_eligible=True,
                    event_id="12345",
                ),
                Outcome.AWAY: StrategySelection(
                    outcome=Outcome.AWAY,
                    source=Source.SOURCE_B,
                    odds=Decimal("4.20"),
                    stake_cents=180_000,
                    cashback_eligible=True,
                    event_id="12345",
                ),
            }
        ),
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_bps * budget // 10_000,
        min_adjusted_profit_percent=profit_bps / 100,
        min_standard_profit_cents=profit_bps * budget // 20_000,
        min_standard_profit_percent=profit_bps / 200,
        is_positive=True,
        oldest_quote_age_seconds=12,
        calculated_at=_NOW,
        alternatives=(),
    )


def _state(profit_bps: int, status: LifecycleStatus, **overrides: object) -> CardState:
    selected = _opportunity(profit_bps)
    base: dict[str, object] = {
        "user_id": 1,
        "chat_id": 99,
        "card_key": card_key("event-1", MarketType.FULL_TIME_RESULT),
        "matched_id": "event-1",
        "market_type": MarketType.FULL_TIME_RESULT,
        "generation": 1,
        "status": status,
        "message_id": 4242,
        "evaluation": DualPolicyEvaluation(
            matched_id="event-1",
            market_type=MarketType.FULL_TIME_RESULT,
            selected=selected,
            draw_only=selected,
            uses_source_a_match_sides=False,
            relevant_sources=frozenset({Source.SOURCE_A, Source.SOURCE_C, Source.SOURCE_B}),
        ),
        "first_profit_bps": 240,
        "current_profit_bps": profit_bps,
        "peak_profit_bps": 270,
        "anchor_bps": profit_bps,
        "last_alert_at": None,
        "below_since": None,
        "closed_at": None,
        "revival_count": 0,
        "first_seen_at": _NOW - timedelta(minutes=29),
        "last_seen_at": _NOW,
        "last_edit_at": None,
        "rendered_fingerprint": None,
    }
    base.update(overrides)
    return CardState(**base)  # type: ignore[arg-type]


def build_samples() -> list[tuple[str, str]]:
    cards = CardRenderer()
    alerts = AlertRenderer()
    stale = _state(240, LifecycleStatus.STALE, last_seen_at=_NOW - timedelta(minutes=4))
    closed = _state(55, LifecycleStatus.CLOSED, closed_at=_NOW)
    return [
        ("Yeni fırsat kartı", cards.render(_state(240, LifecycleStatus.NEW), _NOW).text),
        ("Eşiğe yakın kart", cards.render(_state(91, LifecycleStatus.WATCH), _NOW).text),
        ("Bayat veri kartı", cards.render(stale, _NOW).text),
        ("Kapanmış kart", cards.render(closed, _NOW).text),
        ("Düşüş uyarısı", alerts.render_change(_state(215, LifecycleStatus.ACTIVE), 240).text),
        ("Yükseliş uyarısı", alerts.render_change(_state(270, LifecycleStatus.ACTIVE), 215).text),
        ("Yeniden açıldı", alerts.render_revived(_state(140, LifecycleStatus.ACTIVE)).text),
        ("Kapandı", alerts.render_closed(closed).text),
    ]


@app.command()
def main() -> None:
    """Her mesaj tipinden bir örnek yazdırır."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for title, text in build_samples():
        typer.echo(f"\n=== {title} " + "=" * (60 - len(title)))
        typer.echo(text)


if __name__ == "__main__":
    app()
