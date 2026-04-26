# Pinnacle Mirror Workflow Discovery

**Date:** 2026-04-26
**Domain used:** `pinnacle.se`
**Account:** rasmusblomen@gmail.com (acct `0070660274`, balance SEK 80.00)

Source: live discovery in the running mirror, captured via `/mirror/browser/debug-eval/pinnacle` and the XHR interceptor (`/mirror/stream` SSE).

---

## Login detection

Logged-in state is best detected by a successful response from the wallet endpoint. Once authenticated, the page polls `https://api.arcadia.pinnacle.se/0.1/wallet/balance` every ~30s; pre-auth the page hits `guest.api.arcadia.pinnacle.se/0.1/wallet/balance` instead.

Checks (in order of preference):

1. **API call** — `await fetch("https://api.arcadia.pinnacle.se/0.1/wallet/balance", {credentials:"include"})` returns 200 with a JSON body containing the balance. Cleanest because it confirms session cookies AND returns the balance in one call.
2. **DOM** — the top bar shows the account ID (e.g. `0070660274`) and balance ("SEK 80.00") next to a green "DEPONERA" button. Selector: scan top-bar text for currency code + amount (regex `/SEK\s*[\d.,]+/`).

Logged-out indicator: hitting `api.arcadia.pinnacle.se` returns 401 / 403, or the page shows the "Log in / Join" buttons in the top-right corner.

---

## Balance

- **XHR endpoint:** `GET https://api.arcadia.pinnacle.se/0.1/wallet/balance` (auto-polled every ~30s)
- **Response shape (intercepted via `balance_intercepted` SSE):** parses to `{available: 80.0}` (the interceptor extracts a single `balance` number — exact JSON shape inferred but consistent with `{available: float, currency: "SEK"}`)
- **Currency normalization:** displayed as `SEK 80.00` in the top bar; the API returns the raw number (no formatting). No FX needed for our SEK accounts; native SEK throughout.
- **DOM fallback:** top bar contains text matching `/SEK\s*([\d.,]+)/` — used by the existing `screenshot` route's balance scrape.

---

## History

Not directly captured during discovery (the user did not navigate to the bet history page). Inferred:

