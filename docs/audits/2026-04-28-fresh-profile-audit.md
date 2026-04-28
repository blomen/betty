# Fresh-Profile Audit — 2026-04-28

**Profile id:** 2 (`Audit`)
**API:** http://localhost:8000
**Providers in `/api/bankroll`:** 38
**Yaml bonus blocks:** 30

## Provider coverage

- [ok] all 33 active providers present with balance=0

## Bonus coverage

- [ok] `10bet`: deposit 1000 SEK (bonusdeposit)
- [ok] `1x2`: deposit 500 SEK (bonusdeposit)
- [ok] `888sport`: deposit 500 SEK (bonusdeposit)
- [ok] `bethard`: deposit 500 SEK (bonusdeposit)
- [ok] `betinia`: deposit 1000 SEK (bonusdeposit)
- [ok] `betmgm`: deposit 500 SEK (bonusdeposit)
- [ok] `betsafe`: deposit 100 SEK (freebet)
- [ok] `betsson`: deposit 250 SEK (freebet)
- [ok] `campobet`: deposit 500 SEK (bonusdeposit)
- [ok] `comeon`: deposit 500 SEK (bonusdeposit)
- [ok] `coolbet`: deposit 1000 SEK (bonusdeposit)
- [ok] `dbet`: deposit 500 SEK (freebet)
- [ok] `expekt`: deposit 1000 SEK (bonusdeposit)
- [ok] `goldenbull`: deposit 500 SEK (bonusdeposit)
- [ok] `hajper`: deposit 500 SEK (freebet)
- [ok] `interwetten`: deposit 1000 SEK (bonusdeposit)
- [ok] `leovegas`: deposit 600 SEK (bonusdeposit)
- [ok] `lodur`: deposit 500 SEK (bonusdeposit)
- [ok] `lyllo`: deposit 100 SEK (freebet)
- [ok] `mrgreen`: deposit 500 SEK (freebet)
- [ok] `nordicbet`: deposit 100 SEK (freebet)
- [ok] `quickcasino`: deposit 500 SEK (bonusdeposit)
- [ok] `snabbare`: deposit 600 SEK (bonusdeposit)
- [ok] `speedybet`: deposit 500 SEK (bonusdeposit)
- [ok] `spelklubben`: deposit 500 SEK (bonusdeposit)
- [ok] `swiper`: deposit 1000 SEK (bonusdeposit)
- [ok] `tipwin`: deposit 1000 SEK (bonusdeposit)
- [ok] `unibet`: deposit 1000 SEK (freebet)
- [ok] `vbet`: deposit 800 SEK (bonusdeposit)
- [ok] `x3000`: deposit 500 SEK (bonusdeposit)

## Arb-page sanity

- [ok] `marathon` correctly excluded from arb page (signal-only)
- [ok] `smarkets` correctly excluded from arb page (signal-only)
- [ok] all 33 providers reachable through cluster map (or signal-only)

## Deposit recommendation

**Live solve:** smallest bankroll funding 100% of 211 current bets:

- **Total: 1,000 SEK**
- Per-unlimited-provider split (weighted by bet stakes): pinnacle=47%, polymarket=43%, cloudbet=7%, kalshi=3%
- Bets fundable: 211/211
- Total expected EV: 253.81 SEK

**Target-bankroll table:**

| Bankroll | Bets fundable | % of feed | Total EV | Per-unlimited split |
|---|---|---|---|---|
| 10,000 SEK | 198/211 | 94% | 2304.06 SEK | pinnacle=45%, polymarket=44%, cloudbet=8%, kalshi=2% |
| 25,000 SEK | 207/211 | 98% | 5775.17 SEK | pinnacle=46%, polymarket=44%, cloudbet=8%, kalshi=2% |
| 50,000 SEK | 211/211 | 100% | 11554.39 SEK | pinnacle=46%, polymarket=44%, cloudbet=8%, kalshi=2% |
| 100,000 SEK | 211/211 | 100% | 23092.22 SEK | pinnacle=46%, polymarket=44%, cloudbet=8%, kalshi=2% |

## Verdict: PASS
