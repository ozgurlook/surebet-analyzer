# Manual JSON analysis

`analyze-manual` is a bounded local JSON-in/JSON-out decision-support command
for one event. It reads one regular UTF-8 file (at most 5 MiB), validates the
frozen domain contract, calls the source-independent `find_opportunities`
service, and writes one compact deterministic JSON array followed by a
newline. It does not discover sources, import collectors, access the network,
authenticate, place bets, or perform financial actions.

```json
{
  "request": {
    "budget_cents": 10000,
    "now": "2026-08-24T12:00:00Z",
    "bonus_evaluation_at": "2026-08-24T13:00:00Z",
    "stale_after_seconds": 75,
    "accounts": {
      "source_a": {"balance_cents": 100000, "max_stake_cents": 100000},
      "source_b": {"balance_cents": 200, "max_stake_cents": 100000},
      "source_c": {"balance_cents": 200, "max_stake_cents": 100000}
    }
  },
  "quotes": [
    {"source":"source_a","event_id":"event-1","outcome":"HOME","odds":"2.90","observed_at":"2026-08-24T12:00:00Z"},
    {"source":"source_b","event_id":"event-1","outcome":"DRAW","odds":"3.60","observed_at":"2026-08-24T12:00:00Z"},
    {"source":"source_c","event_id":"event-1","outcome":"AWAY","odds":"3.10","observed_at":"2026-08-24T12:00:00Z"}
  ]
}
```

All money is integer kuruş. Odds must be strings with no more than four
fractional digits; numeric JSON odds and binary floats are rejected. Duplicate
keys (including escaped spellings), non-finite constants, mixed event IDs,
extra fields, malformed JSON, invalid UTF-8, and non-regular or oversized
files are rejected without echoing paths or payloads.

Try the included fixtures:

```text
uv run analyze-manual tests/acceptance/fixtures/standard-arbitrage.json
uv run analyze-manual tests/acceptance/fixtures/bonus-conditional.json
```

## Anonymous public collection

Collection from the WAMP-feed sources (Source B, Source C, Source E)
is a separate, opt-in public-source command. A one-shot command writes one
sanitized `CollectionBatch` JSON record; the watch command writes
newline-delimited batches or normalized unavailable records. Watch intervals
are non-overlapping and must be at least 30 seconds.

The repository ships no provider hosts. Each command reads its source's
endpoint from the environment (`SUREBET_SOURCE_B_ENDPOINT`, `_ORIGIN`,
`_REALM`, `_OPERATOR_ID`, and likewise for the other sources; see
`src/surebet/sources/endpoints.py`). Without an endpoint, `once` prints an
unavailable record with the reason `endpoint not configured`.

```text
uv run collect-wamp once --source source_b
uv run collect-wamp once --source source_e
uv run collect-wamp watch --source source_b --interval-seconds 30
```

To explore the full web UI without any endpoint, run the server in demo mode
(`uv run surebet-web serve --demo --db demo.db`), which uses deterministic
offline collectors and creates a `demo` login whose password is printed on start.

These commands do not log in, accept credentials, retain cookies, tokens, or
user-session identifiers, place bets, or print raw WAMP frames. The anonymous
router session ID used during a live WAMP handshake is ephemeral in-memory
protocol state only; it is not accepted from users or persisted. Their output
is collection input for a separate event-matching/import step;
`analyze-manual` itself remains local JSON-in/JSON-out and has no
source-network access.

`bonus_evaluation_at` is the conservative post-settlement instant at which a
counterfactual bonus claim is evaluated; it is distinct from analysis `now`.
If it is absent, bonus effects are excluded. A standard opportunity is
profitable without bonus. `bonus_qualified` is profitable under confirmed
requirements. `bonus_conditional` is profitable only under the assumed
confirmation mode. The strict-before-assumed rule always evaluates confirmed
mode first; assumptions are not silently promoted to confirmed facts.

The modeled Source B and Source C terms are a 10% direct-cash bonus requiring at least a
100 TL loss and 100 TL net deposit, a balance below 20 TL, an unexpired 72-hour
period, withdrawals not exceeding deposits, no unrelated open bets, and manual
confirmation. There is no rollover. Only incremental entitlement is counted;
an existing bonus can therefore produce a zero or negative delta.

The output is analysis and decision support, not an assurance of bookmaker
acceptance or payout. No login, deposit, withdrawal, automatic betting, or
bonus claim is performed, and this command has no source-network access.
