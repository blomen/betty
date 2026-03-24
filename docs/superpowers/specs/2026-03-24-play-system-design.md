# Play System Design

**Date:** 2026-03-24
**Status:** Draft

## Problem

Value bets appear across 25+ providers in 9 clusters. Manual provider selection wastes time and leaves money sitting on soft books (risk of getting trapped by limiting). The system needs to automate: which provider to bet on, what stakes, and how to progress through bonus wagering — all from a minimal UI that just shows bets to fire.

## Strategy

1. **Empty every balance each session** — money on soft books is a liability
2. **One or two active siblings per cluster** — deloads limiting across brands sharing a backend
3. **Lowest wagering multiplier first** — fastest capital recycling
4. **Auto-advance through providers and clusters** — no manual switching
5. **Works for both fresh and existing accounts** — reads current state, adapts

## Provider Lifecycle

Each provider's state is **auto-derived** from existing DB data (no new status column):

| State | Detection | Meaning |
|-------|-----------|---------|
| `available` | No balance, no active bonus | Ready to deposit + claim bonus |
| `deposited` | Balance > 0, bonus `trigger_needed` | Need to place trigger bet(s) first |
| `wagering` | Bonus `in_progress` | Clearing wagering, fire all +EV bets |
| `freebet` | Bonus `freebet_available` | Freebet ready to place |
| `playing` | Bonus `completed` or no bonus, balance > 0 | Pure +EV, no restrictions |
| `limited` | Limit record exists (any level) | Still playable, flagged with LTD badge |
| `dormant` | Balance = 0, bonus `completed`/`claimed`/`claimed` (legacy skip) | Done — rotate to next sibling |

**Note:** The DB's `bonus_status = "available"` means "bonus ready to use" — distinct from the lifecycle state `available` which means "provider not yet funded." The `claimed` bonus status (used for legacy/skipped bonuses) maps to `dormant`.

### Cluster sibling rules

- **Clusters with ≥30 unique opportunities:** 2 active siblings
- **Clusters with <30 unique opportunities:** 1 active sibling
- **Standalones:** always 1
- Re-evaluated each session based on live opportunity count
- Within a cluster, siblings are prioritized by wagering urgency (highest `wagering_remaining / days_until_expiry`)

### Cluster visibility

A cluster pill is **hidden** when total balance across its active siblings < minimum stake (uses `dynamic_min_stake()` from `StakeCalculator`, floor = 5 kr).

## Trigger Modes

Two modes for bonus trigger phase, configured per provider in `providers.yaml`:

### Single-shot trigger (`trigger_mode: "single"`)

One bet with `stake ≥ trigger_amount` at `odds ≥ trigger_min_odds`.

- Badge: **TRG 500kr**
- Stake: Fixed at trigger amount
- The Play panel highlights the first qualifying bet as the trigger
- Example: "Bet 500 kr at 1.80+ to unlock freebet"

### Cumulative trigger (`trigger_mode: "cumulative"`)

Multiple Kelly-sized bets until `wagered_amount ≥ trigger_requirement`, each at `odds ≥ trigger_min_odds`.

- Badge: **TRG 34%**
- Stake: Normal Kelly sizing
- Progress tracked via existing `wagered_amount` field
- Example: "Wager 3,000 kr total at 1.80+ to unlock deposit match"

### Model change

Add `trigger_mode` field to `ProfileProviderBonus`:

```python
trigger_mode = Column(String, default="cumulative")  # "single" or "cumulative"
```

And in `providers.yaml` bonus config:

```yaml
unibet:
  bonus:
    type: freebet
    trigger_mode: single
    trigger_amount: 500
    trigger_min_odds: 1.80

leovegas:
  bonus:
    type: bonusdeposit
    trigger_mode: cumulative
    trigger_amount: 3000
    trigger_min_odds: 1.80
    main_multiplier: 6
    main_min_odds: 1.80
```

