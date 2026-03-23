# Market Depth + Deferred Matching Design

**Date**: 2026-03-23
**Goal**: Increase opportunity yield by (B) improving spread/total market depth on existing matched events, and (C) recovering soft provider events lost to timing gaps via deferred matching.

## Context

Current match rates are ~100% — when a soft provider extracts an event, it almost always matches Pinnacle. The bottleneck is not matching quality but:

1. **Market depth gaps**: Several providers extract ML-only, missing spread/total markets that could generate value opportunities.
2. **Timing gaps**: Soft events extracted before Pinnacle lists an event are silently dropped (`require_match=True` + no match → discard).

### Current Spread/Total Coverage (latest runs)

| Provider | Platform | Events | Spread% | Total% | Gap |
|----------|----------|--------|---------|--------|-----|
| Betsson | Gecko V2 | 1337 | 83% | 64% | Total underperforms |
| Unibet | Kambi | 686 | 108% | 152% | Good (multi-line) |
| Coolbet | Kambi | 179 | 287% | 595% | Good (multi-line) |
| VBet | VBet API | 561 | 52% | 43% | Both weak |
| 888sport | Spectate | 467 | **15%** | **14%** | Critical |
| Betinia | Altenar | 1146 | **33%** | 60% | Spread critical |
| Interwetten | Interwetten | 228 | **0%** | 59% | No spreads |

### Pinnacle Coverage Gaps (events with Pinnacle odds but no soft provider odds)

