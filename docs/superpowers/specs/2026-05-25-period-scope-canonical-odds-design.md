# Period-Scope Dimension for Canonical Odds — Design

**Date:** 2026-05-25
**Status:** Draft, awaiting user review
**Trigger:** Manual audit caught the scanner surfacing a non-arb on Slovenia v Italy (IIHF Worlds, 2026-05-25): Lodur Under 4.5 @ 2.35 paired against Pinnacle Over 4.5 @ 1.85 with +3.66% "guaranteed" profit. Audit revealed the two legs price different scopes — Lodur is full match incl. OT+SO, Pinnacle is regulation only. Both legs can lose if regulation ends at 4 total goals and an OT goal pushes the final to 5.

## Problem

The canonical `odds` schema identifies a market by `(event_id, provider_id, market, outcome, point)`. It does **not** capture the **temporal/structural scope** of the market (regulation-only vs full match vs first half vs single set, etc.). Provider-native scope identifiers do carry this — Pinnacle's `period`, Altenar's `typeId`, Gecko V2's `market_template` — but the canonicalization layer strips them.

Result: the opportunity scanner joins different-scope odds rows as if they were the same market. This silently produces:
- "Arbs" that can lose both legs (the Slovenia v Italy case)
- Value bets priced against a sharp baseline that doesn't actually cover the same outcomes
- Phantom edges driven by the OT-inclusion probability bump (~10 pp for ice hockey totals)

The user's review constraint is "I want to trust we coded everything right" — so the fix has to make the mismatched comparison structurally impossible, not patch the one case.

## Audit baseline

**Distribution of the bug (live DB at design time):**

| Provider | period | Row count |
|---|---|---:|
| Pinnacle | 0 (incl. OT) | 395,414 |
| Pinnacle | 6 (regulation only) | 5,656 |

**All Pinnacle `period=6` rows are ice_hockey, across 58 leagues** — NHL (657 rows, 1x2 only), IIHF Worlds (294 spread + 240 total + 102 1x2), KHL, SHL, AHL, every European elite league. Despite the code comment in [providers/pinnacle.py](backend/src/providers/pinnacle.py) claiming period=6 spread/total is "minor leagues only," it's actually the default for any league that doesn't expose period=0 totals/spreads on Pinnacle — which is most of them outside North America.

