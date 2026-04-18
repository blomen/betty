# Polymarket Mirror Verification & DOM Wiring

**Date**: 2026-04-18
**Status**: Approved
**Scope**: Polymarket only (first of three unlimited providers — Pinnacle and Cloudbet to follow in separate specs)

## Problem

After the recent Play-UI split into Section A (arb/soft-book DutchRunner) and Section B (value-bet unlimited providers), Polymarket end-to-end placement through Section B has not been verified. Uncommitted local edits in `firevsports/mirror/workflows/polymarket.py` tighten the DOM fallback (login detection, Cash balance scrape, outcome click, stake fill, live-cents readout from `prep_betslip`) but remain untested live.

## Goal

Walk the canonical 8-step mirror checklist against Polymarket, in both placement modes:

- **Mode A — SDK/API (autonomous)**: `has_api=True`. py-clob-client builds, signs, POSTs to `clob.polymarket.com/order`.
- **Mode B — DOM fallback**: `has_api=False`. Navigate slug URL → click outcome → type stake → user clicks Trade → interceptor records.

Fix whatever breaks at each step before moving to the next. One small commit per fix.

## Mode Selection

`PolymarketWorkflow.__init__` sets `self.has_api` based on whether `POLY_PRIVATE_KEY` + `POLY_FUNDER` env vars are present. Verification plan exercises the DOM path first (user's uncommitted changes target DOM), then re-enables SDK creds and re-runs steps 5-7 with `has_api=True`.

## 8-Step Verification Checklist

| # | Step | What to verify | Fix-site if broken |
|---|------|----------------|--------------------|
| 1 | Interception | `polymarket.com` page detection, `clob.polymarket.com/order` placement, `data-api.polymarket.com/trades` history, `data-api.polymarket.com/value` balance all fire SSE events | `browser.py` keyword lists + `_detect_provider` |
| 2 | Login detect | Fresh browser open → user logs in on site → `check_login()` flips true within 120s | `polymarket.py:_check_login_dom` (uncommitted edits) |
| 3 | Balance sync | Interceptor + DOM scrape agree with site nav "Cash $X.XX"; POST to `/api/bankroll/set/polymarket` fires once | `_sync_balance_dom` regex + leaf-element filter |
| 4 | Settlement | `sync_history()` returns real entries; 3-tier matcher assigns wins/losses; UI toast fires | Polymarket `sync_history` Data-API + DOM fallback |
| 5 | Navigate | Pop a bet from Section B queue → browser goes to `/event/{market_slug}` | `navigate_to_event` slug resolution |
| 6 | Prep + live odds | DOM: correct outcome button clicked (home vs away disambiguation), stake typed into Amount input, `_last_click_cents` captured, `prep_betslip` returns `actual_odds` converted from cents | `_navigate_and_fill_dom` + `prep_betslip` |
| 7 | Place + intercept | User clicks Trade → CLOB order POST intercepted → `/api/bets` record created with provider_bet_id | `_on_response` order-endpoint parser |
| 8 | Pending → next | Bet appears in Pending list; PendingLoop picks it up on next poll; Section B queue advances | — |

## Mode B (SDK) Re-run

Once DOM mode is green: set env vars, restart mirror, repeat steps 5-7 with `has_api=True`. Expected behavior: `prep_betslip` builds+signs via SDK, `place_bet` POSTs directly (no user click), interceptor still records the order response.

## Out of Scope

- Pinnacle and Cloudbet verification (separate specs)
- Arb/DutchRunner counter-leg wiring of Polymarket (already in place; not part of this pass)
- New features, UI changes, model changes
- Any regression of existing soft-book flows

## Files In-Play

| File | Why |
|------|-----|
| `firevsports/mirror/workflows/polymarket.py` | Main workflow — active uncommitted edits |
| `firevsports/mirror/browser.py` | Interception keyword sanity check |
| `firevsports/frontend/src/pages/PlayPage.tsx` | Section B rendering, Trade button wiring |
| `firevsports/mirror/provider_runner.py` | Per-runner state machine — sanity-check no regressions for unlimited path |

## Success Criteria

1. Select only Polymarket in the UI, hit Start.
2. Login, balance, settle — all green in both logs and UI.
3. One value bet placed via DOM mode end-to-end, recorded in DB.
4. One value bet placed via SDK mode end-to-end, recorded in DB.
5. No regressions in soft-book DutchRunner flow when Polymarket is deselected.
