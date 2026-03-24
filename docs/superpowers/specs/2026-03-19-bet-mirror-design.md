# Bet Mirror — Inbound Bet Interception from Bookmaker Sites

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Spelklubben (Gecko V2) — extensible to other providers

## Problem

Bets placed manually on bookmaker sites must be re-entered manually into BankrollBBQ. This is tedious, error-prone, and loses data (exact timestamps, raw API payloads) that could feed future RL training.

## Solution

A persistent Playwright browser session that intercepts bet placement API responses in real-time, automatically logs them to BankrollBBQ via existing `BetService`, stores raw payloads for RL training, and notifies the frontend via SSE toast.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Headed Playwright Browser (persistent context)  │
│  User browses & bets on Spelklubben normally     │
└──────────────────┬──────────────────────────────┘
                   │ page.on('response')
                   │ filter: POST to /api/sb/*/betslip*
                   ▼
┌─────────────────────────────────────────────────┐
│  BetInterceptor                                  │
│  - Parses Gecko bet confirmation response        │
│  - Extracts: event, market, outcome, odds, stake │
│  - Stores raw request+response JSON              │
└──────────────────┬──────────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌──────────────────┐  ┌──────────────────┐
│  BetService      │  │  bet_traces      │
│  .create_bet()   │  │  (new table)     │
│  (existing)      │  │  raw JSON store  │
└──────────────────┘  └──────────────────┘
          │
          ▼
┌──────────────────┐
│  Broadcaster     │
│  .publish()      │──→ SSE to frontend
│  "bet_mirrored"  │    (toast notification)
└──────────────────┘
```

## Components

### 1. BetInterceptor (`backend/src/mirror/interceptor.py`)

Long-lived service that manages a headed Playwright browser and listens for bet placement responses.

**Lifecycle:**
- `start(provider_id)` → launches headed browser with persistent context, registers response listener, status = "listening"
- User bets → interceptor fires, parses, logs, notifies
- `stop()` → listener detached, browser stays open
- Browser close → interceptor auto-stops

**Response listener:**
- Registers listener at the **context** level via `context.on('page', ...)` to attach `page.on('response', callback)` to every page (including new tabs the user opens)
- Filtered to:
  - URL contains `/api/sb/` and betslip/bet-related path segments
  - Method is POST
  - Response body is inspected for bet confirmation vs rejection:
    - **Confirmed**: contains `betId` or equivalent → proceed to parse and log
    - **Rejected**: 200 response but body indicates rejection (odds changed, insufficient balance, stake limit) → store in `bet_traces` with `parse_status = "rejected"`, do NOT call `BetService`
- All other requests (odds, navigation, assets) are ignored

**Browser configuration:**
- Uses bare `async_playwright` persistent context directly — does NOT use `BrowserTransport`
- Resource blocking is **disabled** (user needs images, fonts, full UI to bet normally)
- Patchright stealth is applied for bot detection bypass
- Separate `user_data_dir` from extraction browsers (e.g., `data/mirror_profiles/spelklubben/`)

**Separation from extraction:**
- Completely separate Playwright context and browser instance
- Different `user_data_dir` than extraction browsers
- No shared locks, no interference with scheduler or orchestrator

### 2. Gecko Bet Parser (`backend/src/mirror/parsers/gecko.py`)

Provider-specific parser that extracts structured bet data from a Gecko V2 bet confirmation response.

**Extracted fields:**
- Event name, participants (home/away team names)
- Market type (mapped via existing `MARKET_TEMPLATE_MAP` patterns)
- Outcome (home/away/draw/over/under)
- Odds (decimal)
- Stake (SEK)
- Point value (for spread/total markets)
- Gecko event ID, bet ID (stored in `Bet.confirmation_id` for dedup and trace linking)

**Event matching:**
- Uses `normalize_team_name()` + rapidfuzz to find matching `Event` in DB
- If no match found: bet is still logged with `event_id = None`
- Unmatched bets can be linked manually via existing `PATCH /api/bets/{id}`

### 3. bet_traces Table (`backend/src/db/models.py`)

New table for raw payload storage. Append-only, never deleted.

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| timestamp | DATETIME | When intercepted |
| provider_id | TEXT | e.g. "spelklubben" |
| request_url | TEXT | Full API URL called |
| request_body | TEXT | Raw POST body (JSON string) |
| response_body | TEXT | Raw response (JSON string) |
| bet_id | INTEGER FK nullable | Link to bets table after successful create |
| provider_bet_id | TEXT nullable | Gecko's bet ID (searchable without parsing JSON) |
| parse_status | TEXT | "ok" / "failed" / "unmatched" / "rejected" |

### 4. Bet Creation Flow

When a bet response is intercepted:
1. Store raw trace to `bet_traces` (always, regardless of parse success or rejection)
2. Check if response indicates rejection → if so, set `parse_status = "rejected"` and stop
3. Parse confirmed response into structured bet fields
4. **Dedup on `confirmation_id`**: query `Bet` for existing row with same `confirmation_id` (Gecko bet ID). If found, skip creation (prevents double-logging from network retries or browser replay)
5. Call `BetService.create_bet()` with parsed fields via `asyncio.to_thread()` (BetService is synchronous, interceptor is async)
   - `bet_type = "mirror"` to distinguish from manually entered bets
   - `confirmation_id` = Gecko bet ID
   - Existing balance check deducts stake
   - Existing risk tracking records `risk_score_at_bet`
6. Update trace record with `bet_id` FK and `parse_status = "ok"`
7. Publish SSE event via `Broadcaster`

**Async/sync boundary:** The Playwright response callback is async. `BetService.create_bet()` uses synchronous SQLAlchemy. The interceptor bridges this via `asyncio.to_thread()` with a dedicated DB session per bet creation call.

### 5. Frontend Toast Notification

Global overlay toast that appears on any tab when a bet is mirrored.

- Listens for `"bet_mirrored"` SSE event type on existing SSE channel
- Shows: event name, market, odds, stake, matched status
- Auto-dismisses after 5 seconds
- Example: "Bet captured: Virginia United 1x2 @ 2.10 — 100 kr"

### 6. Start/Stop Interface

**API (primary — runs inside FastAPI process, has access to Broadcaster for SSE):**
```
POST /api/mirror/start?provider=spelklubben
POST /api/mirror/stop?provider=spelklubben
GET  /api/mirror/status                      # { running: true, provider: "spelklubben", since: "..." }
```

**CLI (convenience — delegates to running API server):**
```bash
python -m src.app mirror spelklubben        # POSTs to localhost:8000/api/mirror/start
python -m src.app mirror spelklubben --stop  # POSTs to localhost:8000/api/mirror/stop
```

The CLI is a thin HTTP client that delegates to the API server. This ensures the mirror browser runs in the same process as the Broadcaster (for SSE notifications) and the DB session factory.

## Discovery Phase

Before building the parser, we need to discover the exact Gecko bet placement endpoint:

1. Launch the mirror browser with a broad POST listener (all `/api/sb/**` POST requests logged)
2. Log in to Spelklubben
3. Place a minimum-stake bet
4. Inspect captured requests to identify the bet placement endpoint and response schema
5. Hardcode the endpoint pattern and build the parser

## File Structure

```
backend/src/mirror/
├── __init__.py
├── interceptor.py      # BetInterceptor — browser lifecycle + response listener
├── service.py          # MirrorService — orchestrates interceptor + bet creation + broadcast
└── parsers/
    ├── __init__.py
    └── gecko.py         # Gecko V2 bet response parser
```

## Future Extensions

- **Outbound automation (Phase 2):** Reverse the flow — app finds value bet, Playwright places it on the site. The interceptor becomes the confirmation listener.
- **Additional providers:** Add parsers for Kambi, ComeOn, Spectate. Each is a new file in `parsers/`.
- **Full RL loop (Phase 3):** `bet_traces` raw data feeds behavioral cloning. Combined with outbound automation, enables human-in-the-loop RL.

## Non-Goals

- No betslip manipulation (auto-filling stakes, clicking odds)
- No multi-site simultaneous mirroring (single provider at a time for v1)
- No mirror management UI (CLI/API only)
- No deposits/withdrawals interception
