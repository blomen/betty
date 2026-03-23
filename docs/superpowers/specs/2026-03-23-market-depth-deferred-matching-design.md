# Market Depth + Deferred Matching Design

**Date**: 2026-03-23
**Goal**: Increase opportunity yield by (B) improving spread/total market depth on existing matched events, and (C) recovering soft provider events lost to timing gaps via deferred matching.

## Context

Current match rates are ~100% — when a soft provider extracts an event, it almost always matches Pinnacle. The bottleneck is not matching quality but:

1. **Market depth gaps**: Several providers have low spread/total coverage despite having extraction code for these markets — likely bugs or platform limitations masking available data.
2. **Timing gaps**: Soft events extracted before Pinnacle lists an event are silently dropped (`require_match=True` + no match → discard).

### Current Spread/Total Coverage (latest runs)

| Provider | Platform | Events | Spread% | Total% | Gap |
|----------|----------|--------|---------|--------|-----|
| Betsson | Gecko V2 | 1337 | 83% | 64% | Total underperforms |
| Unibet | Kambi | 686 | 108% | 152% | Good (multi-line) |
| Coolbet | Kambi | 179 | 287% | 595% | Good (multi-line) |
| VBet | VBet API | 561 | 52% | 43% | Both weak |
| 888sport | Spectate | 467 | **15%** | **14%** | Confirmed platform limit |
| Betinia | Altenar | 1146 | **33%** | 60% | Enrichment exists but gaps remain |
| Interwetten | Interwetten | 228 | **0%** | 59% | Pass 2 exists but 0% spreads = likely bug |

### Pinnacle Coverage Gaps (events with Pinnacle odds but no soft provider odds)

