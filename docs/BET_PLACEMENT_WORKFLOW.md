# Bet Placement & Tracking Workflow

## Overview

The bet placement and tracking workflow allows users to:
1. Browse opportunities (arbitrage/value/bonus) in a structured UI
2. Place bets with pre-filled forms (user places manually at bookmaker)
3. Track pending bets separately from available balance
4. Settle bets manually and see profit/ROI
5. View balance breakdown per provider

## Architecture

### Backend Changes

#### 1. Exposure Tracking API (`backend/src/api.py:355`)

**Endpoint:** `GET /api/bankroll/exposure`

Calculates pending exposure per provider:
- Queries all enabled providers
- For each provider, finds pending bets (result = "pending")
- Calculates pending exposure (sum of non-bonus bet stakes)
- Returns total balance, pending exposure, and per-provider breakdown

**Response:**
```json
{
  "total_balance": 1000.0,
  "total_pending": 150.0,
  "total_available": 1000.0,
  "providers": [
    {
      "provider_id": "unibet",
      "provider_name": "Unibet",
      "total_balance": 500.0,
      "pending_exposure": 75.0,
      "pending_bets_count": 3,
      "available": 500.0
    }
  ]
}
```

#### 2. Bet Validation (`backend/src/api.py:600`)

**Enhancement:** Added balance validation before bet creation
- Checks if provider has sufficient balance
- Only validates for non-bonus bets
- Returns 400 error with clear message if insufficient

### Frontend Changes

#### New Types (`frontend/src/types/index.ts`)

```typescript
// Bankroll Exposure
interface BankrollExposure {
  total_balance: number;
  total_pending: number;
  total_available: number;
  providers: ProviderExposure[];
}

// Opportunity with Event
interface OpportunityWithEvent extends Opportunity {
  event?: EventSummary;
}

// Bet Placement Form
interface BetPlacementData {
  opportunity_id?: number;
  event_id?: string;
  provider_id: string;
  market?: string;
  outcome?: string;
  odds: number;
  stake: number;
  is_bonus?: boolean;
  bonus_type?: string;
}
```

#### New Components

1. **BalanceBreakdownModal** - Detailed per-provider balance view
   - Shows total balance, pending, available
   - Per-provider cards with exposure
   - Low balance warnings

2. **OpportunitiesOverlay** - Full-screen opportunities browser
   - Filter by type (arb/value/bonus), sport, min value
   - Auto-refresh every 10s
   - Click opportunity to place bet
   - Keyboard: Cmd+O to open, ESC to close

3. **BetPlacementModal** - Pre-filled bet form
   - Event details from opportunity
   - Provider dropdown (switches between opportunity providers)
   - Auto-calculated Kelly stake for value bets
   - Balance validation
   - Potential return/profit preview

4. **BetsPanel** - Bet management interface
   - Filter tabs: All, Pending, Won, Lost, Void
   - Bet cards with status, provider, market, outcome
   - "Settle" button for pending bets
   - Auto-refresh every 10s
   - Keyboard: Cmd+B to open, ESC to close

5. **SettleBetModal** - Settle pending bet
   - Result buttons: Won / Lost / Void
   - Auto-fills payout based on result
   - Manual payout override
   - Profit/ROI preview

#### Enhanced Components

1. **TerminalHeader** - Clickable balance with exposure
   - Shows total balance + pending in yellow
   - Click to open BalanceBreakdownModal

2. **TerminalWindow** - Integrated overlays
   - Manages all modal states
   - Keyboard shortcuts:
     - Cmd+O: Open opportunities
     - Cmd+B: Open bets
     - Cmd+L: Clear chat
     - F5: Refresh

3. **WelcomeMessage** - Quick actions and shortcuts
   - "View Opportunities" button
   - "Manage Bets" button (shows count)
   - Keyboard shortcuts reference
   - Updated stats grid (replaced Events with Pending)

#### New Hooks

1. **useOpportunities** (`frontend/src/hooks/useOpportunities.ts`)
   - Fetches and enriches opportunities with event details
   - Supports filters: type, provider, market, sport, minValue
   - Auto-refresh every 10s
   - Returns opportunities with event data

2. **Enhanced useBankroll** - Added exposure tracking
   - Fetches exposure data alongside bankroll/stats
   - Returns exposure object

## User Workflow

### 1. Placing a Bet from Opportunity

1. Press **Cmd+O** (or click "View Opportunities")
2. OpportunitiesOverlay opens showing filtered opportunities
3. Click opportunity card
4. BetPlacementModal opens with pre-filled data:
   - Event: Arsenal vs Chelsea
   - Provider: Unibet
   - Outcome: Home
   - Odds: 2.5
   - Recommended Stake: $50 (Kelly)
5. User adjusts stake if needed
6. User **manually goes to Unibet and places bet**
7. User clicks "Confirm Bet" in modal
8. System:
   - Validates balance
   - Deducts $50 from Unibet
   - Creates pending bet record
   - Updates header balance display
   - Closes modal
9. Header shows: $950 available, $50 pending

### 2. Settling a Bet

