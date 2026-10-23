"""Short lifecycle alerts. Delivered as replies to the card, so no context is repeated."""

from ..models import CardState
from .format import RenderedMessage, content_fingerprint, format_bps, format_points_signed


class AlertRenderer:
    def render_change(self, state: CardState, previous_anchor_bps: int) -> RenderedMessage:
        delta = state.current_profit_bps - previous_anchor_bps
        icon = "📈" if delta > 0 else "🟡"
        text = (
            f"{icon} %{format_bps(previous_anchor_bps)} → %{format_bps(state.current_profit_bps)}"
            f"   ({format_points_signed(delta)} puan)"
        )
        return self._rendered(text)

    def render_revived(self, state: CardState) -> RenderedMessage:
        return self._rendered(f"♻️ Yeniden açıldı · %{format_bps(state.current_profit_bps)}")

    def render_closed(self, state: CardState) -> RenderedMessage:
        end = state.closed_at or state.last_seen_at
        minutes = int((end - state.first_seen_at).total_seconds() // 60)
        text = (
            f"🔴 Kapandı · {minutes} dk aktif · "
            f"zirve %{format_bps(state.peak_profit_bps)} · "
            f"son %{format_bps(state.current_profit_bps)}"
        )
        return self._rendered(text)

    @staticmethod
    def _rendered(text: str) -> RenderedMessage:
        return RenderedMessage(text=text, fingerprint=content_fingerprint(text))
