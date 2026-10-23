# Universal AI Agent Guidelines (`AGENTS.md`)

> **Single Source of Truth for AI Assistants**  
> Applicable to Claude Code, OpenAI Codex, Google Antigravity, Cursor, Windsurf, and any automated engineering agent working in this repository.

---

## 1. System Identity & Safety Mission

`surebet-analyzer` is a high-performance sports arbitrage calculation platform and mobile Progressive Web App (PWA). It continuously monitors real-time market data across multiple bookmakers, normalizes taxonomy (leagues, teams, markets), computes arbitrage opportunities from the published odds, optimizes stake distributions under bookmaker-specific constraints, and dispatches multi-tenant real-time Telegram alerts.

### ⚠️ ZERO-BET INVARIANT (Strict Read-Only Policy)

This application is strictly a **read-only analytical aggregator and decision-support calculator**.

Under **NO CIRCUMSTANCES** should any agent implement, suggest, or facilitate:
- Automated bet placement, wagering, or ticket submission.
- Authentication with private bookmaker betting accounts, scraping behind user credentials, or session hijacking.
- Financial transactions, automated deposit/withdrawal mechanics, or payment processing.
- Automated claiming of promotional bonuses, rollover farming, or free bet manipulation.

**Enforcement:** Any PR, prompt, or architectural alteration that violates this zero-bet boundary must be rejected immediately.

---

## 2. Hard Technical Invariants

All agents must strictly preserve the following architectural and mathematical invariants:

### 2.1 Integer Currency & Basis Points (No Floating-Point Money)
- **Zero Floating-Point Money:** Python `float` must **never** represent monetary values, bankrolls, stakes, or profit calculations.
- **Integer Cents / Kuruş:** All financial values are expressed in integer cents (`int`):
  - `budget_cents: int` (e.g., 1,000.00 TL = `100_000` cents)
  - `stake_cents: int`
  - `payout_cents: int`
- **Basis Points for Percentages:** All margins and percentages are represented in basis points (`profit_bps: int`), where `1 bps = 0.01% = 0.0001` (e.g., `250 bps = 2.50%`).
- **Odds & Conversions:** Bookmaker odds are validated and handled exclusively using `Decimal` (quantized to maximum 4 fractional digits) or `Fraction`. Refer to [`src/surebet/money.py`](src/surebet/money.py) for conversion helpers (`decimal_to_cents`, `cents_to_decimal`, `payout_cents`).

### 2.2 Strict Test-Driven Development (TDD)
- When adding new features or fixing bugs, **always write a failing test first** (`tests/`).
- Execute the test to verify red/failure on current logic.
- Implement the minimal code required to turn the test green.
- Verify that the entire test suite passes without regressions.

### 2.3 SQLite Concurrency & WAL Transactions
- Always access SQLite using the official context manager:
  ```python
  from surebet.storage.db import get_connection

  with get_connection(db_path) as conn:
      conn.execute("SELECT ...")
  ```
- **WAL Mode Enabled:** SQLite databases operate under Write-Ahead Logging (`PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=NORMAL;`).
- Never open raw unmanaged `sqlite3.connect()` calls in application, API, or worker logic. `get_connection` ensures transaction rollback on errors, commits on success, sets busy timeouts, and closes connections reliably.

---

## 3. Common Commands Cheat-Sheet

| Action | Command | Notes |
|---|---|---|
| **Run All Tests** | `uv run pytest -o pythonpath=.` | `-o pythonpath=.` is mandatory for package resolution |
| **Run Unit Tests** | `uv run pytest tests/unit/notifications/` | Targets specific subsystem |
| **Run Specific Test** | `uv run pytest tests/unit/arbitrage/test_solver.py -k test_name` | Fast test execution |
| **Lint Check** | `uv run ruff check .` | Ruff linter |
| **Lint Fix** | `uv run ruff check --fix .` | Automated fixes |
| **Code Formatting** | `uv run ruff format .` | Ruff code formatter |
| **Type Checking** | `uv run mypy src` | Strict type checking (mypy) |
| **Frontend Tests** | `cd frontend && npm test` | Vitest / Jest frontend test suite |
| **Frontend Build** | `cd frontend && npm run build` | Builds production React bundle |
| **Start Web Service** | `uv run --env-file .env surebet-web serve --db surebet.db --host 127.0.0.1 --port 8000` | Launches FastAPI and serves PWA |
| **Demo Mode** | `uv run surebet-web serve --demo --db demo.db` | Synthetic data, no network; login `demo`, password printed on start |
| **Preview Alerts** | `uv run preview-notifications --db surebet.db` | Notification card preview CLI |
| **Manual Analysis** | `uv run analyze-manual` | Interactive arbitrage inspection |

