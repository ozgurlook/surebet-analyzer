"""Full opportunity card rendering."""

import hashlib
from datetime import datetime
from typing import Any

from surebet.domain.enums import MarketType, Outcome, Source, Sport, TwoWayOutcome
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection

from ..models import CardState, LifecycleStatus
from .format import (
    RenderedMessage,
    escape,
    format_age,
    format_bps,
    format_clock,
    format_countdown,
    format_kickoff,
    format_lira,
    format_percent,
    format_points_signed,
)

_STATUS_LABELS: dict[LifecycleStatus, tuple[str, str]] = {
    LifecycleStatus.NEW: ("🟢", "YENİ FIRSAT"),
    LifecycleStatus.ACTIVE: ("🟢", "Aktif"),
    LifecycleStatus.WATCH: ("🟠", "Eşiğe yakın"),
    LifecycleStatus.STALE: ("⚪", "Veri bekleniyor"),
    LifecycleStatus.CLOSED: ("🔴", "Kapandı"),
}

_OUTCOME_LABELS: dict[Outcome | TwoWayOutcome, str] = {
    Outcome.HOME: "MS1",
    Outcome.DRAW: "X",
    Outcome.AWAY: "MS2",
    TwoWayOutcome.OVER: "2.5 Üst",
    TwoWayOutcome.UNDER: "2.5 Alt",
}

_MARKET_LABELS: dict[MarketType, str] = {
    MarketType.FULL_TIME_RESULT: "1X2",
    MarketType.TOTAL_GOALS_25: "2.5 Alt/Üst",
    MarketType.BASKETBALL_MONEYLINE: "Maç Kazananı",
    MarketType.BASKETBALL_ASIAN_HANDICAP: "Asya Handikap",
}


_SOURCE_LABELS: dict[Source, str] = {
    Source.SOURCE_A: "KAYNAK A",
    Source.SOURCE_B: "KAYNAK B",
    Source.SOURCE_C: "KAYNAK C",
    Source.SOURCE_D: "KAYNAK D",
    Source.SOURCE_E: "KAYNAK E",
    Source.SOURCE_F: "KAYNAK F",
}

# Source D is a second sportsbook backend of the Source A operator.
_SOURCE_A_SOURCES = (Source.SOURCE_A, Source.SOURCE_D)


def _outcome_label(outcome: object, sport: Sport = Sport.FOOTBALL) -> str:
    if sport is Sport.BASKETBALL:
        if outcome == Outcome.HOME:
            return "1 (Ev)"
        if outcome == Outcome.AWAY:
            return "2 (Dep)"
    if isinstance(outcome, (Outcome, TwoWayOutcome)):
        return _OUTCOME_LABELS.get(outcome, str(getattr(outcome, "value", outcome)))
    return str(getattr(outcome, "value", outcome))


