# Provider Improvements: 10bet, 888sport

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve two underperforming providers — 10bet (Pass 2 enrichment for football spread/total), 888sport (confirmed dead end, document limitation).

**Architecture:** 10bet gets Pass 2 event detail DOM enrichment (same pattern as ComeOn). 888sport gets documented as API-limited (no fix possible without authenticated session).

**Tech Stack:** Python 3.10+ / asyncio / Playwright `context.request` API / StandardEvent dataclass

---

## Chunk 2: 10bet Pass 2 Event Detail Enrichment

The investigation confirmed that football spread/total markets exist on 10bet event detail pages but are not in the competition listing DOM. Add Pass 2 enrichment (navigate to event detail pages, scrape Asian Handicap + Asian Total).

### Task 7: Write Pass 2 enrichment tests

**Files:**
- Create: `backend/tests/providers/test_tenbet_enrichment.py`

- [ ] **Step 1: Write tests for event detail market parsing**

The event detail page has markets in `ta-AggregatedMarket` containers. Asian Handicap has outcomes like `"Arsenal -1.5"` with odds. Asian Total has `"Over 2.5"` / `"Under 2.5"`.

The JS extraction snippet on the event detail page will output dicts matching this structure (from `page.evaluate()`). Tests match this output format.

```python
"""Tests for 10bet event detail market parsing."""
import pytest
from src.providers.tenbet import TenBetRetriever


class TestParseDetailSpread:
    def test_parse_asian_handicap(self):
        """JS extraction returns: name (team), point (handicap value), odds."""
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        # Raw format matches JS_EXTRACT_DETAIL_MARKETS output
        raw = {
            "spread": {
                "outcomes": [
                    {"name": "Arsenal", "point": "-1.5", "odds": "2.20"},
                    {"name": "Everton", "point": "+1.5", "odds": "1.67"},
                ],
            },
        }
        result = retriever._parse_detail_spread(raw["spread"])
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["point"] == -1.5
        assert result["outcomes"][1]["point"] == 1.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_spread({"outcomes": []}) is None


class TestParseDetailTotal:
    def test_parse_asian_total(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "total": {
                "outcomes": [
                    {"name": "Over 2.5", "odds": "1.95"},
                    {"name": "Under 2.5", "odds": "1.83"},
                ],
            },
        }
        result = retriever._parse_detail_total(raw["total"])
        assert result is not None
        assert result["type"] == "total"
        assert result["outcomes"][0]["point"] == 2.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_total({"outcomes": []}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_tenbet_enrichment.py -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Commit test file**


### Task 8: Implement Pass 2 event detail enrichment for 10bet

**Files:**
- Modify: `backend/src/providers/tenbet.py`

- [ ] **Step 1: Add JS snippet to extract spread/total from event detail page**

The event detail page has expandable market sections. The key selectors:
- `[class*="ta-AggregatedMarket"]` — market container
- `[class*="ta-MarketName-AsianHandicap"]` — Asian Handicap section (spread)
- `[class*="ta-MarketName-AsianTotal"]` or `[class*="ta-MarketName-ÖverUnder"]` — Asian Total / O/U section
- `[class*="ta-infoTextHandicap"]` — handicap point values
- `[class*="ta-price_text"]` — odds values
- `[class*="ta-participantName"]` — team names in outcomes

Add `JS_EXTRACT_DETAIL_MARKETS` constant and `_enrich_events_with_details()` method:

```python
JS_EXTRACT_DETAIL_MARKETS = """() => {
    const result = {spread: null, total: null};
    // Asian Handicap (spread)
    const ahEl = document.querySelector('[class*="ta-MarketName-AsianHandicap"]');
    if (ahEl) {
        const outcomes = [];
        ahEl.querySelectorAll('[class*="ta-selection"]').forEach(sel => {
            const name = sel.querySelector('[class*="ta-participantName"]');
            const price = sel.querySelector('[class*="ta-price_text"]');
            const info = sel.querySelector('[class*="ta-infoText"]');
            if (name && price) {
                outcomes.push({
                    name: name.textContent.trim(),
                    point: info ? info.textContent.trim() : '',
                    odds: price.textContent.trim()
                });
            }
        });
        if (outcomes.length >= 2) result.spread = {outcomes};
    }
    // Over/Under total
    const ouEl = document.querySelector(
        '[class*="ta-MarketName-ÖverUnder"], [class*="ta-MarketName-AsianTotal"]'
    );
    if (ouEl) {
        const outcomes = [];
        ouEl.querySelectorAll('[class*="ta-selection"]').forEach(sel => {
            const name = sel.querySelector('[class*="ta-participantName"], [class*="ta-label"]');
            const price = sel.querySelector('[class*="ta-price_text"]');
            if (name && price) {
                outcomes.push({
                    name: name.textContent.trim(),
                    odds: price.textContent.trim()
                });
            }
        });
        if (outcomes.length >= 2) result.total = {outcomes};
    }
    return result;
}"""
```

`_enrich_events_with_details()` uses a **page pool** pattern:
1. Create page pool with `asyncio.Queue` — open 4 extra pages via `context.new_page()`
2. Use `asyncio.Semaphore(4)` to throttle concurrent navigations
3. For each event: get page from pool → navigate to `/sports/{sport}/events/{eventId}` → evaluate JS → put page back
4. Cap at 150 events to avoid timeout
5. Close extra pages in `finally` block
6. Parse spread/total from JS output using `_parse_detail_spread()` and `_parse_detail_total()`

- [ ] **Step 2: Call enrichment at end of extract()**

After the existing competition scraping loop, add:
```python
# Pass 2: Enrich with event detail spread/total
if all_events:
    enriched = await self._enrich_events_with_details(all_events, sport)
    logger.info(f"[{self.provider_id}] {sport}: enriched {enriched}/{len(all_events)} with spread/total")
