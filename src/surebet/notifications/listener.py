"""Interactive Telegram Bot Listener for real-time bet allocation calculations."""

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from surebet.accounting.models import (
    AccountingTransaction,
    BankrollAccount,
    DailySummary,
    TransactionStatus,
    TransactionType,
    WeeklySummary,
)
from surebet.accounting.service import (
    ISTANBUL as ACCOUNTING_ISTANBUL,
)
from surebet.accounting.service import (
    AccountingService,
    parse_amount_to_cents,
    parse_date_input,
)
from surebet.accounting.storage import AccountingRepository
from surebet.domain.enums import MarketType, Outcome, Source, TwoWayOutcome
from surebet.opportunities.allocation_fixed import (
    AllocationResult,
    calculate_budget_allocation,
    calculate_fixed_stake_allocation,
)
from surebet.opportunities.allocation_hedging import (
    FixedLeg,
    HedgingResult,
    calculate_hedging_allocation,
)
from surebet.opportunities.strategy import CalculatedOpportunity
from surebet.storage.notifications import NotificationRepository

from .models import LifecycleStatus
from .render.card import CardRenderer
from .render.format import format_bps, format_lira
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

_OUTCOME_LABELS: dict[Outcome | TwoWayOutcome, str] = {
    Outcome.HOME: "MS1",
    Outcome.DRAW: "X",
    Outcome.AWAY: "MS2",
    TwoWayOutcome.OVER: "2.5 Üst",
    TwoWayOutcome.UNDER: "2.5 Alt",
}

_SOURCE_NAMES: dict[Source, str] = {
    Source.SOURCE_A: "Kaynak A",
    Source.SOURCE_B: "Kaynak B",
    Source.SOURCE_C: "Kaynak C",
    Source.SOURCE_D: "Kaynak D",
    Source.SOURCE_E: "Kaynak E",
    Source.SOURCE_F: "Kaynak F",
}


def _outcome_label(outcome: object) -> str:
    if isinstance(outcome, (Outcome, TwoWayOutcome)):
        return _OUTCOME_LABELS.get(outcome, str(getattr(outcome, "value", outcome)))
    return str(getattr(outcome, "value", outcome))


@dataclass(frozen=True)
class HelpTopic:
    id: str
    title: str
    summary: str
    syntax: list[str]
    examples: list[str]
    tips: list[str]


HELP_TOPICS: dict[str, HelpTopic] = {
    "stake": HelpTopic(
        id="stake",
        title="🧮 1. Bahis Dağılımı & Bütçe Sabitleme",
        summary=(
            "Fırsat kartındaki oranlara göre bir seçeneğin tutarını sabitleyebilir veya "
            "elinizdeki toplam bütçeyi tüm sonuçlarda en yüksek minimum net farkı verecek şekilde paylaştırabilirsiniz."
        ),
        syntax=[
            "<code>[tutar]</code> ➡️ İlk seçeneğe göre hesaplar (örn: <code>3500</code>)",
            "<code>[seçenek] [tutar]</code> ➡️ İlgili seçeneğe göre hesaplar (örn: <code>ms1 2000</code>)",
            "<code>bütçe [tutar]</code> ➡️ Toplam bütçeyi paylaştırır (örn: <code>bütçe 10000</code>)",
        ],
        examples=[
            "<code>3500</code> ➡️ İlk seçeneğe (MS1) 3.500 TL basıldığında diğerlerini uyarlar.",
            "<code>ms1 2000</code> ➡️ MS1 bahsine 2.000 TL basıldığında X ve MS2 tutarlarını hesaplar.",
            "<code>x 1500</code> ➡️ Beraberliğe 1.500 TL basıldığında diğer oranları dengeler.",
            "<code>ms2 1000</code> ➡️ MS2 bahsine 1.000 TL basıldığında diğer oranları dengeler.",
            "<code>üst 2500</code> veya <code>alt 2500</code> ➡️ 2.5 Alt/Üst için tutar uyarlar.",
            "<code>bütçe 10000</code> ➡️ Toplam 10.000 TL kasanızı tüm oranlara optimize paylaştırır.",
        ],
        tips=[
            "Karta <b>Yanıt (Reply)</b> vererek veya sohbete doğrudan yazabilirsiniz.",
            "Kayıp bonusu (%10 cashback) formüle dahildir; her sonuç için eşit net fark hesaplanır.",
            "Kart altındaki <b>[ 🧮 Dağılım Hesapla ]</b> butonundan hazır paketleri (5.000 TL, 10.000 TL) tek tıkla seçebilirsiniz.",
        ],
    ),
    "hedge": HelpTopic(
        id="hedge",
        title="🛡️ 2. Kurtarma & Oran Düşüşü (Hedge)",
        summary=(
            "Bahis aldığınız büroda oran düştüğünde veya maç hareketlendiğinde, "
            "paranızı açıkta (%100 riskte) bırakmadan karşı bacakları yeni oranlara göre dengeler."
        ),
        syntax=[
            "<code>kurtar [seçenek] [oran] [tutar]</code> ➡️ Tek bacak kurtarma",
            "<code>kurtar [seçenek1] [oran1] [tutar1] [seçenek2] [oran2] [tutar2]</code> ➡️ Çoklu bacak kurtarma",
            "<code>kurtar ... [karşı_seçenek] [yeni_oran]</code> ➡️ Karşı oran güncelleme",
        ],
        examples=[
            "<code>kurtar ms1 3.10 6000</code> ➡️ MS1'e 3.10'dan 6.000 TL basıldı; karşı bacakları dengeler.",
            "<code>kurtar 1 3.10 4000 x 3.30 3500</code> ➡️ Hem 1 hem X oynandıysa kalan MS2 bacağını hesaplar.",
            "<code>kurtar 1 3.10 4000 x 3.30 3500 2 2.50</code> ➡️ Karşı bacağın (MS2) oranı 2.50'ye düştüyse güncel hesaplar.",
            "<code>kurtar ms1 3.10 5000 x 3.20 ms2 2.40</code> ➡️ Tüm karşı oranları revize ederek kurtarır.",
        ],
        tips=[
            "<b>Pozitif Arbitraj Varsa:</b> Kalan bacakları eşit net farka göre hesaplar.",
            "<b>Arbitraj Negatife Döndüyse:</b> Açıkta kalıp tüm paranızı kaybetmek yerine (-6.000 TL), kaybı tüm sonuçlara minimum ve eşit seviyede yayar (-150 TL gibi).",
            "Kartın altındaki <b>[ 🛡️ Kurtar ]</b> butonuna tıklayarak doğrudan örnek komut şablonu alabilirsiniz.",
        ],
    ),
    "fav": HelpTopic(
        id="fav",
        title="⭐ 4. Favoriler & Bildirim Kontrolü",
        summary=(
            "Takip etmek istediğiniz yüksek kârlı veya kritik maçları favoriye alabilir, "
            "oran hareketlerini anlık kontrol edebilir ve istemediğiniz maç bildirimlerini susturabilirsiniz."
        ),
        syntax=[
            "<code>/favoriler</code> veya <code>/fav</code> ➡️ Takip listenizi listeler",
            "<b>[ 📌 Takibe Al ] / [ ⭐ Takip ]</b> ➡️ Fırsatı favorilere ekler veya çıkarır",
            "<b>[ 🔕 Sustur ]</b> ➡️ Maç bildirimlerini 2 saat boyunca susturur",
            "<b>[ 🔄 Kontrol Et ]</b> ➡️ Maçın hâlâ aktif olup olmadığını sorgular",
        ],
        examples=[
            "<code>/favoriler</code> ➡️ Takip ettiğiniz tüm maçları listeler.",
            "<code>/fav</code> ➡️ Favori listesini kısa komutla açar.",
        ],
        tips=[
            "Favori listenizdeki maçlar kapandığında veya başladığında otomatik olarak listeden düşer.",
            "Favoriler sayfasındaki maçların altında tek tıkla hesaplama butonları bulunur.",
        ],
    ),
    "tips": HelpTopic(
        id="tips",
        title="💡 5. Arbitraj & Sistem Mantığı İpuçları",
        summary=(
            "Sistemimizin arkasındaki matematiksel optimizasyon ilkeleri, "
            "büro kuralları ve maksimum kazanç sağlama yöntemleri."
        ),
        syntax=[
            "<code>/yardim [konu]</code> ➡️ İlgili rehbere doğrudan ulaşır",
        ],
        examples=[
            "<code>/yardim kurtar</code> ➡️ Kurtarma & hedge rehberini açar.",
            "<code>/yardim hesapla</code> ➡️ Dağılım ve bütçe rehberini açar.",
            "<code>/yardim fav</code> ➡️ Favoriler rehberini açar.",
        ],
        tips=[
            "<b>%10 Kayıp İadesi (Cashback):</b> Kaynak C ve Kaynak B bürolarında kaybeden kuponlara %10 nakit iade tanımlanır. Sistem oranları buna göre normalize eder.",
            "<b>Kuruş Hassasiyeti:</b> Paranız asla ondalık (float) yuvarlama hatalarına kurban gitmez; kuruş kuruşuna tamsayı optimizasyonu uygulanır.",
        ],
    ),
}


def resolve_help_topic_id(raw: str) -> str | None:
    norm = raw.strip().lower()
    if norm in ("stake", "dagilim", "dağılım", "hesapla", "butce", "bütçe", "hesap"):
        return "stake"
    if norm in ("hedge", "kurtar", "kurtarma", "risk"):
        return "hedge"
    if norm in ("fav", "favori", "favoriler", "takip"):
        return "fav"
    if norm in ("tips", "ipucu", "ipuçları", "mantik", "mantık", "arbitraj"):
        return "tips"
    return None


