# Interwetten Mirror Workflow Design

**Date:** 2026-04-15
**Status:** Approved
**Scope:** Wire Interwetten to the mirror workflow — settle, pending sync, balance, play bets

## Context

Interwetten is a proprietary platform (Sportsbook Software GmbH) — not Kambi, Altenar, or Gecko. Currently mapped to `GenericWorkflow` with incomplete intel JSON. The site is server-rendered HTML behind Cloudflare, so all interaction is DOM-based (no useful XHR/API to intercept).

Discovery was performed live on the logged-in site. All selectors and patterns below are verified.

## Architecture

New dedicated `InterwettenWorkflow(ProviderWorkflow)` in `firevsports/mirror/workflows/interwetten.py` with a backend mirror copy. Registered in `__init__.py` replacing the `GenericWorkflow` mapping.

No changes to `browser.py`, `play_loop.py`, `pending_loop.py`, or `router.py` — the workflow plugs into the existing interface.

## DOM Discovery Results

### Login Detection
- Balance text in header: element containing `\d+[,.]\d+ SEK`
- Fallback: presence of "Last Login" text in top bar

### Balance
- Header element with text like `816,11 SEK` (Swedish decimal comma format)
- Selector: text content matching `{amount} SEK` in the account area next to user initials
- Always visible on any page when logged in

### Bet History Page (`/en/journal/bets`)
- Tabs: All bets | Open bets | Won bets
- Table with columns: Date (ID), Type of bet, EVENT, Matchday, RESULT, TIP, Odds, STAKE SEK, PROFIT SEK
- Each row is an `<a>` link to `/en/journal/betdetail/{internal_id}`
- Bet ID embedded in Date cell text (e.g., `611403777`)
- Event text format: `"Team A - Team B (Market) -> Outcome / Odds"`
- Status: icon element with title "Lost" or "Won" inside EVENT cell; profit `---` means unsettled

### Event Page (`/en/sportsbook/e/{event_id}/{slug}`)
- `data-betting` JSON attributes on all market and outcome elements
- Market grid (`.s-market-grid`): `[market_id, event_id, "short_name", "market_type", false, " ", false, 0, unix_timestamp]`
- Outcome button (`.s-outcome`): `[outcome_id, "tip", "outcome_name", "outcome_name", "odds_comma", false]`
- CSS class `.s-outcome-selected` when outcome is in betslip
- CSS class pattern: `js-outcome-{outcome_id} js-market-outcome-{market_id}`

### Betslip (right panel)
- Appears when outcome is clicked
- Stake input: `#amount_{outcome_id}` (text input, placeholder "Stake")
- Submit button: `#BS_Button_Submit` (class `s-betslip-button s-sharebet-button-submit`)
- Stats: Number of bets, Total stake, Possible winnings
- CSRF token: hidden input `__RequestVerificationToken`
- JS-driven submission (no HTML form), via `bettingslip-17.min.js`

### Account Statement (`/en/journal/accountturnover`)
- Alternative to bet history — shows all transactions (stakes, wins, deposits)
- Columns: Date (ID), Category, Transaction, Credit SEK, Debit SEK, Balance SEK
- Not used for sync_history (Overview bets page is better structured)

## Method Implementations

### `check_login(page) → bool`
1. Query header for text matching `\d+[,.]\d+\s*SEK`
2. If found → logged in
3. Fallback: check for "Last Login" text
4. No navigation needed — works on any page

### `sync_balance(page) → float`
1. Read balance text from header (same element used for login check)
2. Parse Swedish format: `"816,11 SEK"` → strip SEK, replace comma → `816.11`
3. Works from any page — no navigation required

### `sync_history(page) → list[HistoryEntry]`
1. Navigate to `/en/journal/bets` (Overview bets page)
2. Wait for table to load
3. For each table row (`<a>` link wrapping `<tbody>`):
   - Extract bet_id from Date(ID) cell text (last token, e.g., `611403777`)
   - Extract event text from EVENT cell
   - Parse event text: regex `(.+?)\s*\((.+?)\)\s*->\s*(.+?)\s*/\s*(.+)` → event_name, market, outcome, odds
   - Extract stake from STAKE SEK cell
   - Extract profit from PROFIT SEK cell
   - Determine status: check for "Lost"/"Won" icon title in EVENT cell; if profit is `---` → `pending`
   - Payout: if won, payout = stake + profit; if lost, payout = 0; if pending, payout = None