## Bonus Lifecycle Flows

### A) Simple deposit match (1x wagering)

```
Deposit 500 → bonus 500 → balance 1,000
Status: wagering (in_progress)
Session: fire all +EV bets at odds ≥ min_odds
After 1 session: wagering cleared → playing
```

### B) Two-phase deposit match (cumulative trigger)

```
Deposit 500 → balance 500 (bonus locked)
Status: deposited (trigger_needed, cumulative)
Session: fire Kelly-sized bets at trigger_min_odds
Trigger wagering met → bonus unlocked, added to balance
Status: wagering (in_progress)
Continue firing +EV bets at main_min_odds
Wagering cleared → playing
```

### C) Freebet (single-shot trigger)

```
Deposit 500 → balance 500
Status: deposited (trigger_needed, single)
Session: place one qualifying bet (stake ≥ 500, odds ≥ 1.80)
Trigger settles → freebet unlocked
Status: freebet (freebet_available)
Next session: place freebet on best available bet (is_bonus=true)
Freebet used → playing
```

### D) Two-phase deposit match (single-shot trigger)

```
Deposit 500 → balance 500 (bonus locked)
Status: deposited (trigger_needed, single)
Session: place one qualifying bet (stake ≥ trigger_amount, odds ≥ min_odds)
Trigger settles → bonus unlocked, added to balance
Status: wagering (in_progress)
Continue firing +EV bets at main_min_odds
Wagering cleared → playing
```

## Session Flow

### UI layout

```
┌─────────────────────────────────────────────────────┐
│ Play  Kambi 2  Altenar 2  Gecko 2  Spectate 1  ... │
├─────────────────────────────────────────────────────┤
│ unibet: 847 kr [TRG]  │  leovegas: 1,000 kr [WAGER]│
├─────────────────────────────────────────────────────┤
│ EVENT          OUTCOME       ODDS  FAIR  EDGE  STAKE│
│ Madrid v Gir.  Girona [1X2]  11.5  9.9   16%   150 │
│ Plzen v Spar.  Under 4.5     2.30  1.79  25%   225 │
│ ...                                                  │
├─────────────────────────────────────────────────────┤
│ 847 kr left → ~5 bets │ 12 placed │ 2,400 kr wagered│
└─────────────────────────────────────────────────────┘
```

### Automation rules

1. **Auto-select cluster** — highest wagering urgency (most `remaining / days_left`)
2. **Auto-select sibling** — highest urgency within cluster
3. **Auto-filter bets** by bonus phase:
   - `trigger_needed` (single): show only bets where stake can meet trigger amount AND odds ≥ trigger_min_odds
   - `trigger_needed` (cumulative): show bets with odds ≥ trigger_min_odds, Kelly-sized
   - `freebet_available`: show best bet with "Place Freebet" action
   - `in_progress`: show bets with odds ≥ wagering min_odds
   - `completed` / none: show all +EV bets
4. **Auto-size stakes**:
   - Single-shot trigger: fixed stake = trigger amount
   - Cumulative trigger: Kelly
   - Freebet: fixed stake = freebet amount, marked `is_bonus=true`
   - Wagering / playing: Kelly
   - Limited: Kelly (risk calculator already adjusts)
5. **Auto-advance** when sibling balance < min stake → switch to second sibling → next cluster
6. **Auto-hide clusters** where total balance < min stake
7. **Badges** — TRG (+ amount or %), FREE, WAGER, LTD, PLAY — only bonus visibility in UI

### Session stats bar

Minimal live counters:
- Remaining balance on current sibling
- Estimated bets left (balance / avg stake)
- Total bets placed this session
- Total kr wagered this session

## Day-One Deployment (Fresh Account)

### Recommended setup