def render_help_menu() -> tuple[str, dict[str, Any]]:
    text = (
        "🤖 <b>Surebet Arbitraj Asistanı</b>\n"
        "📖 <b>Yardım & Kullanım Rehberi</b>\n\n"
        "Aşağıdaki başlıklardan öğrenmek istediğiniz konuyu seçebilirsiniz:\n\n"
        "• <b>🧮 Dağılım & Bütçe:</b> Tek bacak sabitleme ve kasa dağılımı\n"
        "• <b>🛡️ Kurtarma & Hedge:</b> Oran düşüşü kurtarma ve risk yönetimi\n"
        "• <b>⭐ Favoriler & Takip:</b> Fırsat takibi ve susturma ayarları\n"
        "• <b>💡 Arbitraj & İpuçları:</b> %10 iade (cashback) ve püf noktaları\n\n"
        "<i>💡 İpucu: Dilediğinizde doğrudan <code>/yardim kurtar</code> veya <code>/yardim fav</code> yazabilirsiniz.</i>"
    )
    keyboard = [
        [
            {"text": "🧮 1. Dağılım & Bütçe", "callback_data": "help_topic:stake"},
            {"text": "🛡️ 2. Kurtarma & Hedge", "callback_data": "help_topic:hedge"},
        ],
        [
            {"text": "⭐ 3. Favoriler & Takip", "callback_data": "help_topic:fav"},
            {"text": "💡 4. Arbitraj & İpuçları", "callback_data": "help_topic:tips"},
        ],
    ]
    return text, {"inline_keyboard": keyboard}


def render_help_topic(topic_id: str) -> tuple[str, dict[str, Any]]:
    topic = HELP_TOPICS.get(topic_id)
    if not topic:
        return render_help_menu()

    lines = [
        f"<b>{topic.title}</b>\n",
        f"ℹ️ {topic.summary}\n",
        "📋 <b>Komut Sözdizimi:</b>",
    ]
    for s in topic.syntax:
        lines.append(f"• {s}")

    section_title = "Örnek Hesaplama Komutları" if topic_id == "stake" else "Örnek Kullanımlar"
    lines.append(f"\n📝 <b>{section_title}:</b>")
    for ex in topic.examples:
        lines.append(f"• {ex}")

    lines.append("\n💡 <b>Püf Noktaları:</b>")
    for tip in topic.tips:
        lines.append(f"• {tip}")

    nav_row = [{"text": "⬅️ Ana Yardım Menüsü", "callback_data": "help_menu"}]

    quick_actions: list[dict[str, str]] = []
    if topic_id == "stake":
        quick_actions.append({"text": "🛡️ Kurtarma Rehberi", "callback_data": "help_topic:hedge"})
    elif topic_id == "hedge" or topic_id == "fav":
        quick_actions.append({"text": "🧮 Dağılım Rehberi", "callback_data": "help_topic:stake"})
    elif topic_id == "tips":
        quick_actions.append({"text": "🛡️ Kurtarma Rehberi", "callback_data": "help_topic:hedge"})

    keyboard = [nav_row]
    if quick_actions:
        keyboard.append(quick_actions)

    return "\n".join(lines), {"inline_keyboard": keyboard}


def parse_cents(val_str: str) -> int | None:
    """Parse monetary string to integer kuruş/cents.

    Supports formats like '3500', '3.500', '3500 TL', '3500,50', '3.500,00'.
    Rejects strings with extraneous words or non-monetary tokens.
    """
    raw = val_str.strip()
    if not raw or raw.startswith("-"):
        return None

    # Strip valid currency markers (e.g. TL, TRY, Lira, ₺)
    no_curr = re.sub(r"(?i)\b(?:tl|try|lira|₺)\b|[₺]", "", raw).strip()
    # Reject if any unexpected alphabetic or symbol characters remain
    if re.search(r"[^\d.,\s]", no_curr):
        return None

    cleaned = re.sub(r"[^\d,\.]", "", no_curr)
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = parts[0] + "." + parts[1]
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            cleaned = parts[0] + parts[1]
        elif len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = parts[0] + "." + parts[1]
        else:
            cleaned = cleaned.replace(".", "")

    try:
        dec = Decimal(cleaned)
        if dec <= 0:
            return None
        return round(dec * 100)
    except Exception:  # noqa: BLE001
        return None


class ParsedCalculatorRequest:
    def __init__(
        self,
        amount_cents: int,
        *,
        is_budget: bool = False,
        outcome_raw: str | None = None,
    ) -> None:
        self.amount_cents = amount_cents
        self.is_budget = is_budget
        self.outcome_raw = outcome_raw


_BUDGET_KEYWORDS: set[str] = {"bütçe", "butce", "toplam", "total"}
_FIX_KEYWORDS: set[str] = {"sabit", "sabitle", "sabitleme", "fix", "kilitle", "kitle"}
_OUTCOME_TOKENS: set[str] = {
    "ms1",
    "ms2",
    "x",
    "1",
    "2",
    "0",
    "ev",
    "dep",
    "deplasman",
    "beraberlik",
    "üst",
    "ust",
    "alt",
    "over",
    "under",
}


def parse_calculator_command(text: str) -> ParsedCalculatorRequest | None:
    """Parse text command from user into a structured calculation request."""
    normalized = text.strip().lower()
    if not normalized:
        return None

    if normalized.startswith("/"):
        normalized = normalized[1:].strip()

    # Pre-normalize multi-word outcomes
    normalized = re.sub(r"\bms\s*1\b", "ms1", normalized)
    normalized = re.sub(r"\bms\s*2\b", "ms2", normalized)
    normalized = re.sub(r"\bms\s*(?:x|0)\b", "x", normalized)
    normalized = re.sub(r"\b2[.,]5\s*(?:üst|ust)\b", "üst", normalized)
    normalized = re.sub(r"\b2[.,]5\s*alt\b", "alt", normalized)

    tokens = normalized.split()
    if not tokens:
        return None

    # Check for budget keywords
    is_budget = False
    tokens_no_budget: list[str] = []
    for t in tokens:
        if t in _BUDGET_KEYWORDS:
            is_budget = True
        else:
            tokens_no_budget.append(t)

    if is_budget:
        amount_str = " ".join(tokens_no_budget)
        cents = parse_cents(amount_str)
        if cents:
            return ParsedCalculatorRequest(amount_cents=cents, is_budget=True)
        return None

    # Check for fix (sabit/sabitle) keywords
    tokens_no_fix: list[str] = []
    for t in tokens:
        if t not in _FIX_KEYWORDS:
            tokens_no_fix.append(t)

    # Look for outcome token in tokens_no_fix
    outcome_raw: str | None = None
    tokens_amount: list[str] = []
    for t in tokens_no_fix:
        if outcome_raw is None and t in _OUTCOME_TOKENS:
            outcome_raw = t
        else:
            tokens_amount.append(t)

    if tokens_amount:
        amount_str = " ".join(tokens_amount)
        cents = parse_cents(amount_str)
        if cents:
            return ParsedCalculatorRequest(
                amount_cents=cents,
                is_budget=False,
                outcome_raw=outcome_raw,
            )

    return None


def parse_odds(val_str: str) -> Decimal | None:
    """Parse odds string to Decimal (e.g. '3.10', '3,10', '3.1')."""
    raw = val_str.strip().replace(",", ".")
    try:
        dec = Decimal(raw)
        if dec > Decimal("1.0"):
            return dec
        return None
    except Exception:  # noqa: BLE001
        return None


class FixedLegInput:
    def __init__(
        self,
        outcome_raw: str,
        odds: Decimal,
        stake_cents: int,
    ) -> None:
        self.outcome_raw = outcome_raw
        self.odds = odds
        self.stake_cents = stake_cents


class ParsedHedgingRequest:
    def __init__(
        self,
        fixed_legs: list[FixedLegInput],
        counter_odds_override: dict[str, Decimal] | None = None,
    ) -> None:
        self.fixed_legs = fixed_legs
        self.counter_odds_override = counter_odds_override or {}

    @property
    def fixed_outcome_raw(self) -> str:
        return self.fixed_legs[0].outcome_raw if self.fixed_legs else ""

    @property
    def fixed_odds(self) -> Decimal:
        return self.fixed_legs[0].odds if self.fixed_legs else Decimal("1.0")

    @property
    def fixed_stake_cents(self) -> int:
        return self.fixed_legs[0].stake_cents if self.fixed_legs else 0


def _classify_odds_and_stake(t1: str, t2: str) -> tuple[Decimal, int] | None:
    c1_odds = parse_odds(t1)
    c1_cents = parse_cents(t1)
    c2_odds = parse_odds(t2)
    c2_cents = parse_cents(t2)

    if c1_odds is not None and c2_cents is not None and (c2_cents >= (c1_cents or 0)):
        return c1_odds, c2_cents
    if c2_odds is not None and c1_cents is not None:
        return c2_odds, c1_cents
    return None


def parse_hedging_command(text: str) -> ParsedHedgingRequest | None:
    """Parse hedging/recovery command with single or multiple fixed legs."""
    raw = text.strip()
    if not raw:
        return None

    prefix_match = re.match(r"^/?(?:kurtar|hedge|kurtarma)\b[:\s]*(.*)$", raw, re.IGNORECASE)
    if not prefix_match:
        return None

    rem = prefix_match.group(1).strip()
    if not rem:
        return None

    rem_cleaned = re.sub(r"\b(?:tl|lira)\b", " ", rem, flags=re.IGNORECASE)
    tokens = [t for t in re.split(r"\s+", rem_cleaned) if t]
    if len(tokens) < 2:
        return None

    # Support '6000 ms1 3.10' permutation by rotating the first 3 tokens if amount is first
    if (
        len(tokens) >= 3
        and tokens[0].lower() not in _OUTCOME_TOKENS
        and tokens[1].lower() in _OUTCOME_TOKENS
    ):
        tokens = [tokens[1], tokens[2], tokens[0], *tokens[3:]]

    out_indices = [i for i, t in enumerate(tokens) if t.lower() in _OUTCOME_TOKENS]
    if not out_indices:
        return None

    fixed_legs: list[FixedLegInput] = []
    counter_overrides: dict[str, Decimal] = {}

    for idx_pos, start_idx in enumerate(out_indices):
        end_idx = out_indices[idx_pos + 1] if idx_pos + 1 < len(out_indices) else len(tokens)
        block = tokens[start_idx:end_idx]
        outcome_raw = block[0].lower()
        param_tokens = block[1:]

        if len(param_tokens) >= 2:
            classified = _classify_odds_and_stake(param_tokens[0], param_tokens[1])
            if classified is not None:
                leg_odds, leg_stake = classified
                fixed_legs.append(FixedLegInput(outcome_raw, leg_odds, leg_stake))
        elif len(param_tokens) == 1:
            c_odds = parse_odds(param_tokens[0])
            if c_odds is not None:
                counter_overrides[outcome_raw] = c_odds

    if not fixed_legs:
        return None

    return ParsedHedgingRequest(
        fixed_legs=fixed_legs,
        counter_odds_override=counter_overrides,
    )