- ~109 Pinnacle-only events from today onwards
- Mostly obscure leagues (U19, 4th division, women's reserves) that Swedish books don't offer
- A few mainstream events (UCL, Serie A, Ligue 2) — likely far-future listings or timing gaps

---

## Part B: Spread/Total Market Depth Improvement

### Problem

Several providers have surprisingly low spread/total coverage. Investigation reveals a mix of confirmed platform limitations, existing-but-underperforming enrichment code, and likely bugs.

### Root Cause Analysis Per Provider

**888sport (Spectate) — CONFIRMED PLATFORM LIMITATION**
- Spectate's bulk `getUpcomingEvents` API only returns spread/total for basketball, ice_hockey, and baseball
- Football, tennis, handball, MMA, esports, volleyball, rugby: **moneyline only**
- No per-event detail API exists — the SPA uses `/load/state` which requires BankID login
- This is documented in `spectate.py` (lines 17-22), confirmed 2026-03-14
- **Action: None possible.** 888sport stays at 15% spread coverage. Remove from improvement targets.

**Betinia (Altenar) — EXISTING ENRICHMENT, INVESTIGATE GAPS**
- `altenar.py` already has `_enrich_missing_spreads()` (line 512) using `GetEventDetails` endpoint
- Batched with 50-event chunks, semaphore of 20 concurrent, cap at 200 events
- **Football is explicitly excluded** (line 499: `sport != 'football'`) with comment "Football on Altenar has no spread markets at all (platform limitation)"
- The remaining 33% spread gap is likely: (a) football events (no spreads available), (b) events exceeding the 200-cap, or (c) sports where enrichment fails silently
- **Action: Investigate** — query DB for Betinia spread coverage by sport to identify where the gap is. If football is the entire gap, it's a platform limitation. If non-football sports are missing, debug the enrichment code.

**Interwetten — EXISTING PASS 2, LIKELY BUG**
- `interwetten.py` already implements a two-pass strategy (documented lines 8-22)
- `SPREAD_LABELS` and `TOTAL_LABELS` constants exist (lines 69-70)
- Pass 2 navigates to event detail pages and extracts via JS (lines 234-249)
- Despite this, the metrics show **0% spread coverage** — this is almost certainly a parsing bug or navigation failure in the detail page extraction
- **Action: Debug** — add logging to Pass 2, check if detail pages are loading, verify JS extraction selectors still match the current DOM.

**VBet — EXISTING PASS 2, PARTIAL FAILURE**
- `vbet.py` has a dedicated Pass 2 WebSocket request for `OverUnder`, `Handicap`, `AsianHandicap` (lines 433-486)
- Markets are merged into existing events (lines 477-482)
- 52% spread / 43% total suggests some sports or leagues fail to return these markets
- **Action: Investigate** — query DB for VBet spread/total coverage by sport. Identify which sports return 0% and whether that's a platform limitation or extraction bug.

**Betsson (Gecko V2) — MODERATE GAP**
- 83% spread, 64% total — decent but not complete
- Gecko V2 uses `events-table` API which should return all market types
- Total gap may be due to certain sports/leagues not offering totals on Betsson
- **Action: Investigate** — query by sport, determine if gaps are platform limitations.

### Investigation Queries

Before implementing fixes, run these diagnostic queries:

```sql
-- Betinia spread coverage by sport
SELECT sport,
    COUNT(DISTINCT event_id) as events,
    SUM(CASE WHEN market = 'spread' THEN 1 ELSE 0 END) as spread_odds,
    SUM(CASE WHEN market = 'total' THEN 1 ELSE 0 END) as total_odds
FROM odds WHERE provider_id = 'betinia'
AND updated_at > datetime('now', '-1 day')
GROUP BY sport ORDER BY events DESC;

-- Same for VBet, Interwetten, Betsson
-- Replace provider_id accordingly
```

### Priority Order (revised)

1. **Interwetten** — 0% spreads despite existing code = almost certainly a bug. Highest ROI fix.
2. **Betinia** — investigate whether 33% gap is all football (unfixable) or includes other sports (fixable)
3. **VBet** — investigate which sports are missing, fix extraction bugs
4. **Betsson** — investigate total gap by sport
5. **888sport** — no action possible (platform limitation)

### Implementation Approach

For each provider, the workflow is:
1. Run diagnostic queries to identify exactly which sports/leagues have gaps
2. Add DEBUG logging to existing enrichment code to trace failures
3. Fix bugs found (DOM selectors, WebSocket filters, API parsing)
4. If cap (200 events) is the bottleneck, evaluate increasing or prioritizing by start_time proximity

No new enrichment architecture needed — the code exists, it just needs debugging.

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
    markets_json TEXT NOT NULL,        -- JSON: StandardEvent.markets list
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    attempt_count INTEGER DEFAULT 0,
    UNIQUE(provider_id, sport, normalized_home, normalized_away, start_time)
);

CREATE INDEX idx_deferred_start ON deferred_events(start_time);
CREATE INDEX idx_deferred_sport ON deferred_events(sport);
```

Key difference from initial draft: `markets_json` stores the full `StandardEvent.markets` list (not a separate odds format), so `to_standard_event()` can reconstruct a proper `StandardEvent` with markets included.

#### Modified Flow in `store_provider_event()`

```python
# Current behavior (storage.py, ~line 690):
if require_match and matched_id is None:
    logger.debug(f"[{provider}] Skipped '{home}' vs '{away}' - no sharp match")
    return (False, 0, 0)

# New behavior:
if require_match and matched_id is None:
    logger.debug(f"[{provider}] Deferred '{home}' vs '{away}' - no sharp match")
    _store_deferred_event(session, event, provider_id)
    return (False, 0, 0)  # Still returns False — no canonical event yet