```
Kambi:       unibet + leovegas       2 × 500 kr = 2,000 kr
Altenar:     quickcasino + dbet      2 × 500 kr = 2,000 kr
Gecko V2:    betsson + nordicbet     2 × 500 kr = 2,000 kr
Spectate:    888sport                1 × 500 kr = 1,000 kr
ComeOn:      comeon                  1 × 500 kr = 1,000 kr
Interwetten:                         1 × 500 kr = 1,000 kr
Vbet:                                1 × 500 kr = 1,000 kr
10bet:                               1 × 500 kr = 1,000 kr
Coolbet:                             1 × 500 kr = 1,000 kr

Total deposit: 6,000 kr
Total with bonuses: ~12,000 kr
Active providers: 12
```

### Kelly stakes at 12k bankroll

- 5% edge @ 2.0: ~150 kr
- 10% edge @ 2.0: ~300 kr
- Max cap (5%): 600 kr
- Avg stake: ~150 kr

### Session capacity

- 12 providers × 1,000 kr / 150 kr = ~80 bets per session
- ~12,000 kr wagered per session
- 1x wagering (1,000 kr): cleared in 1 session
- 6x wagering (6,000 kr): ~5 sessions
- 15x wagering (15,000 kr): ~10 sessions

### Sibling rotation

When a sibling goes dormant (balance 0, wagering cleared):
1. Withdraw balance
2. System suggests next cheapest sibling in cluster
3. Deposit on new sibling → cycle continues

## Existing Account Compatibility

For non-fresh accounts (e.g., the developer's own account):

- System reads existing `ProfileProviderBonus`, `ProfileProviderBalance`, `ProfileProviderLimit`
- Auto-assigns lifecycle states based on current data
- Providers with partial wagering → `wagering` state
- Providers with cleared bonuses → `playing` state
- Providers with limit records → `limited` state (still playable, LTD badge)
- Providers with 0 balance + completed bonus → `dormant`
- Providers not yet opened → `available`

No migration needed — the lifecycle is a view on existing data.

## Backend Changes

### New/modified files

1. **`backend/src/db/models.py`** (and `backend/src/db/profiles.py` if duplicate exists) — Add `trigger_mode` column to `ProfileProviderBonus`
2. **`backend/src/repositories/profile_repo.py`** — Update `start_bonus_trigger()` and `start_freebet_tracking()` to accept and store `trigger_mode`
3. **`backend/src/services/bet_service.py`** — Update trigger advancement logic to respect `trigger_mode` (single: check stake ≥ amount on settle, cumulative: check wagered_amount ≥ requirement)
4. **`backend/src/services/opportunity_service.py`** — New method: `get_play_session()` returning clusters with siblings, states, filtered opportunities, and recommended stakes
5. **`backend/src/api/routes/opportunities.py`** — New endpoint: `GET /api/play/session` returning session data
6. **`backend/src/config/providers.yaml`** — Add `trigger_mode` to each provider's bonus config

### Existing infrastructure reused

- `ProviderAllocator` — wagering urgency scoring
- `StakeCalculator` — Kelly sizing with bonus awareness
- `RiskCalculator` — limit detection and risk-adjusted stakes
- `PLATFORM_GROUPS` / `CANONICAL_MEMBERS` — cluster definitions
- `ProfileProviderBonus` — all bonus tracking
- `ProfileProviderLimit` — limit tracking
- Auto-advancement in `BetService.settle_bet()` — trigger → freebet/wagering → completed

## Frontend Changes

### New/modified files

1. **`frontend/src/components/Terminal/pages/ValuePage.tsx`** — Replace current cluster panel with Play session UI
2. **`frontend/src/components/Terminal/pages/ClusterPanel.tsx`** — Update: pills show balance count, auto-hide empty clusters, auto-advance logic
3. **`frontend/src/services/api.ts`** — New API call: `getPlaySession()`

### Reused

- Existing `OpportunityRow` component for bet display
- Existing `useBetMutations` for bet placement
- Existing `usePersistedState` for active cluster/provider persistence