4. Return list of `HistoryEntry`

### `navigate_to_event(page, bet) → bool`
1. Build URL: `/en/sportsbook/e/{bet.provider_event_id}/{slug}` (slug from bet metadata or just use event_id)
2. Navigate to URL
3. Wait for `.s-market-grid` elements (confirms event page loaded)
4. Return True on success, False on 404 or timeout

### `place_bet(page, bet, stake) → PlacementResult`

**Outcome selection:**
1. Find the correct market by scanning `.s-market-grid` elements and parsing `data-betting` JSON:
   - 1x2/moneyline: `market_type == "Match"`, match tip `"1"/"X"/"2"`
   - Spread: `market_type` contains "Handicap", match by handicap value
   - Total: `market_type` is "How many goals" or "Over/Under", match by line
2. Within the market, find the `.s-outcome` with matching tip/outcome
3. Click the outcome element → adds to betslip

**Stake entry:**
4. Wait for betslip panel (stake input visible)
5. Fill `#amount_{outcome_id}` with stake value
6. Verify "Possible winnings" updates (confirms stake accepted)

**Submission (guided mode):**
7. Return `PlacementResult(status="prepped", ...)` — user clicks "Submit betting slip" on site
8. `confirm_bet()` reads the confirmation state from DOM

### `prep_betslip(page, bet, stake) → PlacementResult`
Implements the two-phase flow:
1. Perform outcome selection + stake fill (steps 1-6 from place_bet)
2. Read live odds from betslip display
3. Return `PlacementResult(status="prepped", actual_odds=live_odds, actual_stake=stake)`

### `confirm_bet(page) → PlacementResult`
1. Click `#BS_Button_Submit`
2. Wait for confirmation: betslip changes to show placed bet or error
3. On success: extract bet_id if visible, return `PlacementResult(status="placed", ...)`
4. On error (odds changed, insufficient balance): return `PlacementResult(status="failed", reason=...)`

### `check_live_price(page, bet) → tuple[float | None, float | None]`
1. Find the outcome element matching bet's market + tip
2. Read odds from `data-betting` JSON (index 4)
3. Parse comma decimal → float
4. Calculate edge vs fair odds from bet metadata
5. Return `(live_odds, edge_pct)`

## Outcome Matching Map

| Our market | Interwetten market_type | Tip field |
|-----------|------------------------|-----------|
| `1x2` (home) | `"Match"` | `"1"` |
| `1x2` (draw) | `"Match"` | `"X"` |
| `1x2` (away) | `"Match"` | `"2"` |
| `moneyline` (home) | `"Match"` | `"1"` |
| `moneyline` (away) | `"Match"` | `"2"` |
| `spread` (home) | `"Handicap X:Y"` or `"Asian Handicap"` | `"1"` |
| `spread` (away) | `"Handicap X:Y"` or `"Asian Handicap"` | `"2"` |
| `total` (over) | `"How many goals"` / `"Over/Under"` | `" "` (match by outcome name "Over X.5") |
| `total` (under) | `"How many goals"` / `"Over/Under"` | `" "` (match by outcome name "Under X.5") |

## File Changes

| File | Change |
|------|--------|
| `firevsports/mirror/workflows/interwetten.py` | New — full workflow implementation |
| `firevsports/mirror/workflows/__init__.py` | Map `"interwetten"` → `InterwettenWorkflow` |
| `backend/src/mirror/workflows/interwetten.py` | Backend mirror copy |
| `backend/src/mirror/workflows/__init__.py` | Backend registry update |

## Not Changed

- `browser.py` — no network interception needed, Interwetten is DOM-only
- `play_loop.py` — workflow interface unchanged
- `pending_loop.py` — workflow interface unchanged
- `router.py` — no new endpoints needed
- Extraction layer — already working, provides `provider_event_id`

## Edge Cases

- **Odds format**: Interwetten uses comma decimals (`"4,6"` not `"4.60"`). All parsing must handle this.
- **Betslip already has items**: Clear betslip before adding new outcome. Click X buttons on existing selections.
- **Event not found**: Return False from navigate_to_event if page shows 404 or no markets.
- **Odds changed during placement**: Betslip may show warning — detect and report in PlacementResult.
- **Session timeout**: If balance element disappears mid-session, check_login will return False and play_loop will wait for re-auth.
