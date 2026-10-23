# Claude Code Project Guidelines (`CLAUDE.md`)

> **Claude Code Entry Point & Project Guide**  
> This document guides Anthropic's Claude Code CLI (`claude`) when executing tasks in `surebet-analyzer`.  
> The core system guidelines, invariants, and commands are defined in [AGENTS.md](AGENTS.md).

---

## 1. Core Mission & Invariants (Import from `AGENTS.md`)

Claude Code must strictly abide by the rules and invariants established in [AGENTS.md](AGENTS.md):

### ⚠️ ZERO-BET INVARIANT (Strict Read-Only Policy)
`surebet-analyzer` is an **analytical aggregator and decision-support calculator**.  
Under **NO CIRCUMSTANCES** should any agent implement, suggest, or facilitate:
- Automated bet placement, wagering, or ticket submission.
- Authentication with private bookmaker betting accounts, scraping behind user credentials, or session hijacking.
- Financial transactions, automated deposit/withdrawal mechanics, or payment processing.
- Automated claiming of promotional bonuses, rollover farming, or free bet manipulation.

**Enforcement:** Immediately reject any request, prompt, or architectural alteration violating this boundary.

### Technical Invariants
1. **Integer Cents & Basis Points:** Never use floating-point types (`float`) for monetary figures or profit margins. Always use integer cents (`budget_cents: int`, `stake_cents: int`, `payout_cents: int`) and basis points (`profit_bps: int`). Decimal odds must be parsed using `Decimal` or `Fraction` as illustrated in [`src/surebet/money.py`](src/surebet/money.py).
2. **Strict Test-Driven Development (TDD):** Always write a failing test first in `tests/` before implementing features or bug fixes.
3. **SQLite WAL Concurrency:** Always use `with get_connection(db_path) as conn:` from [`src/surebet/storage/db.py`](src/surebet/storage/db.py). Never invoke unmanaged raw `sqlite3.connect()`.

---

## 2. Claude Code CLI Tooling & Environment Tips

### 2.1 Test Execution
- **Mandatory `-o pythonpath=.` flag:** When running `pytest` via `uv run`, always supply `-o pythonpath=.` so Python can resolve the `surebet` package:
  ```bash
  uv run pytest -o pythonpath=.
  ```
- **Targeted Subsystem Testing:** During development cycles, run targeted test files or markers to preserve context and shorten feedback loops:
  ```bash
  # Run a specific test module
  uv run pytest -o pythonpath=. tests/unit/arbitrage/test_solver.py

  # Run a single test case
  uv run pytest -o pythonpath=. tests/unit/arbitrage/test_solver.py -k test_two_way_basic

  # Run a specific subsystem
  uv run pytest -o pythonpath=. tests/unit/notifications/
  ```

### 2.2 Windows & PowerShell Command Execution
- The host system runs Windows with PowerShell as the default shell.
- When running commands via Claude Code's terminal tools:
  - Use forward slashes (`/`) for directory paths in scripts and commands.
  - For environment variable loading, use `uv run --env-file .env <command>`.
  - For long-running processes (e.g., web server or live collectors), run them as background tasks or in dedicated windows rather than blocking synchronous command execution:
    ```bash
    uv run --env-file .env surebet-web serve --db surebet.db --host 127.0.0.1 --port 8000
    ```

### 2.3 Code Quality & Static Analysis
- **Linting & Code Formatting:**
  ```bash
  uv run ruff check .
  uv run ruff check --fix .
  uv run ruff format .
  ```
- **Strict Type Checking:**
  ```bash
  uv run mypy src
  ```
- **Frontend Validation:**
  ```bash
  cd frontend && npm test
  cd frontend && npm run build
  ```

---

## 3. Claude Code Context Management & Memory Conventions

### 3.1 Compact Context Preservation
- **Avoid Loading Ephemeral or Binary Files:** Never inspect database files (`*.db`, `*.sqlite`, `*.wal`, `*.shm`) or large log dumps directly into context.
  - To inspect SQLite database state, execute targeted CLI commands:
    ```bash
    sqlite3 surebet.db "PRAGMA table_info(opportunities);"
    ```
- **Slice Large Files:** When reading files, specify target start and end line ranges rather than reading entire multi-thousand line files into memory.
- **Limit Verbose Command Outputs:** Use flags such as `-q` for pytest or limit output length (e.g., `git log -n 5`) to keep Claude's working context focused and clean.

