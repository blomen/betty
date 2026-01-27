# Bonus Mode Implementation

Implemented 2026-01-27

## Overview

Added comprehensive "bonus mode" feature allowing users to find optimal hedges for bonus bets (free bets, qualifying bets) across multiple providers. The implementation follows the plan from the implementation document.

## What Was Implemented

### Backend Changes

#### 1. API Endpoints (`backend/src/api.py`)

**New Endpoint: POST /api/opportunities/bonus/match**
- Finds the best hedge for a bonus bet
- Accepts event ID, market, anchor provider/outcome/odds/stake
- Optionally filters by counterpart providers
- Returns hedge details with stake calculation and retention percentage

**Enhanced Endpoint: GET /api/opportunities**
- Added filters: `provider1`, `provider2`, `providers` (comma-separated)
- Added filters: `market`, `sport`, `min_value`
- Supports filtering opportunities by multiple criteria

**New Schema: BonusMatchRequest**
```python
class BonusMatchRequest(BaseModel):
    event_id: str
    market: str
    anchor_provider: str
    anchor_outcome: str
    anchor_odds: float
    anchor_stake: float
    is_free_bet: bool = False
    counterpart_providers: Optional[list[str]] = None
```

**Updated Schema: ProfileUpdate**
- Added: `min_retention_pct`, `preferred_counterparts`, `bonus_enabled`

#### 2. Database Model (`backend/src/db/models.py`)

**Updated Profile Model**
- `min_retention_pct: Float` - Minimum retention % for free bets (default: 80%)
- `preferred_counterparts: String` - JSON list of preferred providers
- `bonus_enabled: Boolean` - Toggle bonus mode on/off (default: True)

**Migration Required**: Existing databases need to restart the API to apply schema changes.

#### 3. CLI Tool (`backend/scripts/bonus_matcher.py`)

Command-line tool for testing bonus matching:
```bash
python scripts/bonus_matcher.py \
    --event "football:arsenal:chelsea:20260127" \
    --market "1x2" \
    --anchor-provider unibet \
    --anchor-outcome home \
    --anchor-odds 2.5 \
    --stake 100 \
    --free-bet
```

Features:
- Find best hedge for any event/market combination
- Filter by counterpart providers
- Displays retention %, profit/loss
- Works with existing database

### Frontend Changes

#### 4. TypeScript Types (`frontend/src/types/index.ts`)

**New Interfaces**:
```typescript
interface BonusMatchRequest {
  event_id: string;
  market: string;
  anchor_provider: string;
  anchor_outcome: string;
  anchor_odds: number;
  anchor_stake: number;
  is_free_bet: boolean;
  counterpart_providers?: string[];
}

interface BonusMatch {
  event_id: string;
  market: string;
  anchor_provider: string;
  anchor_outcome: string;
  anchor_odds: number;
  anchor_stake: number;
  hedge_provider: string;
  hedge_outcome: string;
  hedge_odds: number;
  hedge_stake: number;
  qualifying_loss: number;
  retention_pct: number;
}
```

**Updated Profile Interface**:
```typescript
interface Profile {
  // ... existing fields
  min_retention_pct: number;
  preferred_counterparts: string[];
  bonus_enabled: boolean;
}
```

#### 5. API Client (`frontend/src/services/api.ts`)

**New Method**:
```typescript
async findBestHedge(request: BonusMatchRequest): Promise<BonusMatch>
```

**Enhanced Method**:
```typescript
async getOpportunities(
  type?: 'arbitrage' | 'value' | 'bonus',
  activeOnly = true,
  provider1?: string,
  provider2?: string,
  providers?: string,
  market?: string,
  sport?: string,
  minValue?: number
): Promise<{ opportunities: Opportunity[]; count: number }>
```

**Updated Method**:
```typescript
async updateProfile(data: {
  // ... existing fields
  min_retention_pct?: number;
  preferred_counterparts?: string[];
  bonus_enabled?: boolean;
}): Promise<{ success: boolean }>
```

#### 6. React Hook (`frontend/src/hooks/useBonusMode.ts`)

State management hook for bonus mode:
```typescript
const {
  findHedge,      // (request: BonusMatchRequest) => Promise<void>
  clearResult,    // () => void
  result,         // BonusMatch | null
  isLoading,      // boolean
  error           // string | null
} = useBonusMode();
```

#### 7. Formatter Utility (`frontend/src/utils/formatters.ts`)

**New Function**:
```typescript
function formatRetention(retention: number): { text: string; color: string }
```
- Green (>=90%): Excellent retention
- Yellow (>=80%): Good retention
- Red (<80%): Poor retention

## Usage Examples

### Backend API

**Find Best Hedge**:
```bash
curl -X POST http://localhost:8000/api/opportunities/bonus/match \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "football:arsenal:chelsea:20260127",
    "market": "1x2",
    "anchor_provider": "unibet",
    "anchor_outcome": "home",
    "anchor_odds": 2.5,
    "anchor_stake": 100,
    "is_free_bet": true
  }'
```

**Response**:
```json
{
  "event_id": "football:arsenal:chelsea:20260127",
  "market": "1x2",
  "anchor_provider": "unibet",
  "anchor_outcome": "home",
  "anchor_odds": 2.5,
  "anchor_stake": 100,
  "hedge_provider": "bet365",
  "hedge_outcome": "away",
  "hedge_odds": 2.1,
  "hedge_stake": 71.43,
  "qualifying_loss": -78.57,
  "retention_pct": 78.6
}
```