1. Press **Cmd+B**
2. BetsPanel opens showing all bets
3. Click "Pending" tab
4. Find Arsenal bet, click "Settle"
5. SettleBetModal opens
6. User checks outcome on Unibet (Arsenal won!)
7. Click "Won" button
8. Payout auto-fills: $125 (stake * odds)
9. Click "Confirm"
10. System:
    - Marks bet as won
    - Adds $125 to Unibet balance
    - Removes from pending
    - Calculates profit: $75
11. Header shows: $1025 available, $0 pending

### 3. Viewing Balance Breakdown

1. Click balance in header
2. BalanceBreakdownModal opens
3. Shows:
   - Summary: Total, Pending, Available
   - Per-provider cards:
     - Unibet: $500 total, $50 pending, $450 available
     - Leovegas: $500 total, $0 pending, $500 available
4. Press ESC to close

## Keyboard Shortcuts

- **Cmd+O**: Open opportunities overlay
- **Cmd+B**: Open bets panel
- **Cmd+L**: Clear chat
- **F5**: Refresh data
- **ESC**: Close any overlay/modal

## Key Features

### Balance Tracking
- **Total Balance**: Sum of all provider balances
- **Pending Exposure**: Locked in pending bets (non-bonus)
- **Available**: Same as total (already deducted when bet placed)

### Kelly Stake Calculation
For value bets, recommended stake auto-calculates:
```
recommended_stake = (edge% / 100) * available_balance
```

### Balance Validation
- Checks provider balance before bet creation
- Only for non-bonus bets
- Returns clear error: "Insufficient balance: X available, Y required"

### Auto-Refresh
- Opportunities overlay: 10s
- Bets panel: 10s
- Bankroll: 30s

## Files Modified

### Backend (2 files)
1. `backend/src/api.py`
   - Added `/api/bankroll/exposure` endpoint (line 355)
   - Enhanced bet validation (line 600)

### Frontend (12 new files + 5 modified)

**New Files:**
1. `frontend/src/hooks/useOpportunities.ts`
2. `frontend/src/components/Terminal/BalanceBreakdownModal.tsx`
3. `frontend/src/components/Terminal/OpportunitiesOverlay.tsx`
4. `frontend/src/components/Terminal/BetPlacementModal.tsx`
5. `frontend/src/components/Terminal/BetsPanel.tsx`
6. `frontend/src/components/Terminal/SettleBetModal.tsx`

**Modified Files:**
1. `frontend/src/types/index.ts` - New interfaces
2. `frontend/src/services/api.ts` - getBankrollExposure()
3. `frontend/src/hooks/useBankroll.ts` - Exposure tracking
4. `frontend/src/hooks/useProfile.ts` - Fixed default profile
5. `frontend/src/hooks/index.ts` - Export useOpportunities
6. `frontend/src/components/Terminal/TerminalHeader.tsx` - Exposure display
7. `frontend/src/components/Terminal/TerminalWindow.tsx` - Overlay integration
8. `frontend/src/components/Terminal/WelcomeMessage.tsx` - Quick actions

## Testing Checklist

### Backend
- [x] Exposure endpoint returns correct data
- [x] Pending exposure excludes bonus bets
- [x] Balance validation prevents overdraft

### Frontend
- [x] Build completes without TypeScript errors
- [ ] Cmd+O opens opportunities
- [ ] Click opportunity opens bet form
- [ ] Stake validation works
- [ ] Balance deducts on bet placement
- [ ] Cmd+B opens bets panel
- [ ] Filter tabs work
- [ ] Settle modal auto-fills payout
- [ ] Balance updates on settlement
- [ ] ESC closes overlays
- [ ] Header shows pending exposure
- [ ] Click balance opens breakdown modal

### End-to-End
- [ ] Place bet from opportunity -> balance deducts -> shows in pending
- [ ] Settle bet as won -> balance increases -> shows correct profit
- [ ] View balance breakdown -> per-provider exposure accurate

## Success Criteria

- **Fast workflow**: Opportunity -> Bet placement = 2 clicks (Cmd+O, click opp, confirm)
- **Accurate tracking**: Balance deducts on placement, adds on settlement
- **Exposure visibility**: Pending bets shown separately from available balance
- **Manual control**: User places bets manually at bookmaker, confirms in app
- **Terminal aesthetic**: All overlays use green accent, monospace fonts, keyboard-first

## Next Steps

1. Start backend API server: `uvicorn backend.src.api:app --reload`
2. Start frontend dev server: `cd frontend && npm run dev`
3. Test workflow end-to-end
4. Monitor for errors in browser console
5. Verify balance tracking accuracy
6. Test all keyboard shortcuts
7. Ensure auto-refresh works correctly

## Notes

- All bet placements are **manual** - user places at bookmaker, then confirms in app
- Balance deduction happens immediately on confirmation (optimistic)
- Pending bets are tracked but don't affect available balance (already deducted)
- Auto-refresh ensures fresh data without manual intervention
- Terminal-style UI maintained throughout (green accent, monospace, ASCII symbols)