```

#### New Function: `_store_deferred_event()`

Serializes the StandardEvent (including `markets`) into the `deferred_events` table. Uses INSERT OR REPLACE (upsert) so re-extractions update buffered odds to latest values rather than serving stale data.

#### New Function: `resolve_deferred_events()`

Called as a post-hook after each Pinnacle extraction completes. Must run within the orchestrator context to access warm `event_cache` and `date_index`.

```python
async def resolve_deferred_events(
    self,  # Orchestrator method — access to event_cache, date_index
    session,
    sharp_sports: set[str],
):
    """Attempt to match deferred events against fresh Pinnacle data."""
    now = datetime.utcnow()

    deferred = session.query(DeferredEvent).filter(
        DeferredEvent.start_time > now,
        DeferredEvent.sport.in_(sharp_sports),
    ).all()

    recovered = 0

    for de in deferred:
        event = de.to_standard_event()  # Reconstructs StandardEvent with markets
        is_new, odds_count, _ = store_provider_event(
            session,
            event,
            de.provider_id,
            event_cache=self.event_cache,
            date_index=self.event_cache_by_date,
            require_match=True,
            sharp_odds_cache=self.sharp_odds_cache,
        )

        if is_new or odds_count > 0:
            session.delete(de)
            recovered += 1
        else:
            de.attempt_count += 1

    # Cleanup: expired events or stale entries (>6 hours old)
    six_hours_ago = now - timedelta(hours=6)
    expired_count = session.query(DeferredEvent).filter(
        (DeferredEvent.start_time <= now) | (DeferredEvent.created_at < six_hours_ago)
    ).delete()

    session.commit()
    logger.info(
        f"Deferred resolution: {recovered} recovered, "
        f"{expired_count} expired, {len(deferred) - recovered} still pending"
    )
    return recovered, expired_count
```

Key corrections from initial draft:
- Method on orchestrator (not standalone) — access to `event_cache`, `date_index`, `sharp_odds_cache`
- Passes all required `store_provider_event()` parameters matching actual signature
- Markets carried inside `StandardEvent.markets`, not a separate `odds_data` param
- Time-based TTL (6 hours) instead of attempt-count TTL — avoids premature cleanup when sharp runs frequently
- INSERT OR REPLACE instead of INSERT OR IGNORE — keeps buffered odds fresh

#### Orchestrator Integration

In `orchestrator.py`, after the sharp tier completes and data is committed:

```python
# After Pinnacle extraction (existing code, ~line 586)
await self._commit_sharp_data()

# New: resolve deferred events against fresh Pinnacle data
sharp_sports = await self.get_cached_sports()
recovered, expired = await self.resolve_deferred_events(session, sharp_sports)
if recovered:
    logger.info(f"Recovered {recovered} deferred events after Pinnacle refresh")
```

#### Metrics

Add to `ProviderRunMetrics` (or use existing `notes` JSON field):
- `events_deferred`: Count of new events added to buffer this run
- `events_recovered`: Count of deferred events successfully matched this run
- `events_expired`: Count of deferred events cleaned up

#### Constraints

- **Buffer is ephemeral**: Events auto-expire when `start_time` passes or after 6 hours
- **Same matching logic**: Uses identical `_resolve_event_id()` — no threshold relaxation
- **No new dependencies**: Pure SQLAlchemy + existing matching code
- **Idempotent**: INSERT OR REPLACE + time-based cleanup prevents duplicate processing
- **Warm caches required**: Must run within orchestrator context after Pinnacle extraction

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
| Interwetten spread coverage | 0% | 50%+ (if platform supports it) |
| Betinia spread coverage (non-football) | TBD (investigate) | 80%+ |
| VBet spread coverage | 52% | 70%+ |
| VBet total coverage | 43% | 60%+ |
| Deferred events recovered per day | 0 (feature doesn't exist) | Track baseline |
| Total value opportunities per scan | Current baseline | 10-20% increase |

Note: 888sport targets removed (confirmed platform limitation). Betinia football spreads excluded (confirmed platform limitation).

---

## Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Interwetten 0% is a platform limitation, not a bug | No gain from debugging | Quick investigation — check if detail pages even show spreads manually |
| Deferred table grows large | Slow queries | Auto-expire on start_time + 6hr TTL, indexes on start_time and sport |
| Debug fixes break existing extraction | Regression | Test each provider independently, verify ML extraction unaffected |
| Provider DOM/API changes since enrichment was written | Selectors stale | Part of debugging — update selectors as needed |