def to_domain_outcome(key: object) -> Outcome | TwoWayOutcome:
    if isinstance(key, (Outcome, TwoWayOutcome)):
        return key
    s = str(getattr(key, "value", key))
    if s in Outcome._value2member_map_:
        return Outcome(s)
    if s in TwoWayOutcome._value2member_map_:
        return TwoWayOutcome(s)
    return Outcome.HOME


def resolve_outcome(
    opportunity: CalculatedOpportunity, outcome_raw: str | None
) -> Outcome | TwoWayOutcome:
    """Resolve user-provided outcome string to matching Outcome or default to primary."""
    market_outcomes = [to_domain_outcome(k) for k in opportunity.selections]
    if not outcome_raw:
        return market_outcomes[0]

    raw = outcome_raw.lower()
    if opportunity.market_type == MarketType.TOTAL_GOALS_25:
        if raw in ("üst", "ust", "over", "1"):
            return TwoWayOutcome.OVER
        if raw in ("alt", "under", "2"):
            return TwoWayOutcome.UNDER
        return market_outcomes[0]

    # Full Time Result (1X2)
    if raw in ("ms1", "1", "ev", "home"):
        return Outcome.HOME
    if raw in ("x", "0", "beraberlik", "draw"):
        return Outcome.DRAW
    if raw in ("ms2", "2", "dep", "deplasman", "away"):
        return Outcome.AWAY

    return market_outcomes[0]


def format_allocation_response(
    opportunity: CalculatedOpportunity,
    result: AllocationResult,
) -> tuple[str, dict[str, Any]]:
    """Generate HTML text and inline keyboard buttons for a calculation result."""
    lines: list[str] = [
        "🧮 <b>Bahis Dağılım Hesabı</b>",
        f"<b>{html.escape(opportunity.home)} - {html.escape(opportunity.away)}</b>",
        f"<i>{html.escape(opportunity.country_name or '')} / {html.escape(opportunity.league_name or opportunity.competition)}</i>",
        "",
    ]

    stakes = {line.outcome: line.stake_cents for line in result.lines}
    payouts = {line.outcome: line.payout_cents for line in result.lines}

    if result.fixed_outcome is not None:
        fixed_label = _outcome_label(result.fixed_outcome)
        fixed_sel = opportunity.selections[result.fixed_outcome]
        fixed_src = _SOURCE_NAMES.get(fixed_sel.source, fixed_sel.source.value)
        fixed_stake = format_lira(stakes.get(result.fixed_outcome, 0))
        lines.append(
            f"📌 <b>Sabitlenen:</b> {fixed_label} ({fixed_src} @{fixed_sel.odds:.2f}) ➡️ <b>{fixed_stake}</b>"
        )
    else:
        lines.append(f"💰 <b>Toplam Bütçe Dağıtımı:</b> {format_lira(result.total_budget_cents)}")

    lines.append("")
    lines.append("📊 <b>Önerilen Bahis Dağılımı:</b>")
    for outcome, sel in opportunity.selections.items():
        domain_outcome = to_domain_outcome(outcome)
        label = _outcome_label(domain_outcome)
        src = _SOURCE_NAMES.get(sel.source, sel.source.value)
        stake = format_lira(stakes.get(domain_outcome, 0))
        payout = format_lira(payouts.get(domain_outcome, 0))
        lines.append(f"• <b>{label}:</b> {src} @{sel.odds:.2f} ➡️ <b>{stake}</b> (Dönüş: {payout})")

    lines.append("")
    lines.append(f"💵 <b>Toplam Yatırım:</b> {format_lira(result.total_budget_cents)}")
    profit_sign = "+" if result.min_profit_cents >= 0 else ""
    lines.append(
        f"📈 <b>Hesaplanan Net Fark:</b> {profit_sign}{format_lira(result.min_profit_cents)} "
        f"(%{format_bps(result.profit_bps)})"
    )

    keyboard_rows: list[list[dict[str, str]]] = []
    card_key = f"{opportunity.matched_id}:{opportunity.market_type.value}"
    keyboard_rows.append(
        [
            {
                "text": "🧮 Yeniden Hesapla",
                "callback_data": f"calc:{card_key}",
            },
            {
                "text": "💡 Örnek Komutlar",
                "callback_data": f"help:{card_key}",
            },
        ]
    )

    reply_markup = {"inline_keyboard": keyboard_rows} if keyboard_rows else {}
    return "\n".join(lines), reply_markup


def format_hedging_response(
    opportunity: CalculatedOpportunity,
    result: HedgingResult,
) -> tuple[str, dict[str, Any]]:
    """Generate HTML text and inline keyboard buttons for a hedging result."""
    lines: list[str] = [
        "🛡️ <b>Bahis Kurtarma & Dengeleme (Hedge)</b>",
        f"<b>{html.escape(opportunity.home)} - {html.escape(opportunity.away)}</b>",
        f"<i>{html.escape(opportunity.country_name or '')} / {html.escape(opportunity.league_name or opportunity.competition)}</i>",
        "",
    ]

    fixed_lines = [line for line in result.lines if line.is_fixed]
    counter_lines = [line for line in result.lines if not line.is_fixed]

    if len(fixed_lines) > 1:
        lines.append("📌 <b>Oynanmış Bacaklar:</b>")
    else:
        lines.append("📌 <b>Oynanmış Bacak:</b>")

    for fl in fixed_lines:
        fl_label = _outcome_label(fl.outcome)
        fl_src_name = _SOURCE_NAMES.get(fl.source, fl.source.value)
        lines.append(
            f"• <b>{fl_label}:</b> {fl_src_name} @{fl.odds:.2f} ➡️ <b>{format_lira(fl.stake_cents)}</b> "
            f"(Dönüş: {format_lira(fl.payout_cents)})"
        )
    lines.append("")

    if result.is_profitable:
        if len(counter_lines) > 1:
            lines.append("📊 <b>Kapatılması Gereken Karşı Bahisler:</b>")
        else:
            lines.append("📊 <b>Kapatılması Gereken Karşı Bahis:</b>")
    else:
        if len(counter_lines) > 1:
            lines.append("📊 <b>Zarar Minimize Karşı Bahisler:</b>")
        else:
            lines.append("📊 <b>Zarar Minimize Karşı Bahis:</b>")

    for cl in counter_lines:
        c_label = _outcome_label(cl.outcome)
        c_src_name = _SOURCE_NAMES.get(cl.source, cl.source.value)
        lines.append(
            f"• <b>{c_label}:</b> {c_src_name} @{cl.odds:.2f} ➡️ <b>{format_lira(cl.stake_cents)}</b> "
            f"(Dönüş: {format_lira(cl.payout_cents)})"
        )

    lines.append("")
    lines.append(f"💵 <b>Toplam Yatırım:</b> {format_lira(result.total_investment_cents)}")

    if result.is_profitable:
        lines.append(
            f"📈 <b>Net Durum:</b> 🟢 <b>+{format_lira(result.net_profit_cents)}</b> "
            f"(%{format_bps(result.profit_bps)}) <i>[Pozitif Fark]</i>"
        )
    else:
        lines.append(
            f"📉 <b>Net Durum:</b> 🛡️ <b>{format_lira(result.net_profit_cents)}</b> "
            f"(%{format_bps(result.profit_bps)}) <i>[Kayıp Eşitlendi]</i>"
        )

    lines.append("")
    lines.append("🛡️ <b>Risk Analizi:</b>")
    lines.append(
        f"• Karşı bahis alınmazsa çıplak risk: <b>-{format_lira(result.naked_loss_cents)}</b> (%-100)"
    )
    if result.is_profitable:
        lines.append(
            f"• Dengeleme sonrası hesaplanan net fark: <b>+{format_lira(result.net_profit_cents)}</b>"
        )
    else:
        lines.append(f"• Kurtarılan tutar: <b>+{format_lira(result.saved_amount_cents)}</b>")
        lines.append(
            f"• Her senaryoda kilitlenen net kayıp: <b>{format_lira(result.net_profit_cents)}</b>"
        )

    keyboard_rows: list[list[dict[str, str]]] = []
    card_key = f"{opportunity.matched_id}:{opportunity.market_type.value}"
    keyboard_rows.append(
        [
            {
                "text": "🛡️ Yeniden Hesapla",
                "callback_data": f"hedge_prompt:{card_key}",
            },
            {
                "text": "🧮 Dağılım Hesapla",
                "callback_data": f"calc:{card_key}",
            },
        ]
    )

    reply_markup = {"inline_keyboard": keyboard_rows} if keyboard_rows else {}
    return "\n".join(lines), reply_markup


@dataclass(frozen=True)
class QuickAccountingCommand:
    tx_type: TransactionType
    amount_str: str
    raw_date: str | None = None
    note: str | None = None


_DEPOSIT_KEYWORDS: set[str] = {
    "yatir",
    "yatır",
    "deposit",
    "depozit",
    "yatirma",
    "yatırma",
}
_WITHDRAWAL_KEYWORDS: set[str] = {
    "cekim",
    "çekim",
    "withdraw",
    "withdrawal",
    "cekme",
    "çekme",
}


