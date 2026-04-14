# Gecko V2 Semi-Auto Workflow

**Date**: 2026-04-14
**Scope**: All 5 Gecko V2 providers (betsson, betsafe, nordicbet, spelklubben, bethard)
**Mode**: Semi-autonomous — auto-navigate to event, user places manually, interceptor records

## Problem

Gecko V2 providers currently have a minimal GUIDED workflow: login/balance work via the wallets API, but navigation and placement are fully manual. The user must find the event on the site themselves. Kambi and Altenar providers already auto-navigate to events using stored `provider_meta` IDs.

## Discovery (Verified via Live Browser)

### Event Page URL Pattern

Gecko V2 sites embed the sportsbook in a cross-origin iframe (`id="sb-iframe"`, hosted on `d-cf.{provider}playground.net`). The main site passes `eventId` as a query parameter to the iframe.

**Navigating `{site_url}{init_path}?eventId=f-{gecko_event_id}` loads the full event page with all markets.** No slug path needed — the `eventId` param alone is sufficient.

| Provider | Navigation URL |
|----------|---------------|
| betsson | `https://www.betsson.com/sv/odds?eventId=f-{id}` |
| betsafe | `https://www.betsafe.com/sv/odds?eventId=f-{id}` |
| nordicbet | `https://www.nordicbet.com/sv/odds?eventId=f-{id}` |
| spelklubben | `https://www.spelklubben.se/sv/betting?eventId=f-{id}` |
| bethard | `https://www.bethard.com/sv/sports?eventId=f-{id}` |

Init paths: spelklubben = `/sv/betting`, bethard = `/sv/sports`, others = `/sv/odds` (default).

### IDs Available During Extraction

The `gecko_v2.py` retriever already has access to:
- `event.id` — Gecko event ID (e.g. `5jN856YZJka9EiEN31i0pQ`)
- `market.id` — Market ID
- `market.marketTemplateId` — Template (e.g. `MW3W` = 1x2, `TGOUOT` = total)
- `selection.id` — Selection/outcome ID

These are just not stored in `provider_meta` today.

## Design

### 1. Extraction: Store `provider_meta` in `gecko_v2.py`

Add `provider_meta` at market and outcome level in `_parse_markets()`, matching the Kambi pattern:

**Market-level** (on the market dict):
```python
"provider_meta": {
    "event_id": event_id,
    "market_template": template_id,
}
```

**Outcome-level** (on each outcome dict):
```python
"provider_meta": {
    "selection_id": str(selection["id"]),
}
```

The storage pipeline already merges `{**market_meta, **outcome_meta}` into `odds.provider_meta`. No storage changes needed.

The `event_id` must be passed into `_parse_markets()` as a new parameter (currently it only receives `markets_raw`, `selections_by_market`, `sport`).

### 2. Navigation: Implement `navigate_to_event()` in `gecko.py`

Replace the no-op `navigate_to_event()` with:

```python
_INIT_PATHS: dict[str, str] = {
    "spelklubben": "/sv/betting",
    "bethard": "/sv/sports",
}
# Default: /sv/odds (betsson, betsafe, nordicbet)

async def navigate_to_event(self, page: Page, bet) -> bool:
    gecko_eid = getattr(bet, "gecko_event_id", "")
    if not gecko_eid:
        return True  # No ID — user navigates manually

    if f"eventId=f-{gecko_eid}" in (page.url or ""):
        return True  # Already on this event

    init_path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
    url = f"https://www.{self.domain}{init_path}?eventId=f-{gecko_eid}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    return True
```

The `init_path` uses a small dict (same pattern as Kambi's `_BALANCE_ENDPOINTS`). Default `/sv/odds` covers betsson/betsafe/nordicbet.

### 3. Play Loop: Add `gecko_event_id` to `_bet_ns()`

In `firevsports/mirror/play_loop.py`, add explicit Gecko field (same pattern as Kambi):

```python
ns.gecko_event_id = meta.get("event_id", "")
```

This avoids collision with top-level `event_id` (canonical UUID).

### 4. Backend Mirror Workflow

Mirror the same `navigate_to_event()` change in `backend/src/mirror/workflows/gecko.py`.

## Files Changed

| File | Change |
|------|--------|
| `backend/src/providers/gecko_v2.py` | Add `provider_meta` to market + outcome dicts in `_parse_markets()` |
| `firevsports/mirror/workflows/gecko.py` | Implement `navigate_to_event()` with eventId URL pattern |
| `backend/src/mirror/workflows/gecko.py` | Same navigate implementation |
| `firevsports/mirror/play_loop.py` | Add `gecko_event_id` to `_bet_ns()` |

## What Stays The Same

- **Login/balance**: Already wired via wallets API (`check_login`, `sync_balance`)
- **Placement**: Manual — user places on site, interceptor auto-records via `/coupons` response
- **History**: Interceptor catches `coupon-history` responses
- **Cluster membership**: `gecko_betsson` cluster unchanged

## Risks

- **`init_path` variance**: If a provider changes their betting page path, navigation breaks. Mitigation: read from `providers.yaml` config (single source of truth).
- **`eventId` format**: Gecko event IDs use base64-ish encoding with special chars (`_`, `-`). The `f-` prefix is required. Verified this works.
- **Cross-origin iframe**: We navigate the main page, not the iframe directly. The site's own routing passes the eventId to the iframe. If the site changes this integration, navigation would silently fail (event page not shown). The interceptor still works regardless.
