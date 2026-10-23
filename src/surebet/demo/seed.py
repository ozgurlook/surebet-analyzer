"""Demo account bootstrap.

Demo mode serves synthetic data only. The `demo` login gets a fresh random
password on every start, printed to the console, so no credential lives in the
repository.
"""

import secrets
from pathlib import Path

from surebet.storage.auth import AuthRepository, DuplicateUsernameError
from surebet.storage.db import init_db
from surebet.storage.settings import UserPreferencesRepository, UserPreferencesUpdate

DEMO_USERNAME = "demo"
DEMO_BUDGET_CENTS = 1_000_000


def seed_demo_account(db_path: Path | str) -> str:
    """Create the demo login, or reset its password, and return the new password."""

    init_db(db_path)
    password = secrets.token_urlsafe(12)
    auth = AuthRepository(db_path)
    try:
        user, _session = auth.register_user(DEMO_USERNAME, password)
    except DuplicateUsernameError:
        auth.set_password(DEMO_USERNAME, password)
        return password
    UserPreferencesRepository(db_path).update(
        user.user_id, UserPreferencesUpdate(default_budget_cents=DEMO_BUDGET_CENTS)
    )
    return password
