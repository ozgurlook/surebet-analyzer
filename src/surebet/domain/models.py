import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from surebet.money import payout_cents, validate_odds

from .enums import MarketType, OpportunityClass, Outcome, QuoteStatus, Source, TwoWayOutcome

MoneyCents = Annotated[int, Field(ge=0, strict=True)]
PositiveMoneyCents = Annotated[int, Field(gt=0, strict=True)]
SignedCents = Annotated[int, Field(strict=True)]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


_AWARE_ISO_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def _datetime_input(value: object) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str):
        if _AWARE_ISO_DATETIME.fullmatch(value) is None:
            raise ValueError("datetime must use strict ISO timezone-aware syntax")
        try:
            return _aware(datetime.fromisoformat(value))
        except (TypeError, ValueError) as error:
            raise ValueError("datetime must be an ISO timezone-aware datetime") from error
    raise ValueError("datetime must be a timezone-aware datetime or ISO string")


def _nonempty(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must be nonempty")
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _FrozenMapping[K, V](Mapping[K, V]):
    """An immutable Mapping backed by a private defensive copy."""

    __slots__ = ("_data",)
    _data: Mapping[K, V]

    def __init__(self, values: Mapping[K, V]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("mapping is immutable")

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        memo[id(self)] = self
        return self


class Quote(DomainModel):
    source: Source
    event_id: StrictStr
    outcome: Outcome | TwoWayOutcome
    odds: Decimal
    observed_at: datetime
    status: QuoteStatus = QuoteStatus.OPEN
    market_type: MarketType = MarketType.FULL_TIME_RESULT
    line: Decimal | None = None

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        return _nonempty(value, "event_id")

    @field_validator("line", mode="before")
    @classmethod
    def validate_line(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception as error:
            raise ValueError("line must be a valid decimal") from error

    @field_validator("odds", mode="before")
    @classmethod
    def validate_odds(cls, value: object) -> Decimal:
        if not isinstance(value, (str, Decimal)):
            raise ValueError("odds must be a string or Decimal")  # noqa: TRY004
        try:
            odds = Decimal(value)
        except Exception as error:
            raise ValueError("odds must be a valid decimal") from error
        return validate_odds(odds)

    _parse_observed_at = field_validator("observed_at", mode="before")(_datetime_input)
    _validate_observed_at = field_validator("observed_at")(_aware)


class BonusState(DomainModel):
    loss_before_cents: MoneyCents
    net_deposit_cents: MoneyCents
    period_anchor: datetime
    withdrawals_exceed_deposits: StrictBool = False
    unrelated_open_bets: StrictBool = False
    requirements_confirmed: StrictBool = True

    _parse_period_anchor = field_validator("period_anchor", mode="before")(_datetime_input)
    _validate_period_anchor = field_validator("period_anchor")(_aware)


class SourceAccount(DomainModel):
    balance_cents: MoneyCents
    min_stake_cents: PositiveMoneyCents = 100
    max_stake_cents: PositiveMoneyCents
    max_payout_cents: MoneyCents | None = None
    bonus: BonusState | None = None

    @model_validator(mode="after")
    def validate_stake_limits(self) -> "SourceAccount":
        if self.max_stake_cents < self.min_stake_cents:
            raise ValueError("max_stake_cents must be at least min_stake_cents")
        return self


class AnalysisRequest(DomainModel):
    budget_cents: PositiveMoneyCents
    now: datetime
    bonus_evaluation_at: datetime | None = None
    stale_after_seconds: Annotated[int, Field(default=75, ge=30, strict=True)]
    accounts: Mapping[Source, SourceAccount]

    _parse_now = field_validator("now", mode="before")(_datetime_input)
    _validate_now = field_validator("now")(_aware)
    _parse_bonus_evaluation_at = field_validator("bonus_evaluation_at", mode="before")(
        lambda value: None if value is None else _datetime_input(value)
    )
    _validate_bonus_evaluation_at = field_validator("bonus_evaluation_at")(
        lambda value: None if value is None else _aware(value)
    )

    @field_validator("accounts")
    @classmethod
    def validate_accounts(
        cls, value: Mapping[Source, SourceAccount]
    ) -> Mapping[Source, SourceAccount]:
        core_sources = {Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C}
        if not (core_sources.issubset(set(value)) and set(value).issubset(set(Source))):
            raise ValueError("accounts must contain at least Source A, Source B, and Source C")
        return _FrozenMapping(dict(value))

    @field_serializer("accounts")
    def serialize_accounts(
        self, value: Mapping[Source, SourceAccount]
    ) -> dict[Source, SourceAccount]:
        return dict(value)

    @model_validator(mode="after")
    def validate_bonus_sources(self) -> Self:
        if self.accounts[Source.SOURCE_A].bonus is not None:
            raise ValueError("Source A cannot have a bonus state")
        if Source.SOURCE_D in self.accounts and self.accounts[Source.SOURCE_D].bonus is not None:
            raise ValueError("Source D cannot have a bonus state")
        if self.bonus_evaluation_at is not None and self.bonus_evaluation_at.astimezone(
            UTC
        ) < self.now.astimezone(UTC):
            raise ValueError("bonus_evaluation_at must not be earlier than now")
        return self


class Allocation(DomainModel):
    stakes_cents: Mapping[Outcome, MoneyCents]

    @field_validator("stakes_cents")
    @classmethod
    def validate_stakes(cls, value: Mapping[Outcome, int]) -> Mapping[Outcome, int]:
        if set(value) != set(Outcome):
            raise ValueError("stakes_cents must contain exactly HOME, DRAW, and AWAY")
        return _FrozenMapping(value)

    @field_serializer("stakes_cents")
    def serialize_stakes(self, value: Mapping[Outcome, int]) -> dict[Outcome, int]:
        return dict(value)


class ScenarioResult(DomainModel):
    outcome: Outcome
    gross_return_cents: MoneyCents
    standard_profit_cents: SignedCents
    bonus_delta_cents: SignedCents
    adjusted_profit_cents: SignedCents


class Opportunity(DomainModel):
    classification: OpportunityClass
    quotes: Mapping[Outcome, Quote]
    allocation: Allocation
    scenarios: tuple[ScenarioResult, ...]
    minimum_standard_profit_cents: SignedCents
    minimum_adjusted_profit_cents: SignedCents

    @field_validator("quotes")
    @classmethod
    def validate_quotes(cls, value: Mapping[Outcome, Quote]) -> Mapping[Outcome, Quote]:
        if set(value) != set(Outcome):
            raise ValueError("quotes must contain exactly HOME, DRAW, and AWAY")
        return _FrozenMapping(value)

    @field_serializer("quotes")
    def serialize_quotes(self, value: Mapping[Outcome, Quote]) -> dict[Outcome, Quote]:
        return dict(value)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if any(outcome is not quote.outcome for outcome, quote in self.quotes.items()):
            raise ValueError("quote keys must match Quote.outcome")
        if len({quote.event_id for quote in self.quotes.values()}) != 1:
            raise ValueError("all quotes must belong to the same event")

        scenarios = {scenario.outcome: scenario for scenario in self.scenarios}
        if set(scenarios) != set(Outcome) or len(scenarios) != len(self.scenarios):
            raise ValueError("scenarios must contain exactly one result per outcome")

        total_stake = sum(self.allocation.stakes_cents.values())
        for outcome in Outcome:
            scenario = scenarios[outcome]
            gross_return = payout_cents(
                self.allocation.stakes_cents[outcome], self.quotes[outcome].odds
            )
            if scenario.gross_return_cents != gross_return:
                raise ValueError("scenario gross return does not match conservative payout")
            standard_profit = gross_return - total_stake
            if scenario.standard_profit_cents != standard_profit:
                raise ValueError("scenario standard profit does not match allocation")
            if (
                scenario.adjusted_profit_cents
                != scenario.standard_profit_cents + scenario.bonus_delta_cents
            ):
                raise ValueError(
                    "scenario adjusted profit does not match standard profit and bonus delta"
                )

        minimum_standard = min(scenario.standard_profit_cents for scenario in scenarios.values())
        minimum_adjusted = min(scenario.adjusted_profit_cents for scenario in scenarios.values())
        if self.minimum_standard_profit_cents != minimum_standard:
            raise ValueError("declared minimum standard profit is incorrect")
        if self.minimum_adjusted_profit_cents != minimum_adjusted:
            raise ValueError("declared minimum adjusted profit is incorrect")
        if (
            self.classification is OpportunityClass.STANDARD
            and self.minimum_standard_profit_cents <= 0
        ):
            raise ValueError("standard opportunities require positive standard profit")
        if self.classification in (
            OpportunityClass.BONUS_QUALIFIED,
            OpportunityClass.BONUS_CONDITIONAL,
        ) and (self.minimum_standard_profit_cents > 0 or self.minimum_adjusted_profit_cents <= 0):
            raise ValueError(
                "bonus opportunities require nonpositive standard and positive adjusted profit"
            )
        if self.classification is OpportunityClass.NOT_PROFITABLE and (
            self.minimum_standard_profit_cents > 0 or self.minimum_adjusted_profit_cents > 0
        ):
            raise ValueError(
                "not profitable opportunities require nonpositive standard and adjusted profit"
            )
        return self
