# Unified Currency Layer — Design

**Date:** 2026-05-21
**Status:** Approved (brainstorming) — pending implementation plan
**Branch:** `feature/unified-currency-layer`

## Problem

Arnold places bets on SEK-denominated soft books (betinia, quickcasino, …) and
USD-denominated unlimited books (polymarket, kalshi). Every SEK↔USD crossing
today relies on a hand-edited rate constant that is **duplicated in three places
that are unaware of each other**:

- **Backend** — `get_exchange_rate(provider_id)` reads `providers.yaml`
  `exchange_rate_sek` (polymarket `10.5`, kalshi `13.3`). Consumed by
  `batch_builder`, `allocator`, `profile_repo`, `bet_repo`, `stake_calculator`.
- **Frontend** — hardcoded `SEK_PER_USD = 10.5` in `PlayPage.tsx` *and*
  `RATE_TO_SEK = { USD: 10.50 }` in `StatsPage.tsx`.
- **The arb engine** — `arnold/mirror/arb_math.py` and `arb_runner.py` have
  **zero** currency awareness. `recalc_counter_stakes` multiplies a SEK anchor
  stake by odds and divides by USD counter odds, producing a SEK-magnitude
  number. It only "works" because `PlayPage.tsx` happens to divide the result
  by `10.5` before placing on polymarket.

### Concrete failures this caused

1. **kalshi `exchange_rate_sek: 13.3`** — kalshi is USD-denominated, same as
   polymarket (`10.5`). One of those is simply wrong. The per-provider
   `exchange_rate_sek` field *conflates* "what currency is this provider" with
   "what is the SEK/USD rate", which is how the bad value hid.

2. **Mixed-currency aggregation.** A verification pass on the 16 most recent arb
   groups summed SEK anchor stakes and USDC counter stakes as if they were the
   same unit, producing a nonsense "−$242 net P&L" conclusion. Re-measured in a
   common currency (SEK @ 10.5): 8 groups are genuine guaranteed-profit arbs, 5
   are trivially under-hedged (−0.5 to −1.3 kr worst case), 2 are materially
   under-hedged (`9e27786e5a19` −22 kr, `b3eb8e2904b0` −16 kr because the
   polymarket counter under-filled), 1 had a void anchor. Nothing structural
   detects an under-filled counter because no component reasons about both legs
   in one currency.

3. **Stale rate.** `providers.yaml` itself comments the rate as "rough 2026-04
   FX" — already stale, with no single place to refresh it.

## Goals

- One source of truth for the SEK/USD rate and for each provider's currency.
- A `Money` value type used everywhere money flows, making cross-currency
  arithmetic a hard error instead of a silent bug.
- The arb engine becomes currency-aware: counter stakes are computed in the
  counter leg's native currency, so no downstream component divides by a rate.
- A structural under-hedge check on placed arbs.

## Non-goals

- Live FX feed. Decision: **static config**, refreshed by hand in one place.
- Decimal-based money. The codebase uses `float` for stakes and odds;
  converting to `Decimal` is a separate, larger refactor (YAGNI). `Money` is
  `float`-backed with explicit rounding at boundaries.
- Schema migrations. `bets.currency` already exists and is populated.

## Design

### 1 · The `money/` package (Python)

New top-level package `money/`, a **pure leaf module** — no I/O, no yaml
dependency, no network.

```
money/
  __init__.py        # re-exports Money, Currency, CurrencyMismatch,
                     # RateNotConfigured, configure, convert
  currency.py        # Currency enum
  money.py           # Money value type
  rates.py           # process-global rate state + convert()
  tests/
    test_money.py
    test_rates.py
```

**`Currency`** — enum with `SEK` and `USD`. A `Currency.parse(str)` classmethod
normalizes provider/DB strings: `"USDC"` and `"USD"` → `USD`; `"SEK"` → `SEK`;
unknown → raises `ValueError`.

**`Money`** — frozen dataclass `(amount: float, currency: Currency)`:

