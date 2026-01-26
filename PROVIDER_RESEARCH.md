# Provider Research & Implementation Notes

## Fastbet.com - SBTech (Implemented)

### Status: Code Ready, Requires Authentication

**Platform:** SBTech
**Parent Company:** Bethard Group Limited (same as Bethard)
**License:** Sweden (valid until 2027-09-12)
**Type:** Pay N Play (BankID required)

### Research Sources
- [Bethard/Fastbet Relationship](https://igamingbusiness.com/tech-innovation/bethard-relaunches-fastbet-as-part-of-b2b-growth-plans/)
- [SBTech Platform](https://casinobeats.com/2023/05/25/thunderkick-bethard-fastbet-sweden/)
- [Swedish License](https://www.sbo.net/country/sweden/)

### Implementation
- **File:** `backend/src/providers/fastbet.py`
- **Base Class:** `SBTechRetriever` (shared with Bethard)
- **Configuration:** `backend/src/config/providers.yaml`
- **Factory:** Registered in `backend/src/factory.py`

### Challenge
Fastbet uses Pay N Play authentication model requiring Swedish BankID. This means:
- No public sportsbook page without authentication
- Requires valid Swedish banking credentials
- Cannot be tested without actual Swedish BankID

### Code Status
✓ Retriever implemented correctly
✓ Configuration added
✓ Factory registration complete
? Testing blocked by authentication requirement
- Not added to active providers (requires BankID for actual use)

---

## 10bet.se - PlayTech (Not Implemented)

### Status: Incorrect Platform Assumption

**Platform:** PlayTech (NOT SBTech as initially researched)
**Research Correction:** [PlayTech Launch](https://www.gamblinginsider.com/news/24071/playtech-launches-sportsbook-with-10bet-in-sweden/)

Initial research indicated SBTech, but 10bet actually uses PlayTech platform. We don't have PlayTech support implemented.

**Decision:** Abandoned implementation

---

## Happy Casino - Kambi (In Transition)

### Status: Platform Migration In Progress

**Platform:** Transitioning to Kambi (from Altenar)
**Parent:** Glitnor Group
**Announcement:** October 2025
**Rollout:** 2025-2026

### Research Sources
- [Glitnor/Kambi Partnership](https://igamingbusiness.com/sports-betting/online-sports-betting/glitnor-partners-kambi-sportsbook-rollout/)
- [Kambi Press Release](https://www.kambi.com/news-insights/kambi-turnkey-sportsbook-partnership-glitnor-group/)

### Decision
Wait for Kambi migration to complete. Currently still on Altenar platform.

**When Ready:**
- Will be simple Kambi configuration (like other Kambi providers)
- Just need to find Kambi brand ID
- Estimated implementation: 1-2 hours

---

## Unimplemented Provider Analysis

### By Platform Type

**Kambi (13 already implemented)**
- unibet, leovegas, expekt, casumo, svenskaspel, paf, atg, betmgm, speedybet, x3000, goldenbull, 1x2, flaxcasino

**SBTech**
- bethard ✓ (implemented)
- fastbet ✓ (implemented, requires BankID)

**ComeOn Group (WebSocket/RSocket)**
- comeon ✓ (implemented)
- hajper ✓ (implemented)

**Other Platforms**
- Spectate: mrgreen ✓, snabbare ✓
- Gecko: betsson, betsafe, nordicbet ✓
- Pinnacle API: pinnacle ✓
- PlayTech: 10bet (not implemented)
- Altenar: betinia, frankfred (not implemented)
- GiG: tipwin (not implemented)
- BetConstruct: vbet (not implemented)

### Remaining from providers.json (26 unimplemented)

Most require new platform implementations:

1. **bet365** - Proprietary platform (very difficult)
2. **betinia** - Altenar (8-12 hours, new platform)
3. **frankfred** - Altenar (4-6 hours once Altenar done)
4. **interwetten** - Mixed (Kambi pools + other)
5. **tipwin** - GiG platform (16-24 hours)
6. **vbet** - BetConstruct (16-24 hours)
7. **10bet** - PlayTech (requires new platform)
8-26. Various casino-focused or small operators

---

## Recommendations

### Quick Wins (When Available)
1. **Happy Casino** - When Kambi migration completes (1-2 hours)
2. **Other Glitnor brands** - Lucky Casino, Flax Casino if they migrate to Kambi

### Medium Effort
3. **Altenar Platform** - Would unlock betinia + frankfred (8-12 hours first, 4-6 second)

### High Effort
4. **GiG/BetConstruct** - Would unlock tipwin, vbet (16-24 hours each)

### Not Recommended
- **bet365** - Proprietary, heavily protected, very difficult
- **10bet** - PlayTech platform (major new implementation)
- **Pay N Play only sites** - Require Swedish BankID authentication

---

## Summary

**Current Coverage:** 25 providers implemented
- 13 Kambi
- 2 SBTech
- 2 ComeOn Group
- 3 Gecko
- 2 Spectate
- 1 Pinnacle
- 2 Custom (Polymarket + others)

**Best Path Forward:**
1. Wait for Happy Casino Kambi migration
2. Consider Altenar platform for betinia/frankfred
3. Focus on improving existing providers (market classification, coverage)

The Swedish market is well-covered with current implementations. Most remaining providers offer limited additional value or require significant platform development.