**Soft side (already filtered):** Altenar ([backend/src/providers/altenar.py:313-316](backend/src/providers/altenar.py#L313-L316)) explicitly skips `typeId=18` (regulation-only) for ice hockey, preferring `typeId=412` (incl. OT+SO). Gecko V2 prefers `TGOUOT` over `TGOU`. So every soft-book hockey total/spread in the DB is incl-OT. **The mismatch is one-sided and predictable: Pinnacle ships regulation, softs ship incl-OT.**

## Approach (selected)

Add a normalized `scope` enum to the `odds` table. Each extractor populates it from its native scope identifier. The scanner joins on `(event_id, market, point, scope)` with exact-match required. No silent fallback to mismatched scope.

This makes wrong code refuse to run: a scanner that forgets to join on `scope` won't return mismatched pairs because the new `ix_odds_event_market_point_scope` index drives the join, and SQL written against the new schema can't accidentally combine OT-inclusive and regulation rows.

## Schema change

```sql
ALTER TABLE odds
  ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'ft';
```

**Canonical scope vocabulary:**

| Value | Meaning | Sports that use it |
|---|---|---|
| `ft` | Full time — match outcome including any OT/SO/extra innings | hockey, basketball, AF, baseball, MMA, football, tennis, esports |
| `reg` | Regulation time only — no OT/SO/extra innings | hockey, basketball, AF, baseball |
| `1h` / `2h` | First / second half | football, basketball, AF |
| `q1`..`q4` | Quarter | basketball, AF |
| `p1`..`p3` | Period | hockey |
| `set_1`..`set_5` | Set | tennis, volleyball |
| `map_1`..`map_5` | Map | esports |

`ft` is the canonical default per sport — it's whatever "the match" naturally means for that sport, matching how sportsbooks settle "Full Time" bets:
- **Ice hockey** — including OT + shootout (Altenar/Gecko already default here; Pinnacle period 0)
- **Football (soccer)** — 90 minutes + stoppage time only; **extra time and penalty shootouts NOT included** (standard FIFA bet-settlement convention)
- **Basketball / AF** — including OT
- **Baseball** — including extra innings
- **Tennis** — final match winner (sets-as-handicap is a separate market dimension, see Out of scope)

Note: for hockey, the `1x2` vs `moneyline` distinction is already encoded in the `market` field (Pinnacle period 0 hockey emits `moneyline` 2-way, period 6 emits `1x2` 3-way with draw). The scope dimension primarily disambiguates `total` and `spread`, where the same `market` name carries different scope semantics.

A sport's canonical scope is hard-coded in `backend/src/constants.py`:

```python
SPORT_CANONICAL_SCOPE: dict[str, str] = {
    "football": "ft",          # 90 min + ET
    "ice_hockey": "ft",        # incl. OT + SO
    "basketball": "ft",        # incl. OT
    "american_football": "ft", # incl. OT
    "baseball": "ft",          # incl. extra innings
    "tennis": "ft",            # match winner
    "volleyball": "ft",
    "handball": "ft",
    "mma": "ft",
    "esports": "ft",           # series outcome by default; map markets are explicit
}
```

The scanner only surfaces opportunities at the canonical scope for that sport. Other scopes (`reg`, `1h`, `set_1`, etc.) can be stored and queried, but won't appear in arb/value scanning unless explicitly opted-in per sport.

## Extractor changes

Each provider extractor populates `scope` from its native scope identifier. Providers that already ship only the canonical scope can rely on the column default; the explicit mappings below document what they ship today so the next regression is visible in code review.

| Provider | Source field | Mapping (hockey shown; other sports analogous) |
|---|---|---|
| **Pinnacle** | `market.period` | `0` → `ft`; `6` → `reg`; for soccer halves: `1` → `1h`, `2` → `2h` |
| **Altenar** | `typeId` | `412` → `ft`; `18` → `reg` (hockey); `225` → `ft`, `18` → `reg` (basket/AF); `258` → `ft` (baseball incl. extras) |
| **Gecko V2** | `market_template` | `TGOUOT` → `ft`; `TGOU` → `reg`; `MHCPNOT` → `reg` (hockey spread) |
| **Kambi** | `betOfferType` + criterion label | enumerate during implementation; flagged as TODO in plan |
| **Spectate / ComeOn / Hajper / Rainbet / 888sport** | varies | enumerate during implementation; most ship only `ft` for the markets we care about |
| **Polymarket / Kalshi / Smarkets / Cloudbet / Marathon / Stake** | n/a | all moneyline-only at the markets we ingest; `ft` |

**Rule for new providers:** if the provider has any market with non-canonical scope and the extractor doesn't set `scope` explicitly, the row defaults to `ft` and silently lies. This is detectable in CI by a unit test that asserts each extractor either explicitly sets `scope` or has an `@scope_audited` marker confirming it only emits canonical-scope rows.

## Scanner change

The arb and value detection paths in [backend/src/analysis/scanner.py](backend/src/analysis/scanner.py) and [backend/src/analysis/value.py](backend/src/analysis/value.py) currently group odds by `(event_id, market, point)`. They must add `scope` to that grouping. Concretely:

- `_resolve_event_id` and the odds-loading queries pull `o.scope` and pivot on it
- `find_value` / arb construction iterates `(event_id, market, point, scope)` tuples
- A new filter step applied **per sport** restricts opportunity surfacing to `scope = SPORT_CANONICAL_SCOPE[sport]`
- Mixed-scope events are silently dropped from opportunity emission (no fallback). Logged at DEBUG with a count, surfaced in `/health/extraction` as an "unscannable_markets" warning if it exceeds a threshold

The scanner does NOT attempt cross-scope hedging (e.g., reg-vs-reg arb when both books happen to have regulation odds). That's a future expansion — at design time no soft provider in our extractor set ships regulation-only hockey, so reg-vs-reg arbs are structurally unavailable anyway.

## Migration

Single migration block added to `models._run_pg_migrations` (no Alembic in this repo):

1. `ALTER TABLE odds ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'ft';`
2. Backfill in a single UPDATE per provider:
   - Pinnacle hockey period=6 → `scope = 'reg'`:
     ```sql
     UPDATE odds SET scope = 'reg'
     WHERE provider_id = 'pinnacle'
       AND provider_meta->>'period' = '6'
       AND event_id IN (SELECT id FROM events WHERE sport = 'ice_hockey');
     ```
   - All other existing rows keep the default `'ft'`
3. `DROP INDEX ix_odds_composite_key;`
4. `CREATE UNIQUE INDEX uq_odds_with_point_scope ON odds (event_id, provider_id, market, outcome, point, scope) NULLS NOT DISTINCT;`
5. `CREATE INDEX ix_odds_event_market_point_scope ON odds (event_id, market, point, scope);`
6. `DROP INDEX uq_odds_with_point_nd;` (replaced by `uq_odds_with_point_scope`)

The migration is idempotent (guards on `IF NOT EXISTS` for the column add, conditional CREATE/DROP for indexes).

## Quality gates

New extraction-health metric in `/health/extraction`:

- **`unscannable_markets`**: count of `(event_id, market, point)` triples where Pinnacle has only non-canonical scope and no soft book provides the canonical scope. These are markets where we have a sharp baseline but at the wrong scope to use. Surfaces as WARNING when count > 10 for an extraction cycle (typical baseline today: hockey IIHF/KHL/SHL ≈ 5-15 events).

This metric tells us how much sharp data we're "losing" to the new strictness. If it grows, that's a signal to investigate whether a soft provider quietly added regulation-only odds (e.g., a new Altenar typeId), or whether we should consider broadcasting these markets as "regulation arb only" once a second regulation-side data source exists.

## Test plan

1. **Unit — extractor scope mapping:**
   - Feed each provider extractor a synthetic raw payload covering each scope it can emit
   - Assert the emitted `StandardEvent.odds[*].scope` matches expectation
   - Specifically: Pinnacle period=0 hockey → `ft`, period=6 hockey → `reg`; Altenar typeId=412 → `ft`, typeId=18 → `reg`

2. **Unit — scanner scope enforcement:**
   - Build two synthetic `Odds` rows for the same event/market/point/outcome at different `scope` values
   - Assert `find_value` and arb construction return zero opportunities

3. **Integration — IIHF Worlds reproduction:**
   - Seed DB with the exact Slovenia v Italy 2026-05-25 fixture (Pinnacle Over 4.5 reg, Lodur Under 4.5 ft)
   - Run scanner
   - Assert zero opportunities surfaced for that event/market

4. **Migration test:**
   - Snapshot DB pre-migration (or use a fixture DB)
   - Run migration
   - Assert all Pinnacle period=6 hockey rows have `scope='reg'`
   - Assert all other rows have `scope='ft'`
   - Assert no duplicate-key violations from the new unique index

5. **Smoke — post-deploy:**
   - Query `opportunities` 60s after deploy
   - Assert no ice_hockey total/spread opportunities exist where the Pinnacle leg has `scope='reg'`

## Rollout

1. Deploy migration + extractor changes + scanner change as a single backend rebuild
2. Migration runs at startup before extraction resumes
3. Post-deploy verification:
   - `SELECT scope, COUNT(*) FROM odds GROUP BY scope` — expect `ft` ≈ 395k, `reg` ≈ 5,656
   - Query `opportunities` for IIHF Worlds hockey — expect empty
   - Tail `/health/extraction` for `unscannable_markets` count
4. If smoke passes, keep deployed. If migration fails or smoke shows new false-positives, roll back schema with `ALTER TABLE odds DROP COLUMN scope;` and revert the deploy.

The rollout uses the standard `server-deploy.sh rebuild backend` flow. No extra coordination beyond the existing multi-agent lock.

## Out of scope

- **Cross-scope hedging:** allowing reg-vs-reg arbs when both sides happen to have regulation odds. Reserved for a future spec once we have a regulation-side soft provider.
- **First-class period/quarter/half markets in the scanner:** schema supports them (`1h`, `q1`, etc.) but extending the scanner to surface them as their own opportunity stream is a separate design.
- **Tennis sets:** scope semantics for tennis (`set_1` vs match) interact with the existing SET_SPREAD_SPORTS handicap-conversion code in a way that needs its own design pass.
- **Historical bet review:** identifying which already-placed bets in the DB were based on scope-mismatched data. This is a one-time analytical query, not part of the scanner change.
- **Generalizing this beyond `odds`:** the same scope dimension may eventually belong on `events` (live state) or `opportunities` (audit trail). Out of scope for this design — `odds` is the join axis the scanner uses, so `odds.scope` is the load-bearing change.

## Files affected (estimated)

- [backend/src/db/models.py](backend/src/db/models.py) — `Odds` model + migration block
- [backend/src/constants.py](backend/src/constants.py) — `SPORT_CANONICAL_SCOPE`
- [backend/src/providers/pinnacle.py](backend/src/providers/pinnacle.py) — set `scope` on emitted odds
- [backend/src/providers/altenar.py](backend/src/providers/altenar.py) — set `scope`; reconsider the `typeId=18` skip (now we can store both)
- [backend/src/providers/gecko_v2.py](backend/src/providers/gecko_v2.py) — set `scope`
- [backend/src/providers/kambi.py](backend/src/providers/kambi.py) — set `scope`
- Other provider extractors that ship totals/spreads — explicit `scope='ft'` or audit marker
- [backend/src/pipeline/storage.py](backend/src/pipeline/storage.py) — `OddsBatchProcessor` carries `scope` end-to-end
- [backend/src/analysis/scanner.py](backend/src/analysis/scanner.py) — add `scope` to grouping
- [backend/src/analysis/value.py](backend/src/analysis/value.py) — add `scope` to grouping
- [backend/src/api/routes/extraction.py](backend/src/api/routes/extraction.py) — `/health/extraction` adds `unscannable_markets` metric
- New tests under `backend/tests/`
