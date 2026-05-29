# Liquidity-Aware Sizing — Design Spec

**Date:** 2026-05-30
**Status:** Approved design — pending implementation plan
**Sub-project 2 of 5** in the "profit-lever gap" program (see the multi-book-sharp-blend spec for the full program).

## Background

Deep research on professional sports-betting profit levers (2026) found a real
scalability ceiling on prediction markets: **order books are thin on niche /
game-prop markets**, so a Kelly-correct stake can move the mid-price 2–3¢ or
fail to fill. Betty already trades Polymarket/Kalshi/Cloudbet but its Kelly
sizer (`backend/src/bankroll/stake_calculator.py`) assumes fully fillable size —
it caps stakes by edge, bankroll, and a single-bet %, but **never by available
order-book depth**.

Betty already captures `depth_usd` (total ask-side depth, USD) on the `Odds`
table for CLOB books (polymarket, kalshi) at extraction time. It is consumed
today only by the arb-table liquidity filter (alongside `max_stake`/`bid`/`ask`),
not by value-bet sizing.

## Goal

Cap the recommended **value-bet** stake on prediction markets by available
order-book depth, so a Kelly stake never exceeds a configurable fraction of the
visible ask-side depth. Cheap, self-contained, protects the unlimited pool from
slippage / partial fills.

## Decisions (from brainstorming)

- **Cap location:** server-side, using the already-captured extraction-time
  `depth_usd`. (Client-side live-depth re-check at placement is a possible
  future enhancement, explicitly out of scope.)
- **Cap aggressiveness:** `stake ≤ 0.50 × depth_usd` (leaves headroom before
  moving the price / hitting empty levels). Fraction is configurable per
  provider.
- **Unknown depth:** `depth_usd` null/≤0 → **no cap** (fall back to current
  Kelly behavior + log). Don't kill volume on missing data.
- **Approach A:** a separate pure helper, keeping `calculate_stake` (the
  MC-tuned Kelly function) untouched.
- **Scope:** prediction-market CLOB books only (polymarket, kalshi — the
  providers with `depth_usd`). Pinnacle (`max_stake`, separate mechanism) and
  cloudbet (no order-book depth captured) are NOT gated.

## Architecture

### Component 1 — `liquidity_capped_stake()` (pure function)

Location: `backend/src/bankroll/stake_calculator.py`, beside the per-provider
profile helpers. No I/O; fully unit-testable.

```python
def liquidity_capped_stake(
    stake_sek: float,
    provider_id: str,
    depth_usd: float | None,
    exchange_rate_sek: float,
) -> tuple[float, bool, str | None]:  # (capped_sek, was_capped, reason)
```

Logic:
1. Look up the provider's `liquidity_fraction` (new field on
   `ProviderStakeProfile`). If `None` (provider not gated) → return
   `(stake_sek, False, None)`.
2. If `depth_usd` is `None` or `≤ 0` → return `(stake_sek, False, None)` (no
   data; don't cap).
3. `cap_sek = fraction × depth_usd × exchange_rate_sek`.
4. If `stake_sek ≤ cap_sek` → return `(stake_sek, False, None)`.
5. Else → return `(cap_sek, True, f"liquidity cap: {fraction:.0%} of ${depth_usd:.0f} depth")`.

**Currency:** `depth_usd` is USD; `exchange_rate_sek` is SEK-per-native
(≈10.5 for USDC/USD providers); USDC≈USD for depth purposes (consistent with
how the codebase already treats these). This is the only conversion and it
lives solely in this function.

### Component 2 — Configuration (`ProviderStakeProfile`)

Add `liquidity_fraction: float | None = None` to the existing
`ProviderStakeProfile` dataclass. Set in `PROVIDER_STAKE_PROFILES`:
- `polymarket`: `0.5`
- `kalshi`: `0.5`
- `pinnacle`: `None` (uses `max_stake`, out of scope)
- `cloudbet`: `None` (no order-book depth)

(Default `None` means any unlisted provider is ungated — fail-open, since the
cap only applies where we have depth data.)

### Component 3 — Integration (`scan_value_with_stakes`)

In `backend/src/analysis/scanner.py`, the value-bet stake path:
1. Thread the chosen provider/outcome's `depth_usd` from its `Odds` row into the
   value path (add `depth_usd: float | None` to the `ValueBet` dataclass; the
   scanner reads it where it reads `bid`/`ask`/`max_stake`).
2. After `calculate_stake` returns the recommended stake, call
   `liquidity_capped_stake(stake, provider_id, depth_usd, get_exchange_rate(provider_id))`.
3. **Re-apply the min-stake floor:** if the capped stake `< provider_min_stake_sek(...)`,
   skip the bet (set stake 0 / skip reason). The cap happens after
   `calculate_stake`'s internal min check, so the floor must be re-checked.
4. Set `recommended_stake` to the capped value; record `was_capped` + reason on
   the `ValueBet` so it can be surfaced (e.g. a "liquidity-capped" indicator).

## Data flow

```
Extraction → Odds.depth_usd (poly/kalshi CLOB ask-side depth, USD)
    ↓
scanner.scan_value_with_stakes (per value opp)
    ↓
calculate_stake(...) → Kelly stake (SEK)        [unchanged]
    ↓
liquidity_capped_stake(stake, provider, depth_usd, rate)   [NEW]
    ↓
re-check provider_min_stake_sek → skip if below
    ↓
ValueBet.recommended_stake (capped) + was_capped flag
```

## Error handling & edge cases

| Case | Behavior |
|---|---|
| Provider ungated (pinnacle/cloudbet) | No cap |
| `depth_usd` null / ≤ 0 | No cap; log |
| Stake already ≤ cap | Unchanged, `was_capped=False` |
| Capped stake < provider min-stake | Skip bet (re-apply `provider_min_stake_sek`) |
| Currency | `cap_sek = fraction × depth_usd × exchange_rate_sek`; isolated in the helper |
| Arb / bonus paths | Untouched — arbs size via ArbRunner; cap wired only into value path |

## Testing

- **Unit (`liquidity_capped_stake`):** under-cap unchanged; over-cap → capped;
  null/≤0 depth → unchanged; ungated provider → unchanged; currency math
  (e.g. $400 × 0.5 × 10.5 = 2100 SEK).
- **Integration (`scan_value_with_stakes`):** thin-depth polymarket value bet →
  stake capped + flagged; pinnacle value bet → unaffected; cap-below-min → bet
  skipped. Uses existing scanner fixtures.
- Pure-helper tests need no DB.

## Deployment note

Touches `backend/` (`bankroll/stake_calculator.py`, `analysis/scanner.py`) →
requires a backend rebuild. Behavior change is confined to **prediction-market
value-bet stake sizing** (caps stakes down on thin books; never increases a
stake). No migration. Frontend `was_capped` surfacing is optional and can ship
separately via the local client.

## The 5-gap program (context)

Sub-project 2 of 5. Sequence: 1. multi-book sharp blend (shipped, PR #30) →
**2. liquidity-aware sizing (this spec)** → 3. steam-execution latency pipeline →
4. shading-aware edge adjustment → 5. bonus-play behavior shaping.
