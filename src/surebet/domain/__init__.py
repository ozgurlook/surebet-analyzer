"""Immutable domain contracts for analysis."""

from .enums import MarketType, OpportunityClass, Outcome, QuoteStatus, Source, TwoWayOutcome
from .models import (
    Allocation,
    AnalysisRequest,
    BonusState,
    Opportunity,
    Quote,
    ScenarioResult,
    SourceAccount,
)

__all__ = [
    "Allocation",
    "AnalysisRequest",
    "BonusState",
    "MarketType",
    "Opportunity",
    "OpportunityClass",
    "Outcome",
    "Quote",
    "QuoteStatus",
    "ScenarioResult",
    "Source",
    "SourceAccount",
    "TwoWayOutcome",
]
