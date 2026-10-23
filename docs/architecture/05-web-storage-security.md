# Web, Storage & Security Subsystem Architecture

> **Subsystem Guide 05 of 05**  
> Target Modules: [`src/surebet/web/`](../../src/surebet/web/), [`src/surebet/storage/`](../../src/surebet/storage/), [`frontend/`](../../frontend/)  
> Authoritative Master Rules: [`AGENTS.md`](../../AGENTS.md)

---

## 1. System Overview & Safety Boundary

The **Web, Storage & Security Subsystem** provides the analytical user interface, administrative taxonomy management controls, persistent multi-user identity storage, and transaction-safe data persistence for `surebet-analyzer`.

```
+-------------------------------------------------------------------------+
|                      Mobile PWA (React 18 + TS)                         |
|     [Opportunities]  [Live Dashboard]  [Taxonomy Admin]  [Settings]     |
+------------------------------------+------------------------------------+
                                     | HTTPS + Cookies (HttpOnly Session)
                                     v + Header (X-CSRF-Token)
+-------------------------------------------------------------------------+
|                   FastAPI Application (src/surebet/web/)                |
|  +-------------------------------------------------------------------+  |
|  |   Auth & Rate Limiting Middleware (Argon2id + Client IP Limiter)  |  |
|  +---------------------------------+---------------------------------+  |
|                                    | Dependency Injection                |
|  +---------------------------------v---------------------------------+  |
|  |  REST Routers: /auth, /opportunities, /mappings, /taxonomy, etc.  |  |
|  +---------------------------------+---------------------------------+  |
|                                    | Background Workers                  |
|  +---------------------------------v---------------------------------+  |
|  |     CollectorRunner (Ingestion) & NotificationDispatcher (Outbox) |  |
|  +---------------------------------+---------------------------------+  |
+------------------------------------+------------------------------------+
                                     | WAL Transactions (get_connection)
                                     v
+-------------------------------------------------------------------------+
|                       SQLite Database (WAL Mode)                        |
|   [Users/Sessions] [Taxonomy/Mappings] [Outbox] [Settings/Snapshots]    |
+-------------------------------------------------------------------------+
```

### ZERO-BET INVARIANT (Strict Read-Only Enforcement)

The web application and its API layer are strictly **read-only analytical aggregation and system management consoles**.
- Under no circumstances should the web UI, REST API, or background tasks place bets, submit betting slips, authenticate with bookmaker betting accounts, or process monetary transactions.
- All monetary inputs in preferences (e.g. `default_budget_cents`) and opportunity queries are strictly analytical parameters for discrete stake calculation.

---

## 2. FastAPI Backend Architecture

The backend is built with modern FastAPI (Python 3.12) leveraging asynchronous request handling, typed Pydantic models, and strict dependency injection.

### 2.1 Application Lifecycle & State (`lifespan`)

The application lifecycle is managed via an asynchronous lifespan context manager in [`src/surebet/web/app.py`](../../src/surebet/web/app.py):
1. **Startup:**
   - Initializes database schema and runs pending migrations via `run_migrations(db_path)`.
   - Initializes singleton storage repositories (`AuthRepository`, `AuthRateLimitRepository`, `TaxonomyRepository`, `SystemSettingsRepository`, `NotificationRepository`).
   - Attaches repositories and services to `app.state`.
   - Starts background tasks (`CollectorRunner` for market ingestion and `NotificationDispatcher` for Telegram outbox dispatching).
2. **Shutdown:**
   - Gracefully cancels background workers and waits for active collection loops to terminate cleanly.
   - Closes active network connections.

