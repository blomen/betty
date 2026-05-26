# Pinnacle Period Codes (verified 2026-05-26)

Discovered by `backend/scripts/discover_pinnacle_periods.py` against the
unauthenticated guest API. Numbers are the `period` field on each market
object in the `/leagues/{league_id}/markets/straight` response.

## Verified mapping (live data)

| Sport      | Pinnacle ID | Periods seen | Market types per period |
|------------|-------------|--------------|--------------------------|
| MLB        | 3           | 0, 1, 3      | full game; F5; F3 (moneyline / spread / total) |
| NBA / basketball | 4    | 0, 1, 4, 5   | full game; first half; Q3?; Q4? |
| Soccer (EPL/world) | 29 | 0, 1         | full game; first half |
| NFL        | 15          | needs proxy  | (401 from datacenter IP — re-run via PROXY_URL) |
| NHL        | 19          | needs proxy  | (401 from datacenter IP — re-run via PROXY_URL) |

To get NFL / NHL period mappings:

```powershell
$env:PROXY_URL = "<the gost socks5 url from .env.docker>"
python backend/scripts/discover_pinnacle_periods.py
```

## Conventional Pinnacle period codes (industry reference)

These are the published Pinnacle period codes (cross-checked against the
discovered MLB/NBA/soccer set):

| Sport        | period 0 | 1   | 2   | 3   | 4   | 5   | 6   | 7   |
|--------------|----------|-----|-----|-----|-----|-----|-----|-----|
| Baseball     | full     | F5  | —   | F3  | —   | F7? | —   | F1? |
| Basketball   | full     | 1H  | 2H  | Q1  | Q2  | Q3  | Q4  | —   |
| Football (NFL) | full   | 1H  | 2H  | Q1  | Q2  | Q3  | Q4  | —   |
| Hockey       | OT-incl  | P1  | P2  | P3  | —   | —   | regulation | — |
| Soccer       | full     | 1H  | 2H  | —   | —   | —   | —   | —   |

(F5 = first 5 innings; F3 = first 3; 1H = first half; Q1 = first
quarter; P1 = first period; reg = regulation only, no OT/SO.)

The `_parse_markets` method in `backend/src/providers/pinnacle.py`
currently only handles `period == 0` (`scope="ft"`), `period == 6` for
ice hockey (`scope="reg"`), and `period in 1..5` for esports
(`scope="map_N"`). All other period values are **silently dropped**.

## Canonical scopes already declared

[`backend/src/constants.py:VALID_SCOPES`](../../backend/src/constants.py)
already accepts: `ft, reg, 1h, 2h, q1-q4, p1-p3, set_1-set_5, map_1-map_5`.

Scopes that would need to be ADDED to enable baseball period extraction:
- `f5` — first 5 innings
- `f3` — first 3 innings

(`f1`, `f7` are rare — defer until live data confirms they ship.)

## What's blocking actual F5/period scanning

Extracting Pinnacle period odds is the easy half. To surface F5/1H/Q
opportunities to the user, three more changes are required:

1. **Scanner scope-awareness.** [`scanner.group_odds`](../../backend/src/analysis/scanner.py)
   filters to `canonical_scope_for(sport)` and drops everything else.
   It would need to either (a) scan per-scope independently and emit
   one set of opportunities per scope, or (b) accept a `scope` parameter
   passed in by an outer loop that iterates over scopes.

2. **Soft-book period extraction.** Pinnacle alone gives us a sharp
   reference but no soft books to arbitrage against. Kambi (Unibet et
   al), Altenar, Gecko, Spectate would each need their period markets
   wired into their extractors with the same canonical scope tags.

3. **UI surface.** Opportunities at non-`ft` scope need scope labels in
   the value-bet row (e.g. "Total 8.5 over · F5") so the user can
   distinguish a full-game bet from a period bet at placement time.

The bet placement workflow itself already passes `scope` through the
storage layer, so no changes are needed there once the upstream three
exist.

## Recommended next step

If F5 specifically is the priority (Anon's videos emphasise it as a
bullpen-fatigue hedge):

1. Add `f5` and `f3` to `VALID_SCOPES`.
2. Add `_BASEBALL_PERIOD_SCOPE = {1: "f5", 3: "f3"}` in `pinnacle.py`
   and emit those markets in `_parse_markets`.
3. Refactor `scanner.group_odds` to take a `scope` parameter; call it
   once per scope per event from the analyzer.
4. Wire Kambi/Altenar baseball F5 extraction (one provider at a time).
5. Add scope chip to PlayPage value-bet rows.

Steps 1-2 are ~30 lines. Step 3 is ~200 lines and touches a critical
path. Steps 4-5 are per-provider and incremental.
