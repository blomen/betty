# Manual Bet Tab — Design Spec

**Date:** 2026-03-12
**Scope:** Frontend-only — new sub-tab in ValuePage for manually logging bets

## Problem

Sometimes a bet is placed manually (e.g., one leg of a planned hedge, a standalone value bet at a limited provider). The system needs to track it for bankroll/profit accuracy, but there's no automated opportunity to place from.

## Design

### Sub-tab

Add `"manual"` as the 4th sub-tab in ValuePage, alongside `value`, `boosts`, `mybets`.

### Form

Minimal freeform entry:

| Field | Type | Required | Stored as |
|-------|------|----------|-----------|
| Provider | Dropdown (existing providers) | Yes | `provider_id` |
| Description | Free text | Yes | `outcome` |
| Odds | Number input | Yes | `odds` |
| Stake | Number input | Yes | `stake` |
| Freebet | Checkbox toggle | No | `is_bonus` + `bonus_type: 'free_bet'` |

### Flow

1. User fills form, clicks Submit
2. Frontend calls `api.createBet()` with `bet_type: 'manual'`, no `event_id`, no `market`, no `start_time`
3. Bet appears in mybets → settle tab (no start_time = immediately settleable)
4. User settles via existing W/L/V buttons in mybets

### Filter Update

Add `b.bet_type === 'manual'` to `softBetFilter` in ValuePage so manual bets appear in the mybets sub-tab.

### What's NOT Changing

- No backend changes — reuses existing `createBet` + `editBet` APIs
- No new database columns or models
- No new API endpoints