Source endpoints are not part of the stored settings: collectors read them from `SUREBET_<SOURCE>_*` environment variables (see [03-collectors-and-protocols.md](03-collectors-and-protocols.md#5-endpoint-configuration--demo-mode)). `surebet-web serve --demo` swaps in the offline demo collectors and seeds a `demo` login instead.

### 2.2 Dependency Injection Pattern

FastAPI route handlers never create unmanaged connections or instantiate repositories directly. Dependencies are injected via `Depends()`:

```python
# Authentication & CSRF Dependencies (src/surebet/web/auth_middleware.py)
@router.post("/api/v1/mappings/{source}/{source_competition_id}/approve")
def approve_mapping(
    source: str,
    source_competition_id: str,
    request_data: ApproveRequest,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _: Annotated[None, Depends(require_csrf_token)],
    taxonomy_repo: Annotated[TaxonomyRepository, Depends(get_taxonomy_repo)],
) -> MappingItem:
    # Handler code with an authenticated session and verified CSRF token
    ...
```

### 2.3 Router Modules

| Router Module | Mount Path | Primary Responsibility | Authentication |
|---|---|---|---|
| [`routes/auth.py`](../../src/surebet/web/routes/auth.py) | `/api/v1/auth` | User registration, login, logout, current session inspection | Public (Rate Limited) / Session |
| [`routes/opportunities.py`](../../src/surebet/web/routes/opportunities.py) | `/api/v1/opportunities` | Real-time arbitrage query, filtering by margin, market type, bookmakers | Session Required |
| [`routes/events.py`](../../src/surebet/web/routes/events.py) | `/api/v1/events` | Cross-bookmaker matched events and raw odd ingestion status | Session Required |
| [`routes/taxonomy.py`](../../src/surebet/web/routes/taxonomy.py) | `/api/v1/taxonomy` | Canonical regions and leagues catalog with event counts | Session Required |
| [`routes/mappings.py`](../../src/surebet/web/routes/mappings.py) | `/api/v1/mappings` | Operator curation workflow (approve, link, create, rename, delete) | Session + CSRF Required |
| [`routes/notifications.py`](../../src/surebet/web/routes/notifications.py) | `/api/v1/notifications` | Telegram chat registrations, user alert thresholds, mute rules | Session + CSRF Required |
| [`routes/preferences.py`](../../src/surebet/web/routes/preferences.py) | `/api/v1/preferences` | User calculation defaults (default budget, Source A policy inclusion) | Session + CSRF Required |
| [`routes/settings.py`](../../src/surebet/web/routes/settings.py) | `/api/v1/settings` | System-wide refresh intervals and staleness limits | Session + CSRF Required |
| [`routes/sources.py`](../../src/surebet/web/routes/sources.py) | `/api/v1/sources` | Live bookmaker collector health status, last errors (including unconfigured endpoints), latencies, manual refresh | Session Required |
| [`routes/dashboard.py`](../../src/surebet/web/routes/dashboard.py) | `/api/v1/dashboard` | Aggregated metrics for live opportunity summaries | Session Required |

---

## 3. SQLite Concurrency, WAL Pragmas & Migrations

Persistent storage uses SQLite with Write-Ahead Logging (WAL) enabled to support concurrent readers alongside active collector and notification writer threads.

### 3.1 Connection Management & WAL Pragmas

All database access throughout the entire application **must** use the official connection context manager defined in [`src/surebet/storage/db.py`](../../src/surebet/storage/db.py):

```python
from surebet.storage.db import get_connection

with get_connection(db_path) as conn:
    conn.execute("SELECT ...")
```

Under the hood, `get_connection()` sets critical runtime pragmas and manages transactions:
```python
@contextmanager
def get_connection(db_path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(Path(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -32000;")  # 32MB page cache
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- **`PRAGMA journal_mode=WAL;`**: Enabled at database creation. Writers append changes to the `-wal` file without blocking concurrent readers.
- **`PRAGMA synchronous=NORMAL;`**: Provides durability against application crashes while eliminating synchronous disk flushes on every commit, boosting write throughput by orders of magnitude.
- **`PRAGMA foreign_keys=ON;`**: Enforces relational integrity across parent/child tables (e.g. cascading deletes for user sessions and preferences).
- **`timeout=10.0`**: Sets a 10-second busy timeout to gracefully wait during brief write lock contention.

### 3.2 Schema Migrations

The database migration engine in `src/surebet/storage/db.py` ensures deterministic schema evolution across upgrades:
- Tracks current migration state in `schema_migrations` table (`version: int`, `applied_at: text`).
- Executes sequential migration functions in order:
  1. `_migration_001_initial_schema`: Core tables (`users`, `sessions`, `user_preferences`, `system_settings`, `auth_rate_limit_attempts`).
  2. `_migration_002_taxonomy_tables`: Canonical taxonomy, regions, competitions, mapping queues, and audit log.
  3. `_migration_003_notifications_outbox`: Transactional outbox table (`notification_outbox`) and card state tracking (`telegram_opportunity_cards`).
  4. `_migration_004_sunset_old_notifications`: Deprecation of legacy state tables.
- Each migration runs in an isolated transaction. If any step fails, the transaction rolls back cleanly.

---

## 4. Security, Identity & Authentication Architecture

`surebet-analyzer` implements a defense-in-depth security model designed for multi-user analytical decision support.

```
Incoming Request
       |
       v
[Client IP Extractor] ---> [Auth Rate Limiter (Rolling 15-min Window)]
       | (Allowed)
       v
[Cookie Session Validator] ---> Validates 32-byte secret in `sessions` table
       | (Authenticated)
       v
Is Mutating Request (POST/PUT/PATCH/DELETE)?
       |-- Yes ---> [CSRF Guard] ---> Checks Header `X-CSRF-Token` == `session.csrf_token`
       +-- No  ---> [Route Handler]
```

### 4.1 Argon2id Password Hashing

Passwords are never stored in plaintext or weak hashes (MD5, SHA, Bcrypt).
- **Algorithm:** Argon2id via `argon2-cffi` ([`src/surebet/storage/auth.py`](../../src/surebet/storage/auth.py)).
- **Parameters:** Memory-hard cryptographic hashing resistant to GPU/ASIC cracking and side-channel timing attacks.
- **Validation Rules:**
  - Username: 3 to 24 characters, normalized using NFKD and lowercase folding (`username_key`), alphanumeric plus `_`, `-`, `.`.
  - Password: 8 to 128 characters.

### 4.2 Cookie-Based Session Management

- Sessions use cryptographically random 32-byte tokens generated via `secrets.token_urlsafe(32)`.
- Stored in SQLite `sessions` table with expiration timestamp (`SESSION_DURATION = timedelta(days=7)`).
- Sent to browser as an `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` (in production) cookie (`session_token`).
- Inactive user accounts immediately invalidate all active sessions upon lookup.

### 4.3 Double-Submit Cookie & Header CSRF Defense

To protect authenticated users against Cross-Site Request Forgery (CSRF):
1. Upon session creation, a unique `csrf_token` (`secrets.token_hex(16)`) is stored with the session in SQLite and exposed to the frontend via the `/api/v1/auth/me` endpoint.
2. For all state-changing HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`), [`src/surebet/web/auth_middleware.py`](../../src/surebet/web/auth_middleware.py) enforces:
   ```python
   def require_csrf_token(
       session: Annotated[SessionData, Depends(require_authenticated_session)],
       x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
   ) -> None:
       if not x_csrf_token or x_csrf_token != session.csrf_token:
           raise HTTPException(
               status_code=status.HTTP_403_FORBIDDEN,
               detail="Invalid or missing CSRF token",
           )
   ```
3. Read-only requests (`GET`, `HEAD`, `OPTIONS`) do not require the CSRF token.

### 4.4 Per-IP Authentication Rate Limiting

To eliminate brute-force and credential stuffing attacks on authentication endpoints:
- **Repository:** [`src/surebet/storage/auth_rate_limit.py`](../../src/surebet/storage/auth_rate_limit.py).
- **Parameters:** Maximum 5 failed attempts (`MAX_ATTEMPTS = 5`) within a rolling 15-minute window (`WINDOW = timedelta(minutes=15)`).
- **Storage:** Logged in `auth_rate_limit_attempts` table recording `client_ip`, `action` (`login` or `register`), and `attempted_at`.
- **IP Extraction:** Client IP is extracted directly from the reliable socket peer IP (`request.client.host`), never blindly trusting spoofable `X-Forwarded-For` headers without an explicit trusted reverse proxy configuration.
- **Response:** If exceeded, returns HTTP 429 Too Many Requests with a `Retry-After` header indicating exact seconds until the earliest failed attempt exits the rolling window.

---

## 5. Mobile-First React PWA Frontend

The user interface is a responsive Single Page Application (SPA) designed as a Progressive Web App (PWA) under [`frontend/`](../../frontend/).

### 5.1 Technology Stack
- **Framework:** React 18 with TypeScript.
- **Styling:** Tailwind CSS with responsive, mobile-first utility classes and dark mode styling.
- **Build Tool:** Vite for sub-second hot-module replacement and optimized production builds.
- **State Management:** TanStack Query (React Query v5) for server state caching, background refetching, and cache invalidation.

### 5.2 Key Frontend Modules & Features

```
frontend/src/
+-- api/             # Typed API client functions (Fetch wrappers with credentials & CSRF)
+-- components/      # Shared reusable UI primitives (Modal, Button, Badge, Card)
+-- features/        # Domain-driven feature components:
|   +-- auth/        # Login, registration, and session provider
|   +-- opportunities/ # Live surebet tables, stake distribution calculator
|   +-- taxonomy/    # Review queue, league linking dialogs, canonical management
|   +-- notifications/ # User alert thresholds, Telegram channel management
|   +-- settings/    # System refresh rates, bookmaker source monitors
+-- pages/           # Top-level view routes (Dashboard, Opportunities, Taxonomy, Settings)
+-- utils/           # Formatters (currency, basis points to %, date/time)
```

### 5.3 Mobile & PWA Capabilities
- **Responsive Navigation:** Bottom navigation bar for mobile smartphones, transitioning to sidebar navigation on desktop screens.
- **PWA Manifest & Service Worker:** Enables "Add to Home Screen" on iOS and Android devices.
- **Offline & Stale Indicators:** Visual indicators notify the user immediately if WebSocket ingestion or backend polling encounters network disconnection.
- **Optimistic UI:** Taxonomy curation actions (approve, link) update the UI immediately while synchronizing with the backend in the background.

---

## 6. Subsystem File Map & Key Classes

| File Path | Component / Layer | Key Classes & Functions | Test Coverage |
|---|---|---|---|
| [`src/surebet/web/app.py`](../../src/surebet/web/app.py) | FastAPI App Setup | `create_app`, `lifespan`, route mounting, exception handlers | [`tests/unit/web/`](../../tests/unit/web/) |
| [`src/surebet/web/auth_middleware.py`](../../src/surebet/web/auth_middleware.py) | Security Middleware | `require_authenticated_session`, `require_csrf_token`, `get_current_session` | [`tests/unit/web/test_api_auth.py`](../../tests/unit/web/test_api_auth.py) |
| [`src/surebet/web/collector_runner.py`](../../src/surebet/web/collector_runner.py) | Background Ingestion | `CollectorRunner`, `run_collector_loop` | [`tests/unit/web/test_collector_runner.py`](../../tests/unit/web/test_collector_runner.py) |
| [`src/surebet/storage/db.py`](../../src/surebet/storage/db.py) | SQLite Engine | `get_connection`, `run_migrations`, schema definitions | Tested across all storage tests |
| [`src/surebet/storage/auth.py`](../../src/surebet/storage/auth.py) | Identity Storage | `AuthRepository`, `UserRecord`, `SessionData`, Argon2id hashing | [`tests/unit/storage/test_auth.py`](../../tests/unit/storage/test_auth.py) |
| [`src/surebet/storage/auth_rate_limit.py`](../../src/surebet/storage/auth_rate_limit.py) | Rate Limiting Storage | `AuthRateLimitRepository`, `RateLimitDecision`, `AuthAction` | [`tests/unit/storage/test_auth_rate_limit.py`](../../tests/unit/storage/test_auth_rate_limit.py) |
| [`src/surebet/web/routes/auth.py`](../../src/surebet/web/routes/auth.py) | Auth Endpoints | `/login`, `/register`, `/logout`, `/me` | [`tests/unit/web/test_api_auth.py`](../../tests/unit/web/test_api_auth.py) |
| [`src/surebet/web/routes/opportunities.py`](../../src/surebet/web/routes/opportunities.py) | Opportunity Endpoints | `/api/v1/opportunities` (filter by margin, market, operator) | [`tests/unit/web/test_api_opportunities.py`](../../tests/unit/web/test_api_opportunities.py) |
| [`src/surebet/web/routes/mappings.py`](../../src/surebet/web/routes/mappings.py) | Taxonomy Admin Endpoints | `/approve`, `/link`, `/league`, `/rename` | [`tests/unit/web/test_api_taxonomy.py`](../../tests/unit/web/test_api_taxonomy.py) |
| [`src/surebet/web/routes/sources.py`](../../src/surebet/web/routes/sources.py) | Source Health Endpoints | `/api/v1/sources` | [`tests/unit/web/test_api_sources.py`](../../tests/unit/web/test_api_sources.py) |
| [`frontend/src/App.tsx`](../../frontend/src/App.tsx) | React App Root | Routing, TanStack Query client, theme providers | [`frontend/src/App.test.tsx`](../../frontend/src/App.test.tsx) |
| [`frontend/src/api/client.ts`](../../frontend/src/api/client.ts) | Frontend API Client | Fetch interceptors for credentials and `X-CSRF-Token` | Frontend unit tests |