**Filter Opportunities**:
```bash
# Get all opportunities between unibet and bet365
curl "http://localhost:8000/api/opportunities?provider1=unibet&provider2=bet365"

# Get bonus opportunities with minimum 80% retention
curl "http://localhost:8000/api/opportunities?type=bonus&min_value=80"

# Get arbitrage opportunities in football
curl "http://localhost:8000/api/opportunities?type=arbitrage&sport=football"
```

**Update Profile**:
```bash
curl -X PUT http://localhost:8000/api/profile \
  -H "Content-Type: application/json" \
  -d '{
    "min_retention_pct": 85.0,
    "preferred_counterparts": ["bet365", "betsson"],
    "bonus_enabled": true
  }'
```

### CLI Tool

```bash
# Free bet matching
python scripts/bonus_matcher.py \
    --event "football:arsenal:chelsea:20260127" \
    --market "1x2" \
    --anchor-provider unibet \
    --anchor-outcome home \
    --anchor-odds 2.5 \
    --stake 100 \
    --free-bet

# Qualifying bet with specific counterparts
python scripts/bonus_matcher.py \
    --event "football:arsenal:chelsea:20260127" \
    --market "1x2" \
    --anchor-provider unibet \
    --anchor-outcome home \
    --anchor-odds 2.0 \
    --stake 50 \
    --counterparts bet365,betsson
```

### Frontend Hook

```typescript
import { useBonusMode } from '@/hooks';

function BonusMatcher() {
  const { findHedge, result, isLoading, error } = useBonusMode();

  const handleFindHedge = async () => {
    await findHedge({
      event_id: "football:arsenal:chelsea:20260127",
      market: "1x2",
      anchor_provider: "unibet",
      anchor_outcome: "home",
      anchor_odds: 2.5,
      anchor_stake: 100,
      is_free_bet: true,
    });
  };

  if (isLoading) return <div>Finding best hedge...</div>;
  if (error) return <div>Error: {error}</div>;
  if (result) {
    return (
      <div>
        <h3>Best Hedge Found</h3>
        <p>Hedge: {result.hedge_provider} @ {result.hedge_odds}</p>
        <p>Stake: ${result.hedge_stake}</p>
        <p>Retention: {result.retention_pct}%</p>
      </div>
    );
  }

  return <button onClick={handleFindHedge}>Find Hedge</button>;
}
```

## Key Concepts

### Free Bet (SNR - Stake Not Returned)
- Stake is "free" (not deducted from balance)
- Only profit returned on win
- Retention % = guaranteed profit / free bet value
- Example: $100 free bet @ 2.5 odds
  - Win: Profit = $150 (no stake returned)
  - Hedge @ 2.1 odds: Stake $71.43
  - Guaranteed: $150 - $71.43 = $78.57 (78.6% retention)

### Qualifying Bet
- Real money staked
- Both stake and profit returned on win
- Minimize loss while covering all outcomes
- Example: $50 qualifying bet @ 2.0 odds
  - Total return if win: $100
  - Hedge to guarantee $100 return
  - Total staked vs return = loss amount

### Retention Percentage
- **>90%**: Excellent - very close odds between providers
- **80-90%**: Good - reasonable value extraction
- **<80%**: Poor - significant gap in odds

## Database Migration

If you have an existing database, the new Profile fields will be added automatically when the API starts. No manual migration needed.

To verify:
```bash
cd backend
python -c "
from src.db.models import init_db, get_session, Profile
init_db()
db = get_session()
profile = db.query(Profile).first()
if profile:
    print(f'min_retention_pct: {profile.min_retention_pct}')
    print(f'preferred_counterparts: {profile.preferred_counterparts}')
    print(f'bonus_enabled: {profile.bonus_enabled}')
db.close()
"
```

## Testing

1. **Backend Unit Tests**: Bonus matching logic tested in plan validation
2. **API Integration Tests**: Use curl or Postman to test endpoints
3. **CLI Tool**: Use `scripts/bonus_matcher.py` to test against real data
4. **Frontend**: Hook can be tested with browser console

## Files Modified

### Backend
- `backend/src/api.py` - Added endpoints and schemas
- `backend/src/db/models.py` - Updated Profile model
- `backend/scripts/bonus_matcher.py` - New CLI tool

### Frontend
- `frontend/src/types/index.ts` - Added types
- `frontend/src/services/api.ts` - Added API methods
- `frontend/src/hooks/useBonusMode.ts` - New hook
- `frontend/src/hooks/index.ts` - Export bonus mode hook
- `frontend/src/utils/formatters.ts` - Added retention formatter

## Future Enhancements

Not implemented yet (optional):
1. Auto-detection service for bonus opportunities in pipeline
2. UI components for bonus mode interface
3. Opportunity storage in database with type="bonus"
4. Notification system for high-retention opportunities

## Notes

- All bonus matching logic already existed in `backend/src/analysis/bonus.py`
- Implementation focused on API integration and frontend wiring
- No changes to core matching algorithms
- Maintains backwards compatibility with existing features
- Database schema changes are additive (no breaking changes)