- ~109 Pinnacle-only events from today onwards
- Mostly obscure leagues (U19, 4th division, women's reserves) that Swedish books don't offer
- A few mainstream events (UCL, Serie A, Ligue 2) — likely far-future listings not yet in soft books, or timing gaps

---

## Part B: Spread/Total Market Depth Improvement

### Problem

888sport, Betinia, Interwetten, and VBet extract moneyline from listing pages but don't drill into event details for spread/total markets. This leaves 40-85% of potential spread/total opportunities uncaptured.

### Design

Add a **Pass 2 detail enrichment** step to underperforming providers, modeled on the existing ComeOn/Hajper pattern (which already does Pass 1: ML listing, Pass 2: event detail for spread/total).

#### Per-Provider Strategy

**888sport (Spectate)**
- Current: Bulk API returns ML for all sports, spread/total only for basketball/ice_hockey/baseball
- Fix: After Pass 1 bulk extraction, query the per-event detail endpoint for football/tennis/handball events missing spread/total
- Constraint: Spectate detail API is rate-limited — cap at 200 detail requests per run
- Expected gain: ~300-400 more spread/total odds for football alone

**Betinia (Altenar)**
- Current: Listing endpoint returns ML + total for most events, but spread is sparse
- Fix: Add event detail fetch for events where spread is missing. Altenar's `/event/{id}` endpoint returns all market types
- Constraint: Sequential detail requests to avoid rate limiting — cap at 200 per run
- Expected gain: ~400 more spread odds

**Interwetten**
- Current: Only extracts ML and totals, zero spreads
- Fix: Investigate Interwetten's API for spread/handicap market availability. If the API supports it, add spread extraction to the existing parser
- Constraint: May be a platform limitation (Interwetten may not offer spreads for all sports)
- Expected gain: Unknown until API is investigated

**VBet**
- Current: 52% spread, 43% total — partial extraction
- Fix: Audit which sports/leagues are missing spread/total and ensure the VBet parser handles all response formats for these markets
- Expected gain: ~200 more spread + total odds

#### Implementation Pattern

Each provider's enrichment follows the same pattern:

```python
# After Pass 1 (listing extraction)
events_needing_detail = [
    e for e in extracted_events
    if e.has_ml and not (e.has_spread and e.has_total)
]

# Pass 2: Detail enrichment (capped, prioritized by start_time proximity)
events_needing_detail.sort(key=lambda e: e.start_time)
for event in events_needing_detail[:MAX_DETAIL_REQUESTS]:
    detail = await fetch_event_detail(event.provider_event_id)
    if detail.spread:
        event.add_market(detail.spread)
    if detail.total:
        event.add_market(detail.total)
```

#### Priority Order

1. **888sport** — biggest gap (15% spread), most events (467), clear API path
2. **Betinia** — second biggest gap (33% spread), most events (1146)
3. **VBet** — moderate gap, audit-first approach
4. **Interwetten** — investigate feasibility first

---

## Part C: Deferred Matching (Store-Then-Match)

### Problem

When soft providers extract events before Pinnacle has listed them, the events are silently discarded:

```
Soft provider: "Team A vs Team B" (football, March 25)
  → require_match=True (Pinnacle has football)
  → _resolve_event_id() finds no match
  → return (False, 0, 0) — event and odds permanently lost
  → Pinnacle adds event 10 minutes later
  → Soft provider doesn't re-extract until 15-60 min cycle
```

### Design

#### New Table: `deferred_events`

```sql
CREATE TABLE deferred_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    normalized_home TEXT NOT NULL,
    normalized_away TEXT NOT NULL,
    start_time DATETIME NOT NULL,
    odds_json TEXT NOT NULL,          -- JSON array of {market, outcome, odds, point}
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    attempt_count INTEGER DEFAULT 0,
    UNIQUE(provider_id, sport, normalized_home, normalized_away, start_time)
);

CREATE INDEX idx_deferred_start ON deferred_events(start_time);
CREATE INDEX idx_deferred_sport ON deferred_events(sport);
```

#### Modified Flow in `store_provider_event()`

```python
# Current behavior (storage.py, ~line 690):
if require_match and matched_id is None:
    logger.debug(f"[{provider}] Skipped '{home}' vs '{away}' - no sharp match")
    return (False, 0, 0)

# New behavior:
if require_match and matched_id is None:
    logger.debug(f"[{provider}] Deferred '{home}' vs '{away}' - no sharp match")
    _store_deferred_event(session, event, provider_id, odds_data)
    return (False, 0, 0)  # Still returns False — no canonical event yet
```

#### New Function: `_store_deferred_event()`

Serializes the StandardEvent + odds into the `deferred_events` table. Uses INSERT OR IGNORE to handle duplicates (same provider/event/time combo from repeated extraction runs).

#### New Function: `resolve_deferred_events()`

Called as a post-hook after each Pinnacle extraction completes (in orchestrator, after sharp tier finishes):

```python
async def resolve_deferred_events(session, sharp_sports: set[str]):
    """Attempt to match deferred events against fresh Pinnacle data."""
    now = datetime.utcnow()

    # Query non-expired deferred events for sports Pinnacle covers
    deferred = session.query(DeferredEvent).filter(
        DeferredEvent.start_time > now,
        DeferredEvent.sport.in_(sharp_sports),
    ).all()

    recovered = 0
    expired = 0

    for de in deferred:
        event = de.to_standard_event()
        is_new, odds_count, _ = store_provider_event(
            session, event, de.provider_id,
            require_match=True,  # Still require Pinnacle match
            odds_data=de.deserialize_odds(),
        )

        if is_new or odds_count > 0:
            # Successfully matched — remove from buffer
            session.delete(de)
            recovered += 1
        else:
            de.attempt_count += 1

    # Cleanup: expired events or too many attempts
    expired_count = session.query(DeferredEvent).filter(
        (DeferredEvent.start_time <= now) | (DeferredEvent.attempt_count > 10)
    ).delete()

    session.commit()
    logger.info(f"Deferred resolution: {recovered} recovered, {expired_count} expired, {len(deferred) - recovered} still pending")

    return recovered, expired_count
```

#### Orchestrator Integration

In `orchestrator.py`, after the sharp tier completes and data is committed:

```python
# After Pinnacle extraction (existing code, ~line 586)
await self._commit_sharp_data()

# New: resolve deferred events against fresh Pinnacle data
sharp_sports = await self.get_cached_sports()
recovered, expired = await resolve_deferred_events(session, sharp_sports)
if recovered:
    logger.info(f"Recovered {recovered} deferred events after Pinnacle refresh")
```

#### Metrics

Add to extraction report:
- `events_deferred`: Count of new events added to buffer this run
- `events_recovered`: Count of deferred events successfully matched this run
- `events_expired`: Count of deferred events cleaned up (started or max attempts)

#### Constraints

- **Buffer is ephemeral**: Events auto-expire when `start_time` passes — no unbounded growth
- **Max 10 retry attempts**: Prevents permanent buffer residents
- **Same matching logic**: Uses identical `_resolve_event_id()` — no threshold relaxation
- **No new dependencies**: Pure SQLAlchemy + existing matching code
- **Idempotent**: INSERT OR IGNORE + attempt counting prevents duplicate processing

---

## What This Does NOT Change

- **Matching thresholds**: fuzzy_threshold=85, min_individual_score=75 stay the same (already ~100% match rate)
- **Team name normalization**: No changes needed
- **Market whitelist**: Still only 1x2/moneyline/spread/total
- **Scanner quality filters**: MIN_VALID_PROB_SUM, MAX_ODDS_RATIO unchanged

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| 888sport spread coverage | 15% | 60%+ |
| 888sport total coverage | 14% | 60%+ |
| Betinia spread coverage | 33% | 70%+ |
| VBet total coverage | 43% | 60%+ |
| Deferred events recovered per day | 0 (feature doesn't exist) | Track baseline |
| Total value opportunities per scan | Current baseline | 15-25% increase |

---

## Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Detail API rate limits (888sport, Betinia) | Slower extraction, potential blocks | Cap detail requests at 200/run, add backoff |
| Deferred table grows large | Slow queries | Auto-expire on start_time, max 10 attempts, index on start_time |
| Detail enrichment slows extraction tier | Delays opportunity scanning | Run enrichment as optional Pass 2, skip if extraction time exceeds threshold |
| Provider API changes | Enrichment breaks | Existing circuit breaker handles failures gracefully |