def _is_date_token(tok: str) -> bool:
    cleaned = tok.strip().lower()
    if cleaned in ("dun", "dün", "bugun", "bugün", "today", "yesterday"):
        return True
    if re.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$", cleaned):
        return True
    if re.match(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$", cleaned):
        return True
    if re.match(r"^\d{1,2}[-/.]\d{1,2}$", cleaned):
        parts = re.split(r"[-/.]", cleaned)
        try:
            d, m = int(parts[0]), int(parts[1])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return True
        except ValueError:
            pass
    return False


def parse_quick_accounting_command(text: str) -> QuickAccountingCommand | None:
    raw = text.strip()
    if not raw:
        return None

    tokens = raw.split()
    if len(tokens) < 2:
        return None

    first = tokens[0].lower().lstrip("/")
    if first in _DEPOSIT_KEYWORDS:
        tx_type = TransactionType.DEPOSIT
    elif first in _WITHDRAWAL_KEYWORDS:
        tx_type = TransactionType.WITHDRAWAL
    else:
        return None

    rem_tokens = tokens[1:]
    raw_date: str | None = None
    non_date_tokens: list[str] = []

    for tok in rem_tokens:
        if raw_date is None and _is_date_token(tok):
            raw_date = tok
        else:
            non_date_tokens.append(tok)

    if not non_date_tokens:
        return None

    amount_str: str | None = None
    note: str | None = None

    # Pattern 1a: non_date_tokens[0] is number, non_date_tokens[1] is currency (e.g. "5000 TL")
    if len(non_date_tokens) >= 2 and non_date_tokens[1].lower() in ("tl", "try", "lira", "₺"):
        try:
            cand = f"{non_date_tokens[0]} {non_date_tokens[1]}"
            if parse_amount_to_cents(cand) > 0:
                amount_str = cand
                note_parts = non_date_tokens[2:]
                note = " ".join(note_parts).strip() or None
        except Exception:  # noqa: BLE001
            amount_str = None

    # Pattern 1b: non_date_tokens[0] is amount (e.g. "5000", "10k")
    if amount_str is None:
        try:
            cand = non_date_tokens[0]
            if parse_amount_to_cents(cand) > 0:
                amount_str = cand
                note_parts = non_date_tokens[1:]
                note = " ".join(note_parts).strip() or None
        except Exception:  # noqa: BLE001
            amount_str = None

    # Pattern 2: amount is at the end (e.g. "source_a 5000" or "source_a 5000 TL")
    if amount_str is None and len(non_date_tokens) >= 2:
        if non_date_tokens[-1].lower() in ("tl", "try", "lira", "₺") and len(non_date_tokens) >= 3:
            try:
                cand = f"{non_date_tokens[-2]} {non_date_tokens[-1]}"
                if parse_amount_to_cents(cand) > 0:
                    amount_str = cand
                    note_parts = non_date_tokens[:-2]
                    note = " ".join(note_parts).strip() or None
            except Exception:  # noqa: BLE001
                amount_str = None
        if amount_str is None:
            try:
                cand = non_date_tokens[-1]
                if parse_amount_to_cents(cand) > 0:
                    amount_str = cand
                    note_parts = non_date_tokens[:-1]
                    note = " ".join(note_parts).strip() or None
            except Exception:  # noqa: BLE001
                amount_str = None

    if amount_str is None:
        return None

    if raw_date is not None:
        try:
            parse_date_input(raw_date)
        except Exception:  # noqa: BLE001
            return None

    return QuickAccountingCommand(
        tx_type=tx_type,
        amount_str=amount_str,
        raw_date=raw_date,
        note=note,
    )


def render_accounting_menu(
    summary_daily: DailySummary, summary_weekly: WeeklySummary
) -> tuple[str, dict[str, Any]]:
    lines = [
        "💼 <b>Kasa & Günlük Muhasebe</b>",
        "",
        f"📅 <b>Bugün:</b> {format_lira(summary_daily.net_profit_cents, signed=True)} ({summary_daily.transaction_count} işlem)",
        f"  • Yatırım: {format_lira(summary_daily.total_deposit_cents)}",
        f"  • Çekim: {format_lira(summary_daily.total_withdrawal_cents)}",
        "",
        f"📈 <b>Bu Hafta:</b> {format_lira(summary_weekly.net_profit_cents, signed=True)}",
        f"  • Yatırım: {format_lira(summary_weekly.total_deposit_cents)}",
        f"  • Çekim: {format_lira(summary_weekly.total_withdrawal_cents)}",
        "",
        "<i>💡 Hızlı komutlar: <code>yatir 5000</code> veya <code>cekim 8000 dün</code></i>",
    ]
    keyboard = [
        [
            {"text": "➕ Yatırım Gir", "callback_data": "acc_dep_prompt"},
            {"text": "➖ Çekim Gir", "callback_data": "acc_with_prompt"},
        ],
        [
            {"text": "📊 Günlük Rapor", "callback_data": "acc_daily_rep"},
            {"text": "📈 Haftalık Rapor", "callback_data": "acc_weekly_rep"},
        ],
        [
            {"text": "🏢 Kaynak B Hesapları", "callback_data": "acc_accounts"},
            {"text": "📋 Son İşlemler / İptal", "callback_data": "acc_recent"},
        ],
        [
            {"text": "❓ Yardım", "callback_data": "help_menu"},
        ],
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def render_accounts_list(
    accounts: list[BankrollAccount],
) -> tuple[str, dict[str, Any]]:
    lines = [
        "🏢 <b>Kaynak B Hesaplarım</b>",
        f"<i>Toplam {len(accounts)} aktif hesap</i>",
        "",
    ]
    if not accounts:
        lines.append(
            "<i>Henüz kayıtlı bir Kaynak B hesabı bulunmuyor. Web panelinden (Kasa sekmesi) hesap ekleyebilirsiniz.</i>"
        )
    else:
        for acc in accounts:
            bal_str = format_lira(acc.current_balance_cents)
            profit_str = format_lira(acc.net_profit_cents, signed=True)
            icon = "🟢" if acc.net_profit_cents >= 0 else "🔴"
            note_str = f" <i>({html.escape(acc.note)})</i>" if acc.note else ""
            lines.append(
                f"• <b>{html.escape(acc.account_name)}:</b> {bal_str} | K/Z: {icon} {profit_str}{note_str}"
            )

    keyboard = [
        [
            {"text": "💼 Menü", "callback_data": "acc_menu"},
            {"text": "➕ Yatırım Gir", "callback_data": "acc_dep_prompt"},
            {"text": "➖ Çekim Gir", "callback_data": "acc_with_prompt"},
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def render_daily_report(summary: DailySummary) -> tuple[str, dict[str, Any]]:
    lines = [
        f"📊 <b>Günlük Muhasebe Raporu ({html.escape(summary.date_str)})</b>",
        "",
        f"➕ <b>Toplam Yatırım:</b> {format_lira(summary.total_deposit_cents)}",
        f"➖ <b>Toplam Çekim:</b> {format_lira(summary.total_withdrawal_cents)}",
        f"📈 <b>Net Kâr:</b> {format_lira(summary.net_profit_cents, signed=True)}",
        f"📝 <b>Toplam İşlem:</b> {summary.transaction_count}",
        "",
    ]
    if summary.account_breakdowns:
        lines.append("🏢 <b>Hesap Dağılımı:</b>")
        for ab in summary.account_breakdowns:
            profit_str = format_lira(ab.net_profit_cents, signed=True)
            icon = "🟢" if ab.net_profit_cents >= 0 else "🔴"
            lines.append(
                f"• <b>{html.escape(ab.account_name)}:</b> {icon} {profit_str} "
                f"(+{format_lira(ab.deposit_cents)} / -{format_lira(ab.withdrawal_cents)})"
            )
        lines.append("")

    if summary.transactions:
        lines.append("📋 <b>İşlemler:</b>")
        for tx in summary.transactions:
            sign = "➕" if tx.tx_type == TransactionType.DEPOSIT else "➖"
            type_label = "Yatırım" if tx.tx_type == TransactionType.DEPOSIT else "Çekim"
            note_str = f" <i>({html.escape(tx.note)})</i>" if tx.note else ""
            lines.append(
                f"• #{tx.id} {sign} {type_label}: <b>{format_lira(tx.amount_cents)}</b>{note_str}"
            )
    else:
        lines.append("<i>Bu tarihe ait kayıtlı aktif işlem bulunmuyor.</i>")

    keyboard = [
        [
            {"text": "💼 Menü", "callback_data": "acc_menu"},
            {"text": "➕ Yatırım Gir", "callback_data": "acc_dep_prompt"},
            {"text": "➖ Çekim Gir", "callback_data": "acc_with_prompt"},
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def render_weekly_report(summary: WeeklySummary) -> tuple[str, dict[str, Any]]:
    lines = [
        "📈 <b>Haftalık Muhasebe Raporu</b>",
        f"🗓️ <i>{html.escape(summary.week_start_str)} — {html.escape(summary.week_end_str)}</i>",
        "",
        f"➕ <b>Haftalık Yatırım:</b> {format_lira(summary.total_deposit_cents)}",
        f"➖ <b>Haftalık Çekim:</b> {format_lira(summary.total_withdrawal_cents)}",
        f"📈 <b>Haftalık Net Kâr:</b> {format_lira(summary.net_profit_cents, signed=True)}",
        "",
        "📅 <b>Günlük Dağılım:</b>",
    ]
    for day in summary.daily_breakdown:
        day_profit_str = format_lira(day.net_profit_cents, signed=True)
        if day.has_transactions:
            icon = "🟢" if day.net_profit_cents >= 0 else "🔴"
            lines.append(
                f"• <b>{day.day_name}</b> ({day.date_str}): {icon} <b>{day_profit_str}</b> "
                f"(+{format_lira(day.deposit_cents)} / -{format_lira(day.withdrawal_cents)})"
            )
        else:
            lines.append(f"• <b>{day.day_name}</b> ({day.date_str}): ⚪ <i>İşlem yok</i>")

    if summary.account_breakdowns:
        lines.append("")
        lines.append("🏢 <b>Hesap Dağılımı:</b>")
        for ab in summary.account_breakdowns:
            profit_str = format_lira(ab.net_profit_cents, signed=True)
            icon = "🟢" if ab.net_profit_cents >= 0 else "🔴"
            lines.append(
                f"• <b>{html.escape(ab.account_name)}:</b> {icon} {profit_str} "
                f"(+{format_lira(ab.deposit_cents)} / -{format_lira(ab.withdrawal_cents)})"
            )

    keyboard = [
        [
            {"text": "💼 Menü", "callback_data": "acc_menu"},
            {"text": "📊 Günlük Rapor", "callback_data": "acc_daily_rep"},
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def render_recent_transactions(
    txs: list[AccountingTransaction],
) -> tuple[str, dict[str, Any]]:
    lines = [
        "📋 <b>Son Muhasebe İşlemleri</b>",
        "",
    ]
    keyboard: list[list[dict[str, str]]] = []
    if txs:
        has_active = any(tx.status == TransactionStatus.ACTIVE for tx in txs)
        if has_active:
            lines.append("İptal etmek istediğiniz işlemin butonuna tıklayabilirsiniz:\n")
        else:
            lines.append("<i>(Aktif iptal edilebilir işlem bulunmuyor)</i>\n")

        for tx in txs:
            sign = "➕" if tx.tx_type == TransactionType.DEPOSIT else "➖"
            type_label = "Yatırım" if tx.tx_type == TransactionType.DEPOSIT else "Çekim"
            note_str = f" <i>({html.escape(tx.note)})</i>" if tx.note else ""
            if tx.status == TransactionStatus.CANCELLED:
                lines.append(
                    f"• <b>#{tx.id}</b> {sign} {type_label}: <s><b>{format_lira(tx.amount_cents)}</b></s>{note_str} "
                    f"· <i>{html.escape(tx.tx_date)}</i> — ❌ <i>(İptal Edildi)</i>"
                )
            else:
                lines.append(
                    f"• <b>#{tx.id}</b> {sign} {type_label}: <b>{format_lira(tx.amount_cents)}</b>{note_str} "
                    f"· <i>{html.escape(tx.tx_date)}</i>"
                )
                btn_note = f" · {tx.note[:15]}" if tx.note else ""
                keyboard.append(
                    [
                        {
                            "text": f"❌ #{tx.id} İptal ({format_lira(tx.amount_cents)}{btn_note})",
                            "callback_data": f"acc_cancel:{tx.id}",
                        }
                    ]
                )
    else:
        lines.append("<i>Kayıtlı işlem bulunmuyor.</i>")

    keyboard.append([{"text": "💼 Menü", "callback_data": "acc_menu"}])
    return "\n".join(lines), {"inline_keyboard": keyboard}


def render_transaction_confirmed(
    tx: AccountingTransaction,
    daily: DailySummary,
    account_name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    type_label = "Yatırım" if tx.tx_type == TransactionType.DEPOSIT else "Çekim"
    type_icon = "➕" if tx.tx_type == TransactionType.DEPOSIT else "➖"

    lines = [
        f"✅ <b>#{tx.id} {type_label} Kaydedildi</b>",
        "",
        f"{type_icon} <b>Tür:</b> {type_label}",
    ]
    if account_name:
        lines.append(f"🏢 <b>Hesap:</b> {html.escape(account_name)}")
    lines.extend(
        [
            f"💵 <b>Tutar:</b> {format_lira(tx.amount_cents)}",
            f"📅 <b>Tarih:</b> {html.escape(tx.tx_date)}",
            f"📈 <b>Güncel Günlük Kâr:</b> {format_lira(daily.net_profit_cents, signed=True)}",
        ]
    )
    if tx.note and (not account_name or tx.note.strip().lower() != account_name.strip().lower()):
        lines.append(f"📝 <b>Not:</b> {html.escape(tx.note)}")

    keyboard = [
        [
            {"text": "❌ Bu İşlemi İptal Et", "callback_data": f"acc_cancel:{tx.id}"},
            {"text": "💼 Menü", "callback_data": "acc_menu"},
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


class TelegramBotListener:
    """Asynchronous background daemon listening to user interactions on Telegram."""

    def __init__(
        self,
        client: TelegramClient,
        repo: NotificationRepository,
        *,
        card_renderer: CardRenderer | None = None,
        poll_interval_seconds: int = 2,
        accounting_service: AccountingService | None = None,
    ) -> None:
        self._client = client
        self._repo = repo
        self._card_renderer = card_renderer or CardRenderer()
        self._poll_interval = poll_interval_seconds
        self._offset: int | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

        if accounting_service is not None:
            self._accounting_service = accounting_service
        else:
            db_path = getattr(repo, "_db_path", "surebet.db")
            self._accounting_service = AccountingService(AccountingRepository(db_path))

        self._pending_accounting_inputs: dict[int, tuple[TransactionType, str]] = {}

    def start(self) -> None:
        """Start the background long-polling listener task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("TelegramBotListener started.")

    async def stop(self) -> None:
        """Stop the background listener cleanly."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TelegramBotListener stopped.")

    def _resolve_user_id(self, chat_id: int) -> int | None:
        if hasattr(self._repo, "get_user_id_by_chat_id"):
            val = self._repo.get_user_id_by_chat_id(chat_id)
            if isinstance(val, int):
                return val
        return None

    def _get_daily_summary(
        self, chat_id: int, date_str: str | None = None, user_id: int | None = None
    ) -> DailySummary:
        if user_id is not None:
            return self._accounting_service.get_daily_summary(
                chat_id=chat_id, target_date_str=date_str, user_id=user_id
            )
        if date_str is not None:
            return self._accounting_service.get_daily_summary(chat_id, target_date_str=date_str)
        return self._accounting_service.get_daily_summary(chat_id)

    def _get_weekly_summary(self, chat_id: int, user_id: int | None = None) -> WeeklySummary:
        if user_id is not None:
            return self._accounting_service.get_weekly_summary(chat_id=chat_id, user_id=user_id)
        return self._accounting_service.get_weekly_summary(chat_id)

    def _record_transaction(
        self,
        chat_id: int,
        tx_type: TransactionType,
        amount: str | int,
        raw_date: str | None = None,
        note: str | None = None,
        account_id: int | None = None,
        user_id: int | None = None,
    ) -> AccountingTransaction:
        if account_id is not None or user_id is not None:
            kwargs: dict[str, Any] = {
                "chat_id": chat_id,
                "tx_type": tx_type,
                "amount": amount,
                "raw_date": raw_date,
                "note": note,
            }
            if account_id is not None:
                kwargs["account_id"] = account_id
            if user_id is not None:
                kwargs["user_id"] = user_id
            return self._accounting_service.record(**kwargs)
        return self._accounting_service.record(
            chat_id=chat_id,
            tx_type=tx_type,
            amount=amount,
            raw_date=raw_date,
            note=note,
        )

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = await self._client.get_updates(offset=self._offset, timeout=30)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = max(self._offset or 0, update_id + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegram polling iteration error: %s", exc)
                await asyncio.sleep(self._poll_interval)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback_query(update["callback_query"])
            elif "message" in update:
                await self._handle_message(update["message"])
        except Exception:
            logger.exception("Error processing Telegram update")

    async def _handle_callback_query(self, cb: dict[str, Any]) -> None:
        cb_id = str(cb.get("id", ""))
        data = str(cb.get("data", ""))
        message = cb.get("message") or {}
        chat_id = message.get("chat", {}).get("id")

        if not chat_id:
            return

        if data == "acc_menu":
            await self._client.answer_callback_query(cb_id)
            user_id = self._resolve_user_id(chat_id)
            daily = self._get_daily_summary(chat_id=chat_id, user_id=user_id)
            weekly = self._get_weekly_summary(chat_id=chat_id, user_id=user_id)
            text, markup = render_accounting_menu(daily, weekly)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=markup,
                )
                if not res.ok:
                    await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            else:
                await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            return

        elif data == "acc_accounts":
            await self._client.answer_callback_query(cb_id)
            user_id = self._resolve_user_id(chat_id)
            accounts = (
                self._accounting_service.list_accounts(user_id=user_id)
                if user_id is not None
                else []
            )
            text, markup = render_accounts_list(accounts)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=markup,
                )
                if not res.ok:
                    await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            else:
                await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            return

        elif data in ("acc_dep_prompt", "acc_with_prompt"):
            await self._client.answer_callback_query(cb_id)
            is_dep = data == "acc_dep_prompt"
            tx_type = TransactionType.DEPOSIT if is_dep else TransactionType.WITHDRAWAL
            type_label = "Yatırım" if is_dep else "Çekim"
            type_icon = "➕" if is_dep else "➖"

            now_ist = datetime.now(ACCOUNTING_ISTANBUL).date()
            d0 = now_ist.isoformat()
            d1 = (now_ist - timedelta(days=1)).isoformat()
            d2 = (now_ist - timedelta(days=2)).isoformat()

            prompt_text = (
                f"{type_icon} <b>{type_label} Girişi</b>\n\n"
                "İşlem hangi gün gerçekleşti? Aşağıdan seçin veya iptal edin:"
            )
            date_keyboard = [
                [
                    {"text": f"📌 Bugün ({d0})", "callback_data": f"acc_date:{tx_type.value}:{d0}"},
                ],
                [
                    {"text": f"⏪ Dün ({d1})", "callback_data": f"acc_date:{tx_type.value}:{d1}"},
                    {
                        "text": f"🗓️ 2 Gün Önce ({d2})",
                        "callback_data": f"acc_date:{tx_type.value}:{d2}",
                    },
                ],
                [
                    {"text": "💼 Menü", "callback_data": "acc_menu"},
                ],
            ]
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=prompt_text,
                    reply_markup={"inline_keyboard": date_keyboard},
                )
                if not res.ok:
                    await self._client.send_text(
                        chat_id=chat_id,
                        text=prompt_text,
                        reply_markup={"inline_keyboard": date_keyboard},
                    )
            else:
                await self._client.send_text(
                    chat_id=chat_id,
                    text=prompt_text,
                    reply_markup={"inline_keyboard": date_keyboard},
                )
            return

        elif data.startswith("acc_date:"):
            parts = data.split(":")
            if len(parts) >= 3:
                tx_type_str = parts[1]
                date_str = parts[2]
                tx_type = TransactionType(tx_type_str)
                self._pending_accounting_inputs[chat_id] = (tx_type, date_str)
                await self._client.answer_callback_query(cb_id)

                type_label = "Yatırım" if tx_type == TransactionType.DEPOSIT else "Çekim"
                type_icon = "➕" if tx_type == TransactionType.DEPOSIT else "➖"
                prompt_text = (
                    f"{type_icon} <b>{type_label} Tutarı Girin</b>\n\n"
                    f"📅 <b>Tarih:</b> <code>{html.escape(date_str)}</code>\n\n"
                    "Lütfen tutarı bu sohbete yazın:\n"
                    "• Örnek: <code>5000</code> veya <code>10k</code>\n"
                    "• İptal etmek için: <code>vazgeç</code>"
                )
                prompt_keyboard = [[{"text": "❌ İptal", "callback_data": "acc_menu"}]]
                msg_id = message.get("message_id")
                if msg_id:
                    res = await self._client.edit_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=prompt_text,
                        reply_markup={"inline_keyboard": prompt_keyboard},
                    )
                    if not res.ok:
                        await self._client.send_text(
                            chat_id=chat_id,
                            text=prompt_text,
                            reply_markup={"inline_keyboard": prompt_keyboard},
                        )
                else:
                    await self._client.send_text(
                        chat_id=chat_id,
                        text=prompt_text,
                        reply_markup={"inline_keyboard": prompt_keyboard},
                    )
            return

        elif data == "acc_daily_rep":
            await self._client.answer_callback_query(cb_id)
            user_id = self._resolve_user_id(chat_id)
            daily_sum = self._get_daily_summary(chat_id=chat_id, user_id=user_id)
            text, markup = render_daily_report(daily_sum)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=markup,
                )
                if not res.ok:
                    await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            else:
                await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            return

        elif data == "acc_weekly_rep":
            await self._client.answer_callback_query(cb_id)
            user_id = self._resolve_user_id(chat_id)
            weekly_sum = self._get_weekly_summary(chat_id=chat_id, user_id=user_id)
            text, markup = render_weekly_report(weekly_sum)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=markup,
                )
                if not res.ok:
                    await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            else:
                await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            return

        elif data == "acc_recent":
            await self._client.answer_callback_query(cb_id)
            user_id = self._resolve_user_id(chat_id)
            txs = self._accounting_service.get_recent(chat_id=chat_id, user_id=user_id, limit=5)
            text, markup = render_recent_transactions(txs)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=markup,
                )
                if not res.ok:
                    await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            else:
                await self._client.send_text(chat_id=chat_id, text=text, reply_markup=markup)
            return

        elif data.startswith("acc_cancel:"):
            try:
                tx_id = int(data[len("acc_cancel:") :])
            except ValueError:
                await self._client.answer_callback_query(cb_id, text="Geçersiz işlem ID.")
                return

            cancelled = self._accounting_service.cancel(chat_id=chat_id, tx_id=tx_id)
            if cancelled is not None:
                await self._client.answer_callback_query(
                    cb_id, text=f"#{cancelled.id} nolu işlem iptal edildi."
                )
                daily = self._accounting_service.get_daily_summary(
                    chat_id=chat_id, target_date_str=cancelled.tx_date
                )
                type_label = "Yatırım" if cancelled.tx_type == TransactionType.DEPOSIT else "Çekim"
                c_text = (
                    f"🗑️ <b>#{cancelled.id} {type_label} İptal Edildi</b>\n\n"
                    f"💵 <b>Tutar:</b> {format_lira(cancelled.amount_cents)}\n"
                    f"📅 <b>Tarih:</b> {html.escape(cancelled.tx_date)}\n"
                    f"📈 <b>Güncel Günlük Kâr:</b> {format_lira(daily.net_profit_cents, signed=True)}"
                )
                if cancelled.note:
                    c_text += f"\n📝 <b>Açıklama:</b> {html.escape(cancelled.note)}"
                c_markup = {
                    "inline_keyboard": [
                        [{"text": "💼 Menü", "callback_data": "acc_menu"}],
                        [{"text": "📋 Son İşlemler", "callback_data": "acc_recent"}],
                    ]
                }
                msg_id = message.get("message_id")
                if msg_id:
                    res = await self._client.edit_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=c_text,
                        reply_markup=c_markup,
                    )
                    if not res.ok:
                        await self._client.send_text(
                            chat_id=chat_id, text=c_text, reply_markup=c_markup
                        )
                else:
                    await self._client.send_text(
                        chat_id=chat_id, text=c_text, reply_markup=c_markup
                    )
            else:
                await self._client.answer_callback_query(
                    cb_id, text="İşlem bulunamadı veya zaten iptal edilmiş.", show_alert=True
                )
            return

        if data.startswith("calc:"):
            card_key = data[5:]
            card_state = self._repo.get_card_by_key(card_key)
            if not card_state:
                await self._client.answer_callback_query(
                    cb_id, text="Kart bulunamadı veya süresi doldu."
                )
                return

            await self._client.answer_callback_query(cb_id)
            opp = card_state.evaluation.selected

            prompt_text = (
                f"🧮 <b>Bahis Dağılım Hesaplayıcı</b>\n"
                f"<b>{html.escape(opp.home)} - {html.escape(opp.away)}</b>\n\n"
                f"Aşağıdaki hazır tutarlardan birine tıklayabilir veya bu karta yanıt vererek "
                f"(reply) özel tutar yazabilirsiniz:\n"
                f"• Örnek: <code>3500</code> veya <code>ms1 2500</code> veya <code>bütçe 10000</code>"
            )

            # Interactive chips
            keyboard: list[list[dict[str, str]]] = []

            # Row 1: Fixed Outcome options with default 2500 TL
            row1: list[dict[str, str]] = []
            for outcome in opp.selections:
                lbl = _outcome_label(outcome)
                row1.append(
                    {
                        "text": f"📌 {lbl} (2.500 TL)",
                        "callback_data": f"do_calc|{card_key}|{outcome!s}|250000",
                    }
                )
            keyboard.append(row1)

            # Row 2: Quick stake chips for primary outcome
            first_outcome = next(iter(opp.selections))
            first_lbl = _outcome_label(first_outcome)
            row2 = [
                {
                    "text": f"{first_lbl}: 1.000 TL",
                    "callback_data": f"do_calc|{card_key}|{first_outcome!s}|100000",
                },
                {
                    "text": f"{first_lbl}: 5.000 TL",
                    "callback_data": f"do_calc|{card_key}|{first_outcome!s}|500000",
                },
                {
                    "text": f"{first_lbl}: 10.000 TL",
                    "callback_data": f"do_calc|{card_key}|{first_outcome!s}|1000000",
                },
            ]
            keyboard.append(row2)

            # Row 3: Budget and Help chip
            row3 = [
                {
                    "text": "💰 Bütçe: 10.000 TL",
                    "callback_data": f"do_budget|{card_key}|1000000",
                },
                {
                    "text": "💡 Örnek Komutlar",
                    "callback_data": f"help:{card_key}",
                },
            ]
            keyboard.append(row3)

            await self._client.send_text(
                chat_id=chat_id,
                text=prompt_text,
                reply_markup={"inline_keyboard": keyboard},
                reply_to_message_id=message.get("message_id"),
            )

        elif data.startswith("hedge_prompt:"):
            card_key = data[len("hedge_prompt:") :]
            card_state = self._repo.get_card_by_key(card_key)
            if not card_state:
                await self._client.answer_callback_query(
                    cb_id, text="Kart bulunamadı veya süresi doldu."
                )
                return

            await self._client.answer_callback_query(cb_id)
            opp = card_state.evaluation.selected

            prompt_text = (
                f"🛡️ <b>Bahis Kurtarma & Dengeleme (Hedge)</b>\n"
                f"<b>{html.escape(opp.home)} - {html.escape(opp.away)}</b>\n\n"
                f"Bir bacağı oynadıktan sonra oranlar değiştiyse, karta yanıt (reply) vererek komut gönderin:\n\n"
                f"• <b>Sadece oynadığınız bacak ve oran:</b>\n"
                f"<code>kurtar ms1 3.10 6000</code>\n"
                f"<i>(Diğer bacakların oranları bu karttan otomatik çekilir)</i>\n\n"
                f"• <b>Diğer oranlar da değiştiyse:</b>\n"
                f"<code>kurtar ms1 3.10 6000 x 3.30 ms2 2.40</code>\n\n"
                f"💡 <i>Fark pozitifse kalan bacakları eşit net farka göre hesaplar; arbitraj negatife döndüyse her senaryoda eşit minimum zararı hesaplar.</i>"
            )

            # Interactive chips for quick guidance
            keyboard = []
            row_legs = []
            for outcome in opp.selections:
                lbl = _outcome_label(outcome)
                row_legs.append(
                    {
                        "text": f"📌 {lbl} Oynadım",
                        "callback_data": f"hedge_leg|{card_key}|{outcome!s}",
                    }
                )
            keyboard.append(row_legs)
            keyboard.append(
                [
                    {
                        "text": "🧮 Standart Dağılım",
                        "callback_data": f"calc:{card_key}",
                    },
                    {
                        "text": "🔄 Güncel Oranlar",
                        "callback_data": f"refresh:{card_key}",
                    },
                ]
            )

            await self._client.send_text(
                chat_id=chat_id,
                text=prompt_text,
                reply_markup={"inline_keyboard": keyboard},
                reply_to_message_id=message.get("message_id"),
            )

        elif data.startswith("hedge_leg|"):
            parts = data.split("|")
            if len(parts) >= 3:
                card_key = parts[1]
                outcome_str = parts[2]
                card_state = self._repo.get_card_by_key(card_key)
                if not card_state:
                    await self._client.answer_callback_query(
                        cb_id, text="Kart bulunamadı veya süresi doldu."
                    )
                    return

                opp = card_state.evaluation.selected
                outcome = resolve_outcome(opp, outcome_str)
                lbl = _outcome_label(outcome)
                sel = opp.selections.get(outcome) or opp.selections[next(iter(opp.selections))]
                await self._client.answer_callback_query(cb_id)
                hint_text = (
                    f"🛡️ <b>{lbl} İçin Kurtarma Komutu</b>\n\n"
                    f"Aşağıdaki komutu kopyalayıp tutarı ve gerçekleşen oranı düzenleyerek bu mesaja yanıt verin:\n\n"
                    f"<code>kurtar {lbl.lower()} {sel.odds:.2f} 5000</code>"
                )
                await self._client.send_text(
                    chat_id=chat_id,
                    text=hint_text,
                    reply_to_message_id=message.get("message_id"),
                )

        elif data.startswith("help:") or data == "help":
            await self._client.answer_callback_query(cb_id)
            h_text, h_markup = render_help_topic("stake")
            await self._client.send_text(
                chat_id=chat_id,
                text=h_text,
                reply_markup=h_markup,
                reply_to_message_id=message.get("message_id"),
            )

        elif data == "help_menu":
            await self._client.answer_callback_query(cb_id)
            h_text, h_markup = render_help_menu()
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=h_text,
                    reply_markup=h_markup,
                )
                if not res.ok:
                    await self._client.send_text(
                        chat_id=chat_id, text=h_text, reply_markup=h_markup
                    )
            else:
                await self._client.send_text(chat_id=chat_id, text=h_text, reply_markup=h_markup)

        elif data.startswith("help_topic:"):
            topic_id = data[len("help_topic:") :]
            await self._client.answer_callback_query(cb_id)
            h_text, h_markup = render_help_topic(topic_id)
            msg_id = message.get("message_id")
            if msg_id:
                res = await self._client.edit_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=h_text,
                    reply_markup=h_markup,
                )
                if not res.ok:
                    await self._client.send_text(
                        chat_id=chat_id, text=h_text, reply_markup=h_markup
                    )
            else:
                await self._client.send_text(chat_id=chat_id, text=h_text, reply_markup=h_markup)

        elif data.startswith("refresh:"):
            card_key = data[8:]
            card_state = self._repo.get_card_by_key(card_key)
            if not card_state:
                await self._client.answer_callback_query(
                    cb_id, text="⚠️ Maç verisi bulunamadı veya süresi doldu.", show_alert=True
                )
                return

            opp = card_state.evaluation.selected
            if card_state.status is LifecycleStatus.CLOSED:
                alert_text = (
                    f"⚠️ Bu fırsat kapandı!\n"
                    f"Maç: {opp.home} - {opp.away}\n"
                    f"Oranlar değişti veya maç başladı."
                )
            else:
                age_sec = max(0, int((datetime.now(UTC) - opp.calculated_at).total_seconds()))
                age_str = f"{age_sec} sn önce" if age_sec < 60 else f"{age_sec // 60} dk önce"
                parts_odds = []
                for out, sel in opp.selections.items():
                    lbl = _outcome_label(out)
                    src_title = _SOURCE_NAMES.get(sel.source, sel.source.value)
                    parts_odds.append(f"{lbl}: {src_title} @{sel.odds:.2f}")
                odds_str = " · ".join(parts_odds)
                alert_text = (
                    f"🔄 Anlık Durum (Son kontrol: {age_str})\n"
                    f"{opp.home} - {opp.away}\n"
                    f"{odds_str}\n"
                    f"Net Fark: %{format_bps(card_state.current_profit_bps)} · 🟢 Aktif"
                )

            await self._client.answer_callback_query(cb_id, text=alert_text, show_alert=True)

        elif data.startswith("mute:"):
            card_key = data[5:]
            self._repo.mute_card(chat_id, card_key, duration_seconds=7200)
            await self._client.answer_callback_query(cb_id, text="Maç 2 saat boyunca susturuldu 🔕")
            card_state = self._repo.get_card_by_key(card_key)
            msg_id = message.get("message_id")
            if card_state and msg_id:
                is_fav = self._repo.is_favorite(chat_id, card_key)
                new_markup = self._card_renderer.build_reply_markup(
                    card_state, is_muted=True, is_fav=is_fav
                )
                await self._client.edit_reply_markup(chat_id, msg_id, reply_markup=new_markup)

        elif data.startswith("unmute:"):
            card_key = data[7:]
            self._repo.unmute_card(chat_id, card_key)
            await self._client.answer_callback_query(cb_id, text="Susturma kaldırıldı 🔔")
            card_state = self._repo.get_card_by_key(card_key)
            msg_id = message.get("message_id")
            if card_state and msg_id:
                is_fav = self._repo.is_favorite(chat_id, card_key)
                new_markup = self._card_renderer.build_reply_markup(
                    card_state, is_muted=False, is_fav=is_fav
                )
                await self._client.edit_reply_markup(chat_id, msg_id, reply_markup=new_markup)

        elif data.startswith("fav:"):
            card_key = data[4:]
            is_fav_now = self._repo.toggle_favorite(chat_id, card_key)
            toast_text = (
                "Fırsat favorilere eklendi ⭐" if is_fav_now else "Fırsat favorilerden çıkarıldı 📌"
            )
            await self._client.answer_callback_query(cb_id, text=toast_text)
            card_state = self._repo.get_card_by_key(card_key)
            msg_id = message.get("message_id")
            if card_state and msg_id:
                is_muted = self._repo.is_card_muted(chat_id, card_key)
                new_markup = self._card_renderer.build_reply_markup(
                    card_state, is_muted=is_muted, is_fav=is_fav_now
                )
                await self._client.edit_reply_markup(chat_id, msg_id, reply_markup=new_markup)

        elif data.startswith("do_calc|"):
            parts = data.split("|")
            if len(parts) >= 4:
                card_key = parts[1]
                outcome_str = parts[2]
                cents = int(parts[3])

                card_state = self._repo.get_card_by_key(card_key)
                if not card_state:
                    await self._client.answer_callback_query(
                        cb_id, text="Kart bulunamadı veya süresi doldu."
                    )
                    return

                await self._client.answer_callback_query(cb_id, text="Hesaplandı!")
                opp = card_state.evaluation.selected
                outcome = resolve_outcome(opp, outcome_str)
                odds = {to_domain_outcome(out): s.odds for out, s in opp.selections.items()}
                sources = {to_domain_outcome(out): s.source for out, s in opp.selections.items()}
                result = calculate_fixed_stake_allocation(
                    fixed_outcome=outcome,
                    fixed_stake_cents=cents,
                    odds=odds,
                    sources=sources,
                )
                resp_text, markup = format_allocation_response(opp, result)
                await self._client.send_text(
                    chat_id=chat_id,
                    text=resp_text,
                    reply_markup=markup,
                    reply_to_message_id=message.get("message_id"),
                )

        elif data.startswith("do_budget|"):
            parts = data.split("|")
            if len(parts) >= 3:
                card_key = parts[1]
                budget_cents = int(parts[2])

                card_state = self._repo.get_card_by_key(card_key)
                if not card_state:
                    await self._client.answer_callback_query(
                        cb_id, text="Kart bulunamadı veya süresi doldu."
                    )
                    return

                await self._client.answer_callback_query(cb_id, text="Hesaplandı!")
                opp = card_state.evaluation.selected
                odds = {to_domain_outcome(out): s.odds for out, s in opp.selections.items()}
                sources = {to_domain_outcome(out): s.source for out, s in opp.selections.items()}
                result = calculate_budget_allocation(
                    total_budget_cents=budget_cents,
                    odds=odds,
                    sources=sources,
                )
                resp_text, markup = format_allocation_response(opp, result)
                await self._client.send_text(
                    chat_id=chat_id,
                    text=resp_text,
                    reply_markup=markup,
                    reply_to_message_id=message.get("message_id"),
                )

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        text = str(msg.get("text", "")).strip()
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")
        if not chat_id or not text:
            return

        words = text.split(maxsplit=1)
        base_cmd = words[0].lower().split("@")[0]
        if base_cmd in ("/start", "/help", "/yardim", "yardım", "yardim"):
            if len(words) > 1:
                topic_id = resolve_help_topic_id(words[1])
                if topic_id:
                    h_text, h_markup = render_help_topic(topic_id)
                    await self._client.send_text(
                        chat_id=chat_id,
                        text=h_text,
                        reply_markup=h_markup,
                        reply_to_message_id=message_id,
                    )
                    return
            h_text, h_markup = render_help_menu()
            await self._client.send_text(
                chat_id=chat_id,
                text=h_text,
                reply_markup=h_markup,
                reply_to_message_id=message_id,
            )
            return

        if base_cmd in ("/muhasebe", "/kasa", "muhasebe", "kasa"):
            user_id = self._resolve_user_id(chat_id)
            daily = self._get_daily_summary(chat_id=chat_id, user_id=user_id)
            weekly = self._get_weekly_summary(chat_id=chat_id, user_id=user_id)
            menu_text, menu_markup = render_accounting_menu(daily, weekly)
            await self._client.send_text(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=menu_markup,
                reply_to_message_id=message_id,
            )
            return

        if base_cmd in ("/iptal", "iptal"):
            cmd_parts = text.split()
            cancelled: AccountingTransaction | None = None
            if len(cmd_parts) > 1 and cmd_parts[1].isdigit():
                tx_id = int(cmd_parts[1])
                cancelled = self._accounting_service.cancel(chat_id=chat_id, tx_id=tx_id)
            else:
                cancelled = self._accounting_service.cancel_latest(chat_id=chat_id)

            if cancelled is not None:
                user_id = self._resolve_user_id(chat_id)
                daily = self._get_daily_summary(
                    chat_id=chat_id, date_str=cancelled.tx_date, user_id=user_id
                )
                type_label = "Yatırım" if cancelled.tx_type == TransactionType.DEPOSIT else "Çekim"
                c_text = (
                    f"🗑️ <b>#{cancelled.id} {type_label} İptal Edildi!</b>\n\n"
                    f"💵 <b>Tutar:</b> {format_lira(cancelled.amount_cents)}\n"
                    f"📅 <b>Tarih:</b> {html.escape(cancelled.tx_date)}\n"
                    f"📈 <b>Güncel Günlük Kâr:</b> {format_lira(daily.net_profit_cents, signed=True)}"
                )
                if cancelled.note:
                    c_text += f"\n📝 <b>Açıklama:</b> {html.escape(cancelled.note)}"
            else:
                c_text = "⚠️ İptal edilecek aktif bir işlem bulunamadı."

            await self._client.send_text(
                chat_id=chat_id,
                text=c_text,
                reply_markup={
                    "inline_keyboard": [[{"text": "💼 Menü", "callback_data": "acc_menu"}]]
                },
                reply_to_message_id=message_id,
            )
            return

        if chat_id in self._pending_accounting_inputs:
            if text.lower() in ("vazgec", "vazgeç", "/iptal", "iptal", "/cancel", "cancel"):
                self._pending_accounting_inputs.pop(chat_id, None)
                await self._client.send_text(
                    chat_id=chat_id,
                    text="❌ İşlem girişi iptal edildi.",
                    reply_markup={
                        "inline_keyboard": [[{"text": "💼 Menü", "callback_data": "acc_menu"}]]
                    },
                    reply_to_message_id=message_id,
                )
                return

            tokens = text.strip().split()
            cents = None
            note = None
            if tokens:
                if len(tokens) >= 2 and tokens[1].lower() in ("tl", "try", "lira", "₺"):
                    try:
                        cents = parse_amount_to_cents(f"{tokens[0]} {tokens[1]}")
                        note = " ".join(tokens[2:]).strip() or None
                    except Exception:  # noqa: BLE001
                        cents = None
                if cents is None:
                    try:
                        cents = parse_amount_to_cents(tokens[0])
                        note = " ".join(tokens[1:]).strip() or None
                    except Exception:  # noqa: BLE001
                        cents = None

            if cents is not None and cents > 0:
                tx_type, date_str = self._pending_accounting_inputs.pop(chat_id)
                try:
                    user_id = self._resolve_user_id(chat_id)
                    account_id = None
                    account_name = None
                    if user_id is not None and note:
                        acc = self._accounting_service.resolve_account(user_id=user_id, text=note)
                        if acc is not None:
                            account_id = acc.id
                            account_name = acc.account_name

                    tx = self._record_transaction(
                        chat_id=chat_id,
                        tx_type=tx_type,
                        amount=cents,
                        raw_date=date_str,
                        note=note,
                        account_id=account_id,
                        user_id=user_id,
                    )
                    daily = self._get_daily_summary(
                        chat_id=chat_id, date_str=tx.tx_date, user_id=user_id
                    )
                    conf_text, conf_markup = render_transaction_confirmed(
                        tx, daily, account_name=account_name
                    )
                    await self._client.send_text(
                        chat_id=chat_id,
                        text=conf_text,
                        reply_markup=conf_markup,
                        reply_to_message_id=message_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    await self._client.send_text(
                        chat_id=chat_id,
                        text=f"⚠️ İşlem kaydedilemedi: {html.escape(str(exc))}",
                        reply_to_message_id=message_id,
                    )
                return

            await self._client.send_text(
                chat_id=chat_id,
                text="⚠️ Geçersiz tutar girdiniz. Lütfen tutarı girin (Örn: <code>5000</code> veya <code>5000 source_a</code>) veya iptal etmek için <code>vazgeç</code> yazın:",
                reply_to_message_id=message_id,
            )
            return

        quick_cmd = parse_quick_accounting_command(text)
        if quick_cmd is not None:
            try:
                user_id = self._resolve_user_id(chat_id)
                account_id = None
                account_name = None
                note = quick_cmd.note
                if user_id is not None and note:
                    acc = self._accounting_service.resolve_account(user_id=user_id, text=note)
                    if acc is not None:
                        account_id = acc.id
                        account_name = acc.account_name

                tx = self._record_transaction(
                    chat_id=chat_id,
                    tx_type=quick_cmd.tx_type,
                    amount=quick_cmd.amount_str,
                    raw_date=quick_cmd.raw_date,
                    note=note,
                    account_id=account_id,
                    user_id=user_id,
                )
                daily = self._get_daily_summary(
                    chat_id=chat_id, date_str=tx.tx_date, user_id=user_id
                )
                conf_text, conf_markup = render_transaction_confirmed(
                    tx, daily, account_name=account_name
                )
                await self._client.send_text(
                    chat_id=chat_id,
                    text=conf_text,
                    reply_markup=conf_markup,
                    reply_to_message_id=message_id,
                )
            except Exception as exc:  # noqa: BLE001
                await self._client.send_text(
                    chat_id=chat_id,
                    text=f"⚠️ İşlem kaydedilemedi: {html.escape(str(exc))}",
                    reply_to_message_id=message_id,
                )
            return

        if text.startswith(("/favorilerim", "/favoriler", "/fav", "/favori")):
            fav_keys = self._repo.list_favorites(chat_id)
            if not fav_keys:
                no_fav_text = (
                    "⭐ <b>Favori Fırsatlarınız</b>\n\n"
                    "Henüz takip listesine eklenmiş bir maç bulunmuyor.\n"
                    "Fırsat kartlarının altındaki <b>[ 📌 Takibe Al ]</b> butonuna tıklayarak maçları buraya ekleyebilirsiniz."
                )
                await self._client.send_text(
                    chat_id=chat_id, text=no_fav_text, reply_to_message_id=message_id
                )
                return

            cards = [self._repo.get_card_by_key(k) for k in fav_keys]
            valid_cards = [c for c in cards if c is not None]

            if not valid_cards:
                await self._client.send_text(
                    chat_id=chat_id,
                    text="⭐ <b>Favori Fırsatlarınız</b>\n\nFavoriye aldığınız tüm fırsatlar kapandı veya süresi doldu.",
                    reply_to_message_id=message_id,
                )
                return

            msg_lines = [
                f"⭐ <b>Favori Fırsatlarınız ({len(valid_cards)} Maç)</b>\n",
            ]
            keyboard: list[list[dict[str, str]]] = []
            for idx, c in enumerate(valid_cards, 1):
                opp = c.evaluation.selected
                status_str = "🟢 Aktif" if c.status is not LifecycleStatus.CLOSED else "🔴 Kapandı"
                odds_parts = [
                    f"{_outcome_label(o)}: {_SOURCE_NAMES.get(s.source, s.source.value)} @{s.odds:.2f}"
                    for o, s in opp.selections.items()
                ]
                market_str = opp.market_label or (
                    "1X2" if opp.market_type is MarketType.FULL_TIME_RESULT else "2.5 Alt/Üst"
                )
                msg_lines.append(
                    f"{idx}️⃣ <b>{html.escape(opp.home)} - {html.escape(opp.away)}</b> ({market_str})\n"
                    f"• Oranlar: {' · '.join(odds_parts)}\n"
                    f"• Kâr: <b>%{format_bps(c.current_profit_bps)}</b> · Durum: {status_str}\n"
                    f"• <i>{html.escape(opp.country_name or '')} / {html.escape(opp.league_name or opp.competition)}</i>\n"
                )
                keyboard.append(
                    [
                        {
                            "text": f"🧮 {opp.home[:10]} - {opp.away[:10]}",
                            "callback_data": f"calc:{c.card_key}",
                        },
                        {
                            "text": "🔄 Kontrol Et",
                            "callback_data": f"refresh:{c.card_key}",
                        },
                    ]
                )

            await self._client.send_text(
                chat_id=chat_id,
                text="\n".join(msg_lines),
                reply_markup={"inline_keyboard": keyboard} if keyboard else None,
                reply_to_message_id=message_id,
            )
            return

        hedging_cmd = parse_hedging_command(text)
        if hedging_cmd is not None:
            card_state = None
            reply_to = msg.get("reply_to_message")
            if reply_to and "message_id" in reply_to:
                card_state = self._repo.get_card_by_message_id(chat_id, int(reply_to["message_id"]))

            if not card_state:
                card_state = self._repo.get_latest_card_for_chat(chat_id)

            if not card_state:
                await self._client.send_text(
                    chat_id=chat_id,
                    text=(
                        "⚠️ Kurtarma yapılacak aktif bir maç kartı bulunamadı.\n"
                        "Lütfen bir fırsat kartına <b>Yanıt (Reply)</b> vererek yazın:\n"
                        "• <code>kurtar ms1 3.10 6000</code>"
                    ),
                    reply_to_message_id=message_id,
                )
                return

            opp = card_state.evaluation.selected
            opp_selections_by_outcome = {
                to_domain_outcome(out_k): sel for out_k, sel in opp.selections.items()
            }

            resolved_fixed_legs: dict[Outcome | TwoWayOutcome, FixedLeg] = {}
            for fl_input in hedging_cmd.fixed_legs:
                f_out = resolve_outcome(opp, fl_input.outcome_raw)
                sel = opp_selections_by_outcome.get(f_out)
                f_source = sel.source if sel else Source.SOURCE_A
                resolved_fixed_legs[f_out] = FixedLeg(
                    outcome=f_out,
                    odds=fl_input.odds,
                    stake_cents=fl_input.stake_cents,
                    source=f_source,
                )

            counter_odds: dict[Outcome | TwoWayOutcome, Decimal] = {}
            counter_sources: dict[Outcome | TwoWayOutcome, Source] = {}

            for dom_out, sel in opp_selections_by_outcome.items():
                if dom_out not in resolved_fixed_legs:
                    override_val: Decimal | None = None
                    for o_key, o_val in hedging_cmd.counter_odds_override.items():
                        if resolve_outcome(opp, o_key) == dom_out:
                            override_val = o_val
                            break
                    counter_odds[dom_out] = override_val if override_val is not None else sel.odds
                    counter_sources[dom_out] = sel.source

            try:
                hedging_result = calculate_hedging_allocation(
                    fixed_legs=resolved_fixed_legs,
                    counter_odds=counter_odds,
                    counter_sources=counter_sources,
                    apply_cashback=True,
                )
                resp_text, markup = format_hedging_response(opp, hedging_result)
                await self._client.send_text(
                    chat_id=chat_id,
                    text=resp_text,
                    reply_markup=markup,
                    reply_to_message_id=message_id,
                )
            except Exception as exc:
                logger.exception("Error executing hedging calculation")
                await self._client.send_text(
                    chat_id=chat_id,
                    text=f"⚠️ Kurtarma hesabı yapılamadı: {html.escape(str(exc))}",
                    reply_to_message_id=message_id,
                )
            return

        parsed = parse_calculator_command(text)
        if not parsed:
            return

        card_state = None
        reply_to = msg.get("reply_to_message")
        if reply_to and "message_id" in reply_to:
            card_state = self._repo.get_card_by_message_id(chat_id, int(reply_to["message_id"]))

        if not card_state:
            # Fallback to the latest opportunity card received in this chat
            card_state = self._repo.get_latest_card_for_chat(chat_id)

        if not card_state:
            await self._client.send_text(
                chat_id=chat_id,
                text="⚠️ Hesaplanacak aktif bir maç kartı bulunamadı. Lütfen bir fırsat kartına yanıt verin.",
                reply_to_message_id=message_id,
            )
            return

        opp = card_state.evaluation.selected
        odds = {to_domain_outcome(out): s.odds for out, s in opp.selections.items()}
        sources = {to_domain_outcome(out): s.source for out, s in opp.selections.items()}
        if parsed.is_budget:
            result = calculate_budget_allocation(
                total_budget_cents=parsed.amount_cents,
                odds=odds,
                sources=sources,
            )
        else:
            outcome = resolve_outcome(opp, parsed.outcome_raw)
            result = calculate_fixed_stake_allocation(
                fixed_outcome=outcome,
                fixed_stake_cents=parsed.amount_cents,
                odds=odds,
                sources=sources,
            )

        resp_text, markup = format_allocation_response(opp, result)
        await self._client.send_text(
            chat_id=chat_id,
            text=resp_text,
            reply_markup=markup,
            reply_to_message_id=message_id,
        )
