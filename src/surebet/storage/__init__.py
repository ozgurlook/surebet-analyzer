"""Storage and persistence layer using SQLite in WAL mode."""

from .auth import AuthRepository, SessionData
from .db import get_connection, init_db
from .settings import (
    SystemSettings,
    SystemSettingsRepository,
    SystemSettingsUpdate,
    UserPreferences,
    UserPreferencesRepository,
    UserPreferencesUpdate,
)
from .snapshots import SnapshotRepository, SourceHealth, SourceHealthStatus
from .taxonomy import TaxonomyConflict, TaxonomyRepository

__all__ = [
    "AuthRepository",
    "SessionData",
    "SnapshotRepository",
    "SourceHealth",
    "SourceHealthStatus",
    "SystemSettings",
    "SystemSettingsRepository",
    "SystemSettingsUpdate",
    "TaxonomyConflict",
    "TaxonomyRepository",
    "UserPreferences",
    "UserPreferencesRepository",
    "UserPreferencesUpdate",
    "get_connection",
    "init_db",
]
