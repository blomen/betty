# Freebet `is_bonus` Accounting (server-side derivation)

**Date:** 2026-05-30
**Status:** Design — approved (approach + branch)
**Scope:** `backend/src/services/bet_service.py` + one test. Backend change → requires rebuild/redeploy.
**Branch:** `worktree-unibet-freebet-lifecycle` (adds to PR #33, making it the complete freebet feature).

## Problem

When a Unibet freebet is placed, the mirror records it via `POST /api/bets` with
`is_bonus: False` hardcoded (every recorder does this:
`play_loop.record_user_placed_bet:439`, `_record_manual_bet:593`,
`pending_loop`, `arb_runner`, `provider_runner`). So a 1000 SEK freebet is
stored as a normal `stake=1000` cash bet. Stats then over-count it: the free
stake is treated as real risk in `total_staked`/ROI, and a losing freebet shows
as `-1000` instead of `0`.

## What already exists (no change needed)

The `is_bonus` accounting is built end-to-end and keys entirely off the
`bets.is_bonus` flag:

- `BetService.create_bet(is_bonus=...)` — when `is_bonus` is true it skips the
  balance check ([bet_service.py:176](../../backend/src/services/bet_service.py)),
  skips the edge gate (243), stores `is_bonus=True` (294), and
  **auto-completes the freebet** `freebet_available → completed` (313-327).
- `Bet.profit` ([models.py:410-413](../../backend/src/db/models.py)) — won+bonus → `payout`; lost+bonus → `0`.
- `get_stats` ([bankroll_service.py:131](../../backend/src/services/bankroll_service.py)) — excludes bonus rows from staked/profit/ROI.
- `settle_bet` ([bet_service.py:381-404](../../backend/src/services/bet_service.py)) — already auto-unlocks `trigger_needed → freebet_available` when the qualifying single bet settles.

The single gap: nothing flips `is_bonus` to true for the freebet, because the
client always sends false.

## The change

Derive `is_bonus` **server-side** in `create_bet`, immediately after the
provider-exists check (~line 104, before the balance check at 176):

```python
# Freebet auto-flag: a bet placed while this provider sits in the
# freebet_available phase IS the freebet — flag is_bonus server-side
# (the mirror always sends is_bonus=False). Guarded on stake ≈ the freebet
# amount so a small cash bet during the phase isn't misflagged (a freebet
# token is staked at its full value).
if not is_bonus:
    fb = (
        self.db.query(ProfileProviderBonus)
        .filter(
            ProfileProviderBonus.profile_id == profile.id,
            ProfileProviderBonus.provider_id == provider_id,
            ProfileProviderBonus.bonus_status == "freebet_available",
        )
        .first()
    )
    if fb and stake >= (fb.bonus_amount or 0) * 0.9:
        is_bonus = True
```

`ProfileProviderBonus` is already imported in `bet_service.py` (used by the
existing freebet blocks). No other code changes — the downstream `is_bonus`
handling and the existing auto-complete block do the rest.

## Why one choke point

All mirror recorders POST to `/api/bets` → `BetService.create_bet`. Deriving
the flag here covers every recording path (intercept, "mark placed", reactive
history sync, arb) without touching the mirror. The batch path
(`/api/bets/batch`) is intentionally out of scope: freebets are placed as
single manual bets, never batched.

## Lifecycle effect

This also delivers the **auto-complete-on-placement** deferred in PR #33: the
freebet bet's recording advances the bonus to `completed` (existing block), so
the chip's "mark freebet used" button becomes a fallback. Combined with the
existing settle-time auto-unlock, the full lifecycle can now run hands-off,
with the chip buttons as manual overrides.

## Edge cases

- **Cash bet during `freebet_available`.** The `stake >= bonus_amount * 0.9`
  guard prevents a small regular bet from being misflagged. A freebet token is
  staked at its full value, so the real freebet passes the guard.
- **`bonus_amount` missing/zero.** `(fb.bonus_amount or 0) * 0.9 == 0`, so any
  stake ≥ 0 would pass — but a `freebet_available` row created by
  `start_freebet_tracking` always carries a positive `bonus_amount`, so this is
  not a real path. Acceptable.
- **Client explicitly sets `is_bonus=True`.** The `if not is_bonus` guard leaves
  it untouched; the existing auto-complete block still fires.

## Testing

`pytest` unit tests on `BetService.create_bet`, fixture pattern from
`backend/tests/test_ban_system.py` (in-memory engine, seed `Provider` + active
`Profile`):

1. **Freebet flagged + completed:** seed a `freebet_available`
   `ProfileProviderBonus` (bonus_amount=1000); `create_bet(..., stake=1000, is_bonus=False)`
   → recorded bet has `is_bonus is True` AND the bonus row is now `completed`.
2. **Cash bet not misflagged:** same `freebet_available` bonus;
   `create_bet(..., stake=50, is_bonus=False)` → recorded bet `is_bonus is False`,
   bonus still `freebet_available`.
3. **No bonus row (regression):** no `ProfileProviderBonus`;
   `create_bet(..., stake=1000, is_bonus=False)` → `is_bonus is False`.

Run: `cd backend && pytest tests/test_bet_service_freebet.py -v` → all pass.
Also run the existing suite touching this service:
`cd backend && pytest tests/test_ban_system.py tests/test_bankroll_service_trigger.py -q`.

## Deployment

Backend code change → rebuild required. Deploy via the `/deploy` skill
(`server-deploy.sh rebuild backend`), then verify: `/health` responds (note
`boot_id`), server `git rev-parse HEAD` matches the pushed commit, and the
container `CreatedAt` is after deploy. 5-minute deploy cooldown applies.