class CardRenderer:
    """Renders the live card and its inline keyboard."""

    def render(
        self,
        state: CardState,
        now: datetime,
        *,
        is_muted: bool = False,
        is_fav: bool = False,
    ) -> RenderedMessage:
        selected = state.evaluation.selected
        lines: list[str] = []

        if state.status is LifecycleStatus.STALE:
            lines.append(self._stale_banner(state, now))

        if selected.sport is Sport.BASKETBALL:
            lines.append("🏀 [EUROLEAGUE SUREBET]")

        icon, label = _STATUS_LABELS[state.status]
        if selected.market_type is MarketType.BASKETBALL_ASIAN_HANDICAP and selected.market_label:
            market = selected.market_label
        else:
            market = _MARKET_LABELS.get(selected.market_type, selected.market_label)
        lines.append(f"{icon} {label} · %{format_bps(state.current_profit_bps)} · {market}")
        lines.append("")
        lines.append(
            f"{escape(selected.country_name or '')} / "
            f"{escape(selected.league_name or selected.competition)}"
        )
        lines.append(f"{escape(selected.home)} - {escape(selected.away)}")

        kickoff_line = f"Başlangıç: {format_kickoff(selected.kickoff, now)}"
        countdown = format_countdown(selected.kickoff, now)
        lines.append(f"{kickoff_line} ({countdown})" if countdown else kickoff_line)
        lines.append("")

        for outcome, selection in selected.selections.items():
            lines.append(self._selection_line(outcome, selection, selected))

        lines.append("")
        lines.append(f"Bütçe: {format_lira(selected.budget_cents)}")
        lines.append(
            f"Kâr: {format_lira(selected.min_adjusted_profit_cents, signed=True)} "
            f"(%{format_bps(state.current_profit_bps)})"
        )
        if selected.min_standard_profit_cents != selected.min_adjusted_profit_cents:
            lines.append(
                f"       cashbacksiz {format_lira(selected.min_standard_profit_cents, signed=True)} "
                f"(%{format_percent(selected.min_standard_profit_percent)})"
            )
            if selected.sport is Sport.BASKETBALL:
                cashback_sources = [
                    _SOURCE_LABELS.get(s.source, s.source.value.upper())
                    for s in selected.selections.values()
                    if s.cashback_eligible
                ]
                if cashback_sources:
                    names = " / ".join(dict.fromkeys(cashback_sources))
                    lines.append(f"       ℹ️ {names} kaybederse %10 Kayıp Bonusu dahildir")
                else:
                    lines.append("       ℹ️ Kaynak B kaybederse %10 Kayıp Bonusu dahildir")

        lines.append(self._source_a_usage(selected))
        lines.append("")
        lines.append(
            f"İlk %{format_bps(state.first_profit_bps)} · "
            f"Zirve %{format_bps(state.peak_profit_bps)} · "
            f"Güncel %{format_bps(state.current_profit_bps)}"
        )
        lines.append(
            f"Oran yaşı {format_age(selected.oldest_quote_age_seconds)} · "
            f"{format_clock(selected.calculated_at)} hesaplandı"
        )

        if state.evaluation.uses_source_a_match_sides:
            lines.extend(self._source_a_comparison(state))

        text = "\n".join(lines)
        reply_markup = self.build_reply_markup(state, is_muted=is_muted, is_fav=is_fav)
        return RenderedMessage(
            text=text,
            fingerprint=self._fingerprint(state),
            reply_markup=reply_markup,
        )

    def build_reply_markup(
        self,
        state: CardState,
        *,
        is_muted: bool = False,
        is_fav: bool = False,
    ) -> dict[str, Any]:
        keyboard_rows: list[list[dict[str, str]]] = []

        # Row 1: Calculator, Hedging, and Refresh Alert
        keyboard_rows.append(
            [
                {
                    "text": "🧮 Dağılım Hesapla",
                    "callback_data": f"calc:{state.card_key}",
                },
                {
                    "text": "🛡️ Kurtar",
                    "callback_data": f"hedge_prompt:{state.card_key}",
                },
                {
                    "text": "🔄 Güncel Oranlar",
                    "callback_data": f"refresh:{state.card_key}",
                },
            ]
        )

        # Row 2: Mute, Favorite, and Help
        mute_btn = (
            {"text": "🔔 Susturmayı Kaldır", "callback_data": f"unmute:{state.card_key}"}
            if is_muted
            else {"text": "🔕 2 Saat Sustur", "callback_data": f"mute:{state.card_key}"}
        )
        fav_btn = (
            {"text": "⭐ Takipte", "callback_data": f"fav:{state.card_key}"}
            if is_fav
            else {"text": "📌 Takibe Al", "callback_data": f"fav:{state.card_key}"}
        )
        keyboard_rows.append(
            [
                mute_btn,
                fav_btn,
                {
                    "text": "💡 Komutlar",
                    "callback_data": f"help:{state.card_key}",
                },
            ]
        )
        return {"inline_keyboard": keyboard_rows}

    @staticmethod
    def _fingerprint(state: CardState) -> str:
        """Coarsened fingerprint for edit detection.

        Avoids micro-edits from negligible bps fluctuations (bucketed to 25 bps)
        or continuously advancing clocks/quote ages while preserving genuine
        market movements and lifecycle status changes.
        """
        selected = state.evaluation.selected
        bucketed_profit_bps = round(state.current_profit_bps / 25) * 25
        bucketed_peak_bps = round(state.peak_profit_bps / 25) * 25
        bucketed_first_bps = round(state.first_profit_bps / 25) * 25

        selections = tuple(
            (
                str(outcome),
                selection.source.value,
                f"{selection.odds:.2f}",
                format_lira(selection.stake_cents),
            )
            for outcome, selection in sorted(
                selected.selections.items(), key=lambda item: str(item[0])
            )
        )

        key_data = (
            state.status.value,
            selected.market_type.value,
            selected.sport.value,
            bucketed_profit_bps,
            bucketed_peak_bps,
            bucketed_first_bps,
            selected.budget_cents,
            selections,
            state.evaluation.uses_source_a_match_sides,
        )
        return hashlib.sha256(repr(key_data).encode("utf-8")).hexdigest()

    # -- pieces ---------------------------------------------------------

    @staticmethod
    def _stale_banner(state: CardState, now: datetime) -> str:
        minutes = max(1, int((now - state.last_seen_at).total_seconds() // 60))
        return f"⚠️ Veri {minutes} dk'dır güncellenmedi — oranlar bayat olabilir"

    def _selection_line(
        self,
        outcome: object,
        selection: StrategySelection,
        selected: CalculatedOpportunity,
    ) -> str:
        label = _outcome_label(outcome, selected.sport)
        source_label = _SOURCE_LABELS[selection.source]
        stake = format_lira(selection.stake_cents)
        # Normalized to two decimals: a raw Decimal renders `1.850`, which in a
        # Turkish-formatted message reads as one thousand eight hundred fifty.
        odds = f"{selection.odds:.2f}"
        kb_badge = (
            " (⚡ %10 KB)"
            if (selection.cashback_eligible and selected.sport is Sport.BASKETBALL)
            else ""
        )
        return f"{label}  {source_label}{kb_badge}  @{odds}  {stake}"

    @staticmethod
    def _source_a_usage(selected: CalculatedOpportunity) -> str:
        used = [
            (outcome, selection.source)
            for outcome, selection in selected.selections.items()
            if selection.source in _SOURCE_A_SOURCES
        ]
        if not used:
            return "Kaynak A kullanımı: Yok"
        names = ", ".join(
            f"{_outcome_label(outcome)} ({_SOURCE_LABELS[source]})" for outcome, source in used
        )
        return f"Kaynak A kullanımı: {names}"

    @staticmethod
    def _source_a_comparison(state: CardState) -> list[str]:
        wide = state.evaluation.selected
        draw_only = state.evaluation.draw_only
        advantage_bps = round(
            (wide.min_adjusted_profit_percent - draw_only.min_adjusted_profit_percent) * 100
        )
        return [
            "",
            (
                f"Kaynak A MS1/MS2 açık: {format_lira(wide.min_adjusted_profit_cents, signed=True)} "
                f"(%{format_percent(wide.min_adjusted_profit_percent)})"
            ),
            (
                f"Kaynak A yalnız X: {format_lira(draw_only.min_adjusted_profit_cents, signed=True)} "
                f"(%{format_percent(draw_only.min_adjusted_profit_percent)})"
            ),
            f"Kaynak A avantajı: {format_points_signed(advantage_bps)} puan",
        ]
