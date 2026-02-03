# Provider Pipeline Validation Workflow

This document describes the formal workflow for validating the provider pipeline and adding new providers.

## Overview

The OddOpp pipeline extracts 1x2/moneyline odds from multiple providers and compares them against sharp sources (Pinnacle) to find value bets and arbitrage opportunities.

**Main pipeline providers:**
- **Pinnacle** - Sharp source (fair odds baseline)
- **Polymarket** - Prediction market data
- **LeoVegas** - Kambi-powered soft bookmaker

---

## Phase 1: Main Pipeline Validation

Use this phase to establish or verify the baseline metrics for existing providers.

### 1.1 Clear Database and Run Baseline Extraction

```bash
cd backend
rm -f data/oddopp.db
python -m src.app extract pinnacle polymarket leovegas
```

### 1.2 Run Validation Script

```bash
python scripts/validate_pipeline.py
```

Expected output:
```
=== Pipeline Validation ===
Provider    | Odds | Events | Ratio | Norm% | Status
------------|------|--------|-------|-------|-------
pinnacle    |  842 |    312 |  2.70 |  100% | PASS
polymarket  |  156 |     65 |  2.40 |   98% | PASS
leovegas    | 1205 |    402 |  3.00 |  100% | PASS

Cross-provider matching: 89/402 events (22.1%)
Score-like outcomes: 0 (PASS)
```

### 1.3 Run Opportunity Detection

```bash
python -m src.app arbs   # Check for arbitrage opportunities
python -m src.app value  # Check for value bets
```

### 1.4 Baseline Metrics

| Provider      | Odds/Event Ratio | Outcome Norm % | Expected Events |
|---------------|------------------|----------------|-----------------|
| Pinnacle.Com  | 2.4 - 2.8        | 100%           | 500-1500        |
| Polymarket    | 2.0 - 2.6        | ≥90%           | 100-300         |
| Leovegas.Com  | 2.8 - 3.2        | 100%           | 500-1500        |

**Note:** Provider names in the database include domain suffixes (e.g., "Pinnacle.Com" not "pinnacle").

---

## Phase 2: New Provider Development (Isolated)

Use this phase when developing a new provider extractor.

### 2.1 Create Provider Extractor

1. Check provider type in `config/providers.yaml` (Kambi, Gecko V2, Spectate, SBTech, Altenar, etc.)
2. If existing type: add config entry only
3. If new type: create extractor in `providers/`, register in `factory.py`
4. For WebSocket/RSocket providers: use `RSocketMixin` from `providers/mixins/`

### 2.2 Test Provider in Isolation

```bash
# Extract only the new provider
python -m src.app extract <new_provider>

# Run validation
python scripts/validate_pipeline.py --provider <new_provider>
```

### 2.3 Validation Checklist

- [ ] Odds/event ratio in expected range (2.4-3.1)
- [ ] Outcome normalization 100% (home/away/draw only)
- [ ] No score-like outcomes (X-X patterns)
- [ ] Event count matches visual audit of provider site
- [ ] No API errors or rate limit hits

---

## Phase 3: Integration to Main Pipeline

Use this phase when integrating a validated new provider into the main pipeline.

### 3.1 Clear Database and Run Full Extraction

```bash
rm -f data/oddopp.db
python -m src.app extract pinnacle polymarket leovegas <new_provider>
```

### 3.2 Run Full Validation

```bash
python scripts/validate_pipeline.py
```

### 3.3 Integration Checklist

- [ ] All baseline providers still have same metrics (no regression)
- [ ] New provider has expected metrics
- [ ] Cross-provider matching improved (more events matched)
- [ ] No suspicious arbitrage (>10% profit)
- [ ] Opportunity detection runs without errors

### 3.4 Spot-Check Matched Events

Run this SQL to verify matched events have correct structure:

```sql
SELECT e.id, e.home_team, e.away_team, p.name, o.outcome, o.odds
FROM events e
JOIN odds o ON e.id = o.event_id
JOIN providers p ON o.provider_id = p.id
WHERE e.id IN (
    SELECT event_id FROM odds
    WHERE provider_id = (SELECT id FROM providers WHERE name = '<new_provider>')
)
ORDER BY e.id, p.name, o.outcome
LIMIT 30;
```

---

## Phase 4: Documentation & Commit

### 4.1 Update Baseline Metrics

If adding a new provider, update `scripts/baseline_metrics.json`:

```json
{
  "providers": {
    "NewProvider.Com": {
      "min_ratio": 2.4,
      "max_ratio": 3.2,
      "min_norm": 100,
      "description": "New provider description"
    }
  }
}
```

Or use the capture feature to auto-generate thresholds:
```bash
python scripts/validate_pipeline.py --capture
```

### 4.2 Add Provider to Production List

Update this document's "Main pipeline providers" section.

---

## Validation Metrics Reference

### Odds/Event Ratio

Expected range: **2.4 - 3.1** for 1x2 markets

- **< 2.0**: Missing outcomes (check market parsing)
- **2.4 - 3.1**: Normal for 1x2 markets (2 outcomes for moneyline, 3 for 1x2)
- **> 4.0**: Non-1x2 markets leaking through (check `betOfferType.id` filter)

### Outcome Normalization

Expected: **≥97%** for all providers

All outcomes should be normalized to `home`, `away`, or `draw`. Lower percentages indicate:
- Team name matching failing
- Player names not normalized (common in tennis/esports)

### Score-Like Outcomes

Expected: **0** for all providers

Any outcomes matching `X-X` pattern (e.g., "1-0", "2-1") indicate correct score markets leaking through.

### Cross-Provider Matching

Expected: **≥15%** of events matched across 2+ providers

Lower matching rates indicate:
- Team name normalization issues
- Sport/league mapping mismatches
- Timezone issues affecting event matching

---

## Troubleshooting

### High Ratio (>4.0)

Non-1x2 markets are being extracted. Check:
1. Market type filter in extractor (e.g., Kambi `betOfferType.id == 2`)
2. `ALLOWED_MARKETS` constant in `constants.py`

### Low Normalization (<95%)

Team names not being normalized. Check:
1. `normalize_outcome()` function in the provider
2. Outcome format returned by API

### Score-Like Outcomes

Correct score markets leaking through. Check:
1. Market type filter (should only allow 1x2/moneyline)
2. API response structure for market identification

### Low Cross-Provider Matching

Events not matching across providers. Check:
1. Team name normalization (`normalize_team_name()`)
2. Event start time timezone handling
3. Sport/league category mappings