| Operation | Behavior |
|---|---|
| `Money + Money`, `Money - Money` | same currency → `Money`; mismatch → raise `CurrencyMismatch` |
| comparisons (`<`, `<=`, `>`, `>=`) | same currency only, else raise `CurrencyMismatch` |
| `==` | equal iff same currency **and** amount; never raises (so `Money` is dict/set safe) |
| `Money * scalar`, `Money / scalar` | scalar is `int`/`float` → `Money`, same currency |
| `Money * Money` | raises `TypeError` (meaningless) |
| `.convert(to: Currency) -> Money` | identity if same; else applies the configured rate |
| `.rounded(dp=2) -> Money` | round to currency minor unit |
| `Money.zero(currency)` | classmethod |
| `.is_zero`, `__bool__` | amount == 0 |
| `repr` | `Money(242.00, SEK)` |

**`rates.py`** — process-global rate, set once at startup:

- `configure(sek_per_usd: float)` — sets the process rate. Called once by each
  app's startup (backend lifespan, local client launch).
- `convert(amount: float, frm: Currency, to: Currency) -> float` — raises
  `RateNotConfigured` if `configure` was never called. **No silent fallback** —
  a missing rate is loud, not guessed.
- `Money.convert` delegates here.

`money/` does not read `providers.yaml`. The *value* lives in the **deployed
server's** `providers.yaml` (§2). The backend reads its own yaml at startup;
the local client and frontend both obtain the rate from the backend **API** at
startup — never from a local file copy, which could drift from the deployed
server. This keeps `money/` a dependency-free leaf that is trivially
unit-testable, and keeps the single source of truth unambiguous.

**Packaging.** `money/` sits at repo root. The backend `Dockerfile` gains
`COPY money/ money/` and `money` is importable on the backend `PYTHONPATH`. The
local client (`arnold.bat` → `arnold/launch.py`) runs from repo root, so
`import money` resolves there already. Both deployables import the *same*
source.

### 2 · Rate and provider currency — single source

`backend/src/config/providers.yaml`:

- Add one top-level key:
  ```yaml
  currency:
    sek_per_usd: 10.5   # single source of truth — refresh periodically
  ```
- Each provider entry gains `currency: SEK` or `currency: USD`. polymarket →
  `USD`, kalshi → `USD` (fixes the `13.3` bug), all soft books → `SEK`.
- The per-provider `exchange_rate_sek` field is **removed**.

`backend/src/config/loader.py`:

- Remove `get_exchange_rate(provider_id)` and `get_all_exchange_rates()`.
- Add `get_provider_currency(provider_id) -> Currency` and
  `get_sek_per_usd() -> float`.
- Backend startup calls `money.configure(get_sek_per_usd())`.
- The local client's Python (`arnold/launch.py`) fetches `sek_per_usd` from the
  backend API over the SSH tunnel at startup and calls `money.configure()`. If
  the fetch fails the rate stays unconfigured and arb sizing raises
  `RateNotConfigured` — a loud failure, by design.

### 3 · Frontend

- New `arnold/frontend/src/utils/money.ts` — TS mirror: a `Currency` union, a
  `Money` shape `{ amount, currency }`, and helper functions `add`, `sub`,
  `mul`, `convert`, `format`. TS cannot enforce currency matching at compile
  time as hard as Python; the helpers **throw at runtime** on mismatch.
- Delete `SEK_PER_USD = 10.5` from `PlayPage.tsx` and `RATE_TO_SEK` from
  `StatsPage.tsx`.
- The rate is fetched from the API. The provider endpoint currently ships
  per-provider `exchange_rate_sek`; it changes to ship per-provider `currency`
  plus a top-level `sek_per_usd`. The frontend stores the rate once (context or
  the existing provider/config fetch) and `money.ts` reads it.
- `betting.ts` currency formatting routes through `money.ts`.

### 4 · The arb engine — the behavioral fix

`arnold/mirror/arb_math.py`:

- `recalc_counter_stakes(anchor_stake: Money, anchor_odds: float, counter_legs)`
  — for each counter leg:
  1. `total_payout = anchor_stake * anchor_odds` (Money, anchor currency).
  2. `target = total_payout.convert(counter_currency)`.
  3. `counter_stake = (target / counter_odds).rounded()` (Money, counter
     currency).
  Returns `list[Money]`, each in its leg's native currency. **The stake handed
  to polymarket is already correct USD.**