```

- [ ] **Step 3: Run tests and commit**


### Task 9: Smoke test 10bet enrichment

**Files:** None (manual verification)

- [ ] **Step 1: Run smoke test with football**

```bash
cd backend && python -c "
import asyncio, sys
sys.path.insert(0, '.')
async def test():
    from src.providers.tenbet import TenBetRetriever
    from src.core import BrowserTransport
    t = BrowserTransport(headless=True)
    r = TenBetRetriever({'id': '10bet', 'site_url': 'https://www.10bet.se'}, t)
    events = await r.extract('football')
    spread = sum(1 for e in events for m in e.markets if m['type'] == 'spread')
    total = sum(1 for e in events for m in e.markets if m['type'] == 'total')
    print(f'Events: {len(events)}, Spread: {spread}, Total: {total}')
    for e in events[:5]:
        print(f'  {e.name} | markets={[m[\"type\"] for m in e.markets]}')
    await t.close()
asyncio.run(test())
"
```
Expected: Football spread count > 0 (was always 0 before). Total count should remain similar or improve.

- [ ] **Step 2: Commit any fixes**


---

## Chunk 3: 888sport Documentation + Cleanup

### Task 10: Document 888sport API limitation

**Files:**
- Modify: `backend/src/providers/spectate.py` (add docstring note)
- Modify: `backend/src/config/providers.yaml` (update 888sport note)

- [ ] **Step 1: Add platform limitation documentation**

In `spectate.py`, update the class docstring:
```python
"""
Retriever for 888sport / Spectate based sites.
Uses BrowserTransport to bypass protections.

API limitation (confirmed 2026-03-14): The Spectate bulk API
(getUpcomingEvents) only returns 1x2/moneyline for football,
tennis, handball, MMA, esports, volleyball, rugby. Spread + total
markets are only available for basketball, ice_hockey, and baseball.
No event detail API exists — the SPA uses authenticated /load/state
which requires BankID login. This is a platform-level limitation,
not a parsing issue.
"""
```

In `providers.yaml`, update the 888sport comment:
```yaml
888sport:
  # ... existing config ...
  # NOTE (2026-03-14): Spectate bulk API only returns 1x2 for football/tennis/handball/etc.
  # Spread+total only available for basketball, ice_hockey, baseball.
  # No event detail API exists. This is a confirmed platform limitation.
```

- [ ] **Step 2: Commit**


### Task 11: End-to-end verification

- [ ] **Step 1: Run full extraction with all providers**

Start the backend and trigger a browser_soft tier extraction. Verify:
- 10bet: Football spread count > 0 (was always 0)
- 888sport: No regression (same event count, same market coverage)

- [ ] **Step 2: Commit any final fixes**