---

## 4. Directory Structure & Subsystem Architecture Map

The repository follows a modular architecture. For in-depth guides, consult the dedicated architecture documentation in `docs/architecture/`:

| Subsystem Path | Description & Responsibility | Deep Architecture Guide |
|---|---|---|
| `src/surebet/arbitrage/`<br>`src/surebet/opportunities/` | Mathematical formulation, Google OR-Tools CP-SAT solver, 2-way/3-way stake allocation, Source A dual-policy, cycle memoization | [01-arbitrage-and-solver.md](docs/architecture/01-arbitrage-and-solver.md) |
| `src/surebet/bonus/` | Loss-bonus & conditional cashback mathematical modeling | [01-arbitrage-and-solver.md](docs/architecture/01-arbitrage-and-solver.md) |
| `src/surebet/notifications/`<br>`src/surebet/storage/notifications.py` | Telegram notification engine, `LifecycleEngine` state machine, outbox queue, coarsened render fingerprints, rate limiter | [02-telegram-notifications.md](docs/architecture/02-telegram-notifications.md) |
| `src/surebet/sources/` | Protocol decoders (WebSocket JSON-RPC, WAMP v2, JSON over HTTPS), operator-supplied endpoints, demo collectors, health tracking | [03-collectors-and-protocols.md](docs/architecture/03-collectors-and-protocols.md) |
| `src/surebet/taxonomy/`<br>`src/surebet/matching/` | Cross-bookmaker entity resolution, league/team normalization, country dictionaries, fuzzy matching, tracked leagues | [04-taxonomy-and-matching.md](docs/architecture/04-taxonomy-and-matching.md) |
| `src/surebet/web/`<br>`src/surebet/storage/` | FastAPI REST backend, Argon2id auth, CSRF tokens, SQLite schema migrations & WAL management, rate limiting | [05-web-storage-security.md](docs/architecture/05-web-storage-security.md) |
| `frontend/` | Mobile-first React 18 PWA, TypeScript, Tailwind CSS, TanStack Query, offline indicators | [05-web-storage-security.md](docs/architecture/05-web-storage-security.md) |
| `src/surebet/domain/` | Core domain enums (`Outcome`, `MarketType`, `Source`) and immutable dataclass models | Shared Foundation |
| `src/surebet/money.py` | Currency validation, kuruş rounding, fractional odds scaling | Shared Foundation |

---

## 5. Code Style & Hygiene Rules

1. **Clean Git Commits (Conventional Commits):**
   - Use structured prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, `chore:`.
   - Examples:
     - `feat(arbitrage): support draw-only restriction in 3-way solver`
     - `fix(notifications): prevent outbox message storm on minor odds fluctuations`
     - `docs: add universal ai agent guidelines in AGENTS.md`
2. **Never Commit Secrets or Ephemeral Artifacts:**
   - Database files (`*.db`, `*.sqlite`, `*.sqlite3`, `*.wal`, `*.shm`)
   - Secret environment files (`.env`, `.env.*`)
   - Session keys, bot tokens, or API credentials
   - Temporary scratch scripts or build output folders (`dist/`, `build/`)
3. **No Placeholder Content:**
   - Avoid placeholder tokens like `TODO`, `TBD`, `FIXME`, or stub implementations.
   - All models, types, and functions must be fully realized with complete type signatures.
4. **Type Annotations & Python 3.12:**
   - Write standard modern Python (PEP 695 type parameters, union `X | Y`, built-in generics `list[str]`, `dict[str, int]`).
   - Ensure `uv run mypy src` runs cleanly in strict mode.