- `recalc_profit_pct` — unchanged (odds-only, currency-agnostic). Docstring
  clarified: it assumes the legs are sized to *converted-equal* payouts.

`arnold/mirror/arb_runner.py`:

- `anchor_stake = Money(balance, get_provider_currency(self.provider_id))`.
- Counter stakes are `Money` inside `arb_runner`. The workflow methods
  `prep_betslip` / `update_slip_stake` keep their existing `float` signature —
  `arb_runner` unwraps `.amount` at the call site, which *is* the boundary: the
  stake is known to be in the provider's native currency there. This avoids
  re-typing every provider workflow.
- **Under-hedge guard (new behavior).** After the counter leg fills, compare
  `filled_counter_payout.convert(SEK)` against `anchor_payout.convert(SEK)`. If
  the counter covers materially less than the anchor (threshold: counter
  payout < 98% of anchor payout), emit an `arb_underhedged` SSE event and log a
  warning. The existing odds-only `arb_negative_profit` check is retained; this
  adds a *stake-coverage* check. This is what would have flagged `9e27786e5a19`
  and `b3eb8e2904b0` at placement time.
- `_record_bet` stores `stake` + `currency` from the `Money` (DB column
  already exists).

### 5 · Backend bankroll and stats

- All `get_exchange_rate` call sites migrate to `Money` + `.convert()`:
  `batch_builder`, `allocator` (×4), `profile_repo` (×2), `bet_repo`,
  `stake_calculator`.
- `profile_provider_balances.balance` reads reconstruct
  `Money(balance, get_provider_currency(provider_id))`.
- Stats P&L aggregation builds a list of `Money` and converts each to `SEK`
  explicitly before summing. A raw `sum()` over mixed currencies now raises
  `CurrencyMismatch` — the bug class is eliminated structurally.

### 6 · Database

- `bets.currency` exists and is populated (`SEK` / `USDC`); reads reconstruct
  `Money`. No schema change. Optional one-off backfill of any `NULL`
  `bets.currency` from `get_provider_currency(provider_id)`.
- `profile_provider_balances` has no currency column; currency is derived from
  `provider_id`. No migration.

### 7 · Rollout and risk

- **Independent, land first:** the `money/` package, the `providers.yaml`
  restructure, `loader.py`, and the backend bankroll/stats migration.
- **Coupled, must ship in one step:** `recalc_counter_stakes` returning correct
  USD **and** `PlayPage.tsx` dropping its `/ 10.5`. Shipping either alone
  double-divides (counter under-bet ~10×) or skips the divide (counter
  over-bet ~10×). The implementation plan locks these into a single task.
- The arb engine runs in the **local client** (`arnold/`), shipped via
  `arnold.bat` (Vite + local FastAPI) — no backend deploy. The backend changes
  ship via `server-deploy.sh`. The API contract change (provider endpoint:
  `exchange_rate_sek` → `currency` + `sek_per_usd`) couples a backend deploy
  with a frontend rebuild; the frontend must tolerate the old shape until the
  backend deploy lands (read new fields, fall back to old).

## Testing

- **`Money` contract** (`money/tests/`): mismatch raises; `convert` round-trips;
  scalar mul/div; rounding; `==` never raises; `RateNotConfigured` before
  `configure`.
- **`recalc_counter_stakes`**: golden cases built from the 16 real arb groups
  (anchor SEK stake/odds + counter USD odds → expected counter USD stake).
  Verifies the converted counter payout equals the anchor payout within
  rounding.
- **Under-hedge guard**: a filled counter materially short of the anchor payout
  triggers `arb_underhedged`; a correctly-filled one does not.
- **Backend**: bankroll/stats call sites produce the same SEK totals as before
  the refactor for SEK-only inputs, and correct totals for mixed inputs.

## Open items resolved during brainstorming

- Scope: unified currency layer everywhere (backend + frontend + arb engine).
- Rate source: static config, single place.
- Representation: `Money` value type everywhere.
- Module placement: shared top-level `money/` package (Approach A).
- Under-hedge guard: included.