- Account history page: `https://www.pinnacle.se/en/account/bet-history/` (typical Pinnacle structure)
- Likely XHR: `https://api.arcadia.pinnacle.se/0.1/wagers/...` (Pinnacle's public API uses similar patterns)

**Action item for Task 6 implementer:** Either:
- (a) Walk the bet history page and intercept its XHRs (cleanest), OR
- (b) Hit `/0.1/wagers` / `/0.1/bets/history` via `_evaluate_api()` style fetches inside the page's session

This stays as a ⚠️ TODO for Task 6 — `sync_history` can ship as a stub returning `[]` for the first arb cycles, then be filled in with real history once we observe the endpoint.

---

## Event navigation

**URL pattern (verified):**
```
https://www.pinnacle.se/en/{sport}/{league-slug}/{home-slug}-vs-{away-slug}/{matchupId}/
```

Examples captured live:
- `https://www.pinnacle.se/en/soccer/sweden-allsvenskan/djurgardens-vs-hammarby/1629401631/`
- `https://www.pinnacle.se/en/soccer/spain-la-liga/rayo-vallecano-vs-real-sociedad/1629382109/`
- `https://www.pinnacle.se/en/soccer/china-super-league/chongqing-tonglianglong-vs-qingdao-west-coast/1629395352/`

**Critical mapping:** `matchupId` (the trailing path segment) is the same `matchupId` returned in the slip's quote XHR body and stored in `localStorage["Main:Betslip"]`. The server-side arb opportunity has its own `event_id`; mapping server's `event_id` → Pinnacle's `matchupId` is **NOT** 1:1 and likely requires a sport-specific ID lookup.

**Recommended mapping strategy for Task 6:**
- For each opp, the server already provides `display_home`, `display_away`, `sport`, `league`, `start_time`. Use these to build a Pinnacle search URL or directly construct the slug-based URL.
- If slug construction fails (team names differ between providers), fall back to: open the league page (`/en/{sport}/{league-slug}/matchups/`), find the matchup row whose home/away text matches, click it.

**Outcome buttons (verified):** All odds buttons across the entire site share class `market-btn` plus three CSS-modules-hashed classes. Stable selector:
```css
button.market-btn
```
On the homepage this returned 101 elements. On a single event page, the buttons live in the body of the matchup card.

To pick a specific outcome button by market type, walk the DOM up from the click target — the parent groups buttons under labelled rows like "Money Line" / "Spread" / "Total" / "1X2" with text labels inside the same card.

---

## Slip widget

**Framework:** React. Class names use CSS-modules hashes (e.g. `button-IFFsVVcXY2`, `pill-yZejyxICzt`, `inputBox-Dli1iDw9gB`) — these survive within a deployed bundle but break across Pinnacle's redeploys. Pair every hashed class with a semantic prefix (`market-btn`, `inputBox-`, `buttonWrapper-`) and prefer the prefix match.

**Slip storage location: `localStorage["Main:Betslip"]`** — JSON, source of truth for both selection AND stake.

Sample value captured live (after I clicked one outcome and wrote stake=25):

```json
{
  "Selections": [
    {
      "originTag": "h",
      "marketKey": "s;0;m",
      "safeKey": "0:moneyline:\"no-points\":no-side:non-teaser",
      "matchupId": 1629401631,
      "designation": "home",
      "wager": 25,
      "price": -133,
      "period": 0,
      "type": "moneyline",
      "score": 1,
      "cards": 0,
      "valid": true,
      "isReused": false,
      "isSportsBetting": true,
      "isSingle": false,
      "previousWagerValue": null,
      "oddsLastChangedTime": 1777206495687
    }
  ],
  "QuickBetSelections": [],
  "TeaserSelections": [...]
}
```

Field meanings:
- `wager`: stake in account currency (here SEK)
- `price`: **American odds** (e.g. `-133` ≈ 1.752 decimal). Need conversion: `price < 0 → decimal = 1 + 100/abs(price)`; `price > 0 → decimal = 1 + price/100`.
- `marketKey: "s;0;m"`: `period;handicap;market_type` — `s` = full-time, `0` = no handicap, `m` = moneyline. Other observed types: `h` = handicap (spread), `tt` = total points.
- `designation`: `"home" | "away" | "draw" | "over" | "under"` — maps to the outcome on the leg
- `oddsLastChangedTime`: epoch-ms; useful for staleness detection

**Live odds also stream via XHR:** every 1-3s while the slip has a selection, the page POSTs `/0.1/bets/straight/quote` with the current selection and gets a fresh `price` back. We already intercept this (`bet_intercepted` SSE event):
```
POST https://api.arcadia.pinnacle.se/0.1/bets/straight/quote
{
  "classes": [{"name":"Straight","price":-133.0}],
  "limits": [
    {"amount":3.68,"type":"minRiskStake"},
    {"amount":30657.66,"type":"maxRiskStake"},
    {"amount":2.77,"type":"minWinStake"},
    {"amount":23050.88,"type":"maxWinStake"}
  ],
  "selections": [{
    "designation":"home", "marketAltId":null, "marketId":3576208882,
    "marketKey":"s;0;m", "matchupId":1629401631, "price":-133.0
  }]
}
```

Both sources (localStorage + quote XHR) update in lock-step. **Use localStorage for `read_slip_odds`** (no extra IPC, idempotent, fast); use the quote XHR's `limits[]` to learn the per-quote stake caps (Task: stake-cap learning, deferred).

**Stake input (DOM):**
- `<input type="text" placeholder="Stake">` inside parent `.inputBox-Dli1iDw9gB`
- Sibling: `<input type="text" placeholder="Win">` (computed; written value triggers React's controlled-input re-derivation of the other field)
- React-controlled — needs the hidden setter pattern (verified working):
```js
const el = document.querySelector('input[placeholder="Stake"]');
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
setter.call(el, '25');
el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
```
Verified that `read_back === written` and the slip continued to re-quote with the new wager applied.

**`update_slip_stake` strategy — TWO viable paths:**

1. **localStorage write + storage event** (mirrors Altenar pattern). Update `Selections[0].wager`, dispatch `storage` event so React reads. Cleaner — no DOM coupling. UNVERIFIED whether Pinnacle's React store listens to `storage` events.
2. **DOM input write** (verified above). Less elegant but proven to work and trigger re-quote.

**Recommendation:** Implement DOM input write first (verified). Add localStorage path as a fallback or for the all-counter-legs-in-parallel batch update.

**Place button:**
```
<button class="button-l9TRHt6rdY fullWidth-RjvaOdiHkK ... primary-yLpfPClBYy button-HGjI8wfcU5">
  CONFIRM 1 SINGLE BET
</button>
```
Text starts with `CONFIRM` plus a count. Selector: `button:has-text("CONFIRM")` or grep buttons for `/^CONFIRM\b/i`.

When the slip is empty / awaiting a selection / awaiting login, the same button area shows other text — verified earlier as `CONTINUE 0 SELECTIONS` (gray, disabled) before any selection. So the button text is the slip-state indicator:
- `CONTINUE 0 SELECTIONS` (or similar) → empty
- `CONFIRM 1 SINGLE BET` (or `... N SINGLE BETS` for multiples) → ready

---

## Placement XHR

NOT directly observed (we did not click "Confirm" — the user has only SEK 80, didn't want to actually place).

**Strongly inferred from the quote endpoint:**
- URL: `POST https://api.arcadia.pinnacle.se/0.1/bets/straight/place` (or `/wager`)
- Request body: same shape as `/quote` body but with `acceptedPrice` and `wager` filled in
- Response: includes a `wagerNumber` or `betId` field

**Action for Task 6:** Stub `parse_placement_status` to handle the inferred shape; flag it as ⚠️ to fix once the first real placement intercepts. The runner does NOT need this to function — anchor placements happen on the soft-book side; Pinnacle is the **counter** that the user clicks Place for. We intercept whatever XHR comes out and record it.

---

## Open issues / unknowns

1. **History endpoint** — never observed. Stub `sync_history` returns `[]` until first navigation to the history page. Pending bets reconciliation will sit dormant for Pinnacle until this is implemented.
2. **Place XHR shape** — inferred from `/quote` analog but not confirmed. Will surface on first real placement.
3. **Spread / total `marketKey` format** — only observed `"s;0;m"` (full-time moneyline). Need samples for `"s;-2.5;h"` (spread -2.5) and `"s;2.5;tt"` (total over 2.5) — observe by clicking spread/total buttons in a future discovery pass.
4. **Account-level cookie / session expiry** — not measured. Assume standard ~24h.
5. **Odds price sign conversion edge cases** — American `-100` ↔ decimal `2.00`, `+100` ↔ `2.00`. Ensure the conversion handles boundary correctly (`abs(price) >= 100` always for valid Pinnacle prices).

---

## Summary for Task 6 implementer

What's confirmed and ready to wire:

| Method | Strategy |
|---|---|
| `check_login` | `await fetch("https://api.arcadia.pinnacle.se/0.1/wallet/balance", {credentials:"include"}).then(r=>r.ok)` |
| `sync_balance` | Same fetch, parse JSON `.available` (or whichever field; intercepted as a single number already) |
| `navigate_to_event` | Construct URL from sport+league+slug+matchupId; if mapping fails, fall back to league page + DOM find |
| `prep_betslip` | (1) Click `button.market-btn` for the right outcome; (2) wait for `localStorage["Main:Betslip"]` to populate; (3) write stake via the verified DOM-input-setter pattern |
| `read_slip_odds` | Read `localStorage["Main:Betslip"]`, parse JSON, take `Selections[0].price`, convert American → decimal |
| `update_slip_stake` | DOM input setter pattern (verified). Add localStorage write as a backup if the React store ever stops re-rendering. |
| `place_bet` | Mirror flow — user clicks Place on site; we intercept the placement XHR (shape TBD on first real fire) |
| `parse_placement_status` | Stub: any 200 response with a `wagerNumber`/`betId` → success. Refine on first real placement. |
| `parse_placement_response` | Extract `wagerNumber` or `betId` field |
| `sync_history` | Stub returning `[]` for now; ⚠️ TODO observe endpoint on first manual visit to history page |

Conversion helpers needed:
```python
def american_to_decimal(price: float) -> float:
    if price < 0:
        return 1.0 + 100.0 / abs(price)
    return 1.0 + price / 100.0
```
