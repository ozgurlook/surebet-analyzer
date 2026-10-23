"""Multi-source event matching and normalization."""

from .matcher import match_events
from .models import MatchedEvent
from .normalizer import normalize_competition, normalize_team_name

__all__ = [
    "MatchedEvent",
    "match_events",
    "normalize_competition",
    "normalize_team_name",
]
