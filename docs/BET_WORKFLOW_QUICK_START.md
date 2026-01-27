# Bet Workflow Quick Start

## How to Use

### 1. View Opportunities
**Keyboard:** `Cmd+O` (or `Ctrl+O` on Windows)

**UI:** Click "View Opportunities" button on welcome screen

**Features:**
- Filter by type (arbitrage/value/bonus)
- Filter by sport
- Filter by minimum value %
- Auto-refreshes every 10 seconds
- Click any opportunity to place a bet

### 2. Place a Bet

**Triggered by:** Clicking an opportunity in the overlay

**Flow:**
1. Modal opens with pre-filled data from opportunity
2. For value bets: Recommended Kelly stake auto-calculated
3. Review event, provider, outcome, odds
4. Adjust stake if needed (shows potential return)
5. Check "Free Bet / Bonus Bet" if applicable
6. **Go to bookmaker website and place the bet manually**
7. Click "Confirm Bet" to track it in the system

**Validations:**
- Checks if you have sufficient balance
- Shows error if balance too low
- Must have stake > 0

**What Happens:**
- Stake deducted from provider balance immediately
- Bet saved as "pending"
- Balance in header updates
- Modal closes

### 3. Manage Bets
**Keyboard:** `Cmd+B` (or `Ctrl+B` on Windows)

**UI:** Click "Manage Bets" button on welcome screen

**Features:**
- View all bets or filter by status (Pending/Won/Lost/Void)
- See stake, odds, potential return
- For pending bets: Click "Settle" button
- Auto-refreshes every 10 seconds

### 4. Settle a Bet

**Triggered by:** Clicking "Settle" on a pending bet

**Flow:**
1. Modal shows bet summary
2. Check the result on bookmaker website
3. Click result button: Won / Lost / Void
4. Payout auto-fills:
   - Won: stake * odds
   - Lost: 0
   - Void: stake (refund)
5. Can manually override payout if needed
6. See profit/ROI preview
7. Click "Confirm"

**What Happens:**
- Bet marked with result (won/lost/void)
- Payout added to provider balance
- Profit/ROI calculated
- Bet moves out of pending list

### 5. View Balance Breakdown

**Trigger:** Click balance in header (shows total + pending)

**Shows:**
- Total balance across all providers
- Total pending exposure
- Total available balance
- Per-provider breakdown:
  - Balance
  - Pending bets count and value
  - Available balance
  - Low balance warning (if < $10)

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Cmd+O` | Open opportunities overlay |
| `Cmd+B` | Open bets panel |
| `Cmd+L` | Clear chat |
| `F5` | Refresh data |
| `ESC` | Close any overlay/modal |

## Visual Indicators

### Header
- **Green number**: Total balance
- **Yellow number in parentheses**: Pending exposure

Example: `$1000 (50 pending)` = $1000 total balance, $50 locked in pending bets

### Opportunity Types

| Type | Color | Icon | Metric |
|------|-------|------|--------|
| Arbitrage | Blue | ⇄ | Profit % |
| Value | Green | ↗ | Edge % |
| Bonus | Purple | 🎁 | Edge % |

### Bet Status

| Status | Color | Icon | Meaning |
|--------|-------|------|---------|
| Pending | Yellow | 🕐 | Waiting for settlement |
| Won | Green | ✓ | Bet won, profit added |
| Lost | Red | ✗ | Bet lost, stake gone |
| Void | Gray | ⚠ | Bet cancelled, stake refunded |

## Important Notes

1. **Manual Placement**: You must place bets manually at the bookmaker website. This system only tracks them.

2. **Immediate Deduction**: Balance is deducted immediately when you confirm a bet (optimistic tracking).

3. **Pending = Locked**: Pending bets are already deducted from your balance. The "pending" display shows how much is locked.

4. **Auto-Refresh**: Data refreshes automatically:
   - Opportunities: 10s
   - Bets: 10s
   - Bankroll: 30s

5. **Kelly Stakes**: For value bets, recommended stake = (edge% / 100) * available balance

6. **Bonus Bets**: Check "Free Bet / Bonus Bet" to prevent balance deduction (it's a free bet from bookmaker).

## Example Workflow

### Placing a Value Bet

```
1. Press Cmd+O
2. See: "Manchester United vs Liverpool - Value: 5.2%"
3. Click opportunity
4. Modal shows:
   - Provider: Unibet
   - Outcome: Home
   - Odds: 2.1
   - Recommended Stake: $26 (auto-calculated)
5. Go to Unibet website, place $26 on Man Utd
6. Return to app, click "Confirm Bet"
7. Header updates: $974 (26 pending)
```

### Settling the Bet

```
1. Press Cmd+B
2. Click "Pending" tab
3. Find Man Utd bet
4. Click "Settle"
5. Man Utd won!
6. Click "Won" button
7. Payout shows: $54.60 (26 * 2.1)
8. Profit: $28.60
9. ROI: +110%
10. Click "Confirm"
11. Header updates: $1002.60 (0 pending)
```

## Troubleshooting

**"Insufficient balance" error**
- Check balance breakdown (click balance in header)
- Reduce stake amount
- Wait for pending bets to settle

**Opportunity disappeared**
- Odds changed, opportunity no longer valid
- Refresh and look for new opportunities

**Wrong payout calculated**
- Override the auto-filled payout manually
- Enter actual payout from bookmaker

**Can't find pending bet**
- Check if it was already settled
- Use "All" tab instead of "Pending"

## Tips

1. **Use Kelly Stakes**: Auto-calculated stakes optimize long-term growth
2. **Filter Smart**: Use sport + min value filters to find best opportunities
3. **Quick Settlement**: Settle bets immediately after they complete for accurate tracking
4. **Monitor Exposure**: Keep pending exposure reasonable (don't lock all balance)
5. **Bonus Optimization**: Use bonus bet checkbox for free bets to avoid double deduction