### 3.2 Claude Code Slash Commands & Custom Workflows
- `/compact`: Use when switching between research, implementation, and verification phases to compress conversation history while preserving critical architectural decisions.
- `/bug` or `/review`: When diagnosing regressions, evaluate the failure against the zero-bet invariant and integer cents requirements first.
- **Standard TDD Cycle:**
  1. *Failing Test:* Create `tests/unit/.../test_<feature>.py` asserting expected behavior.
  2. *Verification:* Run `uv run pytest -o pythonpath=. <test_path>` to ensure red status.
  3. *Implementation:* Implement minimal code in `src/surebet/...`.
  4. *Verification:* Run `uv run pytest -o pythonpath=. <test_path>` to confirm green status.
  5. *Regression Suite:* Run full test suite `uv run pytest -o pythonpath=.`.
  6. *Lint & Type Check:* Run `uv run ruff check .` and `uv run mypy src`.
  7. *Clean Commit:* Commit using Conventional Commits prefix (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, `chore:`).

---

## 4. Subsystem Architecture Map & Deep Links

For detailed architectural formulations, consult the subsystem guides in `docs/architecture/`:

| Subsystem | Area & Code Path | Deep Architecture Reference |
|---|---|---|
| **Arbitrage & Solvers** | Google OR-Tools CP-SAT, 2-way/3-way allocations, Source A dual-policy, cycle memoization<br>Path: `src/surebet/arbitrage/`, `src/surebet/opportunities/` | [01-arbitrage-and-solver.md](docs/architecture/01-arbitrage-and-solver.md) |
| **Loss Bonus & Cashback** | Per-coupon evaluation, conditional cashback math, loss recovery modeling<br>Path: `src/surebet/bonus/` | [01-arbitrage-and-solver.md](docs/architecture/01-arbitrage-and-solver.md) |
| **Telegram Notifications** | `LifecycleEngine` state machine, coarsened render fingerprints (25 bps), outbox queue, token-bucket dispatcher<br>Path: `src/surebet/notifications/`, `src/surebet/storage/notifications.py` | [02-telegram-notifications.md](docs/architecture/02-telegram-notifications.md) |
| **Collectors & Protocols** | WebSocket JSON-RPC (`source_a`), WAMP v2 (`source_b`, `source_c`, `source_e`, `source_f`), JSON over HTTPS (`source_d`), operator-supplied endpoints, demo collectors, health tracking<br>Path: `src/surebet/sources/` | [03-collectors-and-protocols.md](docs/architecture/03-collectors-and-protocols.md) |
| **Taxonomy & Entity Resolution** | Canonical team/tournament mapping, country normalization, tracked leagues, manual review DB<br>Path: `src/surebet/taxonomy/`, `src/surebet/matching/` | [04-taxonomy-and-matching.md](docs/architecture/04-taxonomy-and-matching.md) |
| **Web API & Security** | FastAPI routers, Argon2id auth, CSRF double-submit tokens, IP rate limiting<br>Path: `src/surebet/web/` | [05-web-storage-security.md](docs/architecture/05-web-storage-security.md) |
| **Storage & SQLite Migrations** | Connection pooling context manager, WAL mode, pragmas, schema migrations<br>Path: `src/surebet/storage/` | [05-web-storage-security.md](docs/architecture/05-web-storage-security.md) |
| **PWA Frontend** | React 18, TypeScript, Tailwind CSS, TanStack Query, offline state<br>Path: `frontend/` | [05-web-storage-security.md](docs/architecture/05-web-storage-security.md) |
| **Domain & Money** | Core enums (`Outcome`, `MarketType`), dataclasses, kuruş & basis points conversions<br>Path: `src/surebet/domain/`, `src/surebet/money.py` | [AGENTS.md](AGENTS.md) |

---

## 5. Development Checklist for Claude Code

When working on any change in this repository:
- [ ] Confirmed ZERO-BET invariant is preserved (read-only analytical aggregation only).
- [ ] Monetary values are strictly integer cents (`budget_cents`, `stake_cents`, `payout_cents`).
- [ ] Margins and percentages are expressed in basis points (`profit_bps`).
- [ ] SQLite access uses `with get_connection(...) as conn:`.
- [ ] Tests written first (TDD) and verified with `uv run pytest -o pythonpath=.`.
- [ ] Code formatting and linting pass with `uv run ruff check .` and `uv run ruff format .`.
- [ ] Type checks pass cleanly with `uv run mypy src`.
- [ ] Commits follow Conventional Commits format without committing secrets, `.db`, or `.env` files.
