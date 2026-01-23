# Provider Research Summary

This document tracks research on Swedish betting providers and their platform technologies.

## Provider Status Overview

Total providers in list: 53
- Added to providers.yaml: 25 (47%)
- Researched but not added: 28 (53%)

## Added Providers (25)

### Kambi Platform (13)
| Provider | Domain | Brand ID | Status |
|----------|--------|----------|--------|
| Unibet | unibet.se | ubse | Active |
| LeoVegas | leovegas.com | leose | Active |
| Expekt | expekt.se | expektse | Active |
| Paf | paf.se | pafse | Active |
| ATG | atg.se | atg | Active |
| BetMGM | betmgm.se | betmgmse | Active |
| Casumo | casumo.com | case | Active |
| SpeedyBet | speedybet.com | speedybetse | Active |
| X3000 | x3000.se | speedyspelse | Active |
| Svenskaspel | spela.svenskaspel.se | svenskaspel | Active |
| Golden Bull | goldenbull.se | goldenbullse | Active |
| 1X2 | 1x2.se | 1x2se | Active |
| Flax Casino | flaxcasino.se | flaxse | Active |

### Spectate Platform (2)
| Provider | Domain | API Base | Status |
|----------|--------|----------|--------|
| Mr Green | mrgreen.se | spectate-web.mrgreen.se | Active |
| 888sport | 888sport.se | spectate-web.888sport.se | Active |

### Gecko Platform (3)
| Provider | Domain | Type | Status |
|----------|--------|------|--------|
| Betsson | betsson.com | gecko_v2 | Active |
| Betsafe | betsafe.com | gecko_v2 | Active |
| NordicBet | nordicbet.com | gecko_v2 | Active |

### SBTech Platform (3)
| Provider | Domain | Type | Status |
|----------|--------|------|--------|
| Bethard | bethard.com | sbtech | Active |
| ComeOn | comeon.com | sbtech | Active |
| Hajper | hajper.com | sbtech | Active |

### Custom Platforms (4)
| Provider | Domain | Type | Status |
|----------|--------|------|--------|
| Snabbare | snabbare.com | snabbare | Active |
| Pinnacle | pinnacle.com | pinnacle | Active (guest API) |
| Coolbet | coolbet.com | coolbet_nodriver | BLOCKED (Incapsula - requires commercial services) |
| Polymarket | polymarket | polymarket | Active (truth source) |

## Researched Providers Requiring New Implementation (31)

### Public API Providers (1)
**Priority: HIGH - Can be added with custom retrievers**

1. **Smarkets** (smarkets.com)
   - Platform: Betting exchange
   - API: Trading API (docs.smarkets.com)
   - Features: Peer-to-peer betting, 2% commission
   - Status: Needs smarkets retriever

### SBTech Platform (3)
**Priority: MEDIUM - Shared platform, one retriever for all**

1. **Bethard** (bethard.com)
   - Platform: SBTech (5-year contract)
   - Nordic focus, Malta-based

2. **ComeOn** (comeon.com)
   - Platform: SBTech
   - European sportsbook

3. **Hajper** (hajper.com)
   - Platform: SBTech
   - Swedish license active

**Implementation:** Create sbtech.py retriever (similar to kambi.py pattern)

### Soft2Bet Platform (2)
**Priority: MEDIUM - Shared platform**

1. **CampoBet** (campobet.se)
   - Platform: Soft2Bet MEGA engine
   - Operator: Romix Limited
   - Launched: 2020 in Sweden

2. **QuickCasino** (quickcasino.se)
   - Platform: Soft2Bet MEGA gamification
   - Features: 4,500+ games
   - Awards: EGR "Rising Star" 2025

**Implementation:** Create soft2bet.py retriever

### PlayTech Platform (1)
**Priority: LOW - Single provider**

1. **10bet** (10bet.se)
   - Platform: PlayTech (formerly SBTech)
   - License: Malta Gaming Authority

### Altenar Platform (2)
**Priority: LOW**

1. **LuckyCasino** (luckycasino.com)
   - Platform: Altenar
   - Operator: Glitnor Services
   - Top 5 Swedish brand

2. **HappyCasino** (happycasino.se)
   - Platform: Altenar
   - Operator: Glitnor Group
   - License valid until June 2027

### Proprietary Platforms (12)
**Priority: LOW - Custom implementation required for each**

1. **Bet365** (bet365.com)
   - Platform: Proprietary (Erlang-based)
   - Major international bookmaker
   - No public API

2. **Tipwin** (tipwin.se)
   - Platform: Proprietary + GiG partnership
   - Built from scratch

3. **Videoslots** (sports.videoslots.com)
   - Platform: Proprietary
   - Parent: Videoslots Malta

4. **Interwetten** (interwetten.se)
   - Platform: Proprietary
   - Founded: 1990
   - Historical Kambi connection (pool betting only, 2011-2013)

5. **Fastbet** (fastbet.com)
   - Platform: Custom
   - Focus: Pakistan market

6. **FrankFred** (frankfred.com)
   - Platform: Proprietary
   - Operator: UnionJack Limited
   - Relaunched: Nov 2024

7. **PokerStars** (pokerstars.se)
   - Platform: Flutter Entertainment proprietary
   - Part of Flutter group (FanDuel, Betfair, etc.)

8. **VBET** (vbet.se)
   - Platform: BetConstruct
   - Armenian platform, 400+ payment methods

9. **ProntoSport** (prontosport.se)
   - Platform: PremierGaming proprietary
   - License: Spelinspektionen 24Si351
   - Events: 170,000+/month
   - Launched: Dec 2023

10. **DBET** (dbet.com)
    - Platform: DBET Ltd proprietary
    - License: Spelinspektionen 24Si2403
    - Malta-based operator

11. **RaceCasino** (racecasino.com)
    - Platform: L&L Europe proprietary
    - Pay N Play: Trustly
    - Launched: 2020

12. **Spelklubben** (spelklubben.se)
    - Platform: Betsson proprietary (NOT Kambi)
    - Operator: Betsson Group subsidiary
    - Launched: Summer 2024
    - Note: Uses Betsson sportsbook, not Kambi odds

### Multi-Provider Aggregators (5)
**Priority: VERY LOW - No unified sportsbook API**

1. **NoBonusCasino** (nobonuscasino.com)
   - Aggregates: Microgaming, NetEnt, Playtech, Evolution, 1x2gaming, Pragmatic
   - Launched: 2013

2. **LylloCasino** (lyllocasino.com)
   - Operator: MOA Gaming Sweden
   - Providers: NetEnt, Red Tiger, Play'n Go, Playtech, Evolution
   - 1,300+ games

3. **YakoCasino** (yakocasino.com)
   - Platform: CasinoEngine aggregator
   - Operator: L&L Europe
   - 4,000+ games

4. **YetiCasino** (yeticasino.com)
   - HTML5 multi-provider
   - 3,000+ games, 100+ live casino

5. **Casinostugan** (casinostugan.com)
   - Platform: ComeOn Group proprietary
   - Live Casino: Evolution Gaming Ruby Lounge

### Additional Videoslots Group (2)
**Priority: LOW - Proprietary Videoslots platform**

1. **Kungaslottet** (kungaslottet.se)
   - Platform: Videoslots Group proprietary
   - 4,500+ games
   - Launched: March 2024

2. **MegaRiches** (megariches.com)
   - Platform: Videoslots Limited
   - 9,000+ games
   - Sponsor: West Bromwich Albion

### Unknown/Unverified (2)
**Priority: NONE - Needs verification**

1. **Betinia** (betinia.se)
   - Status: Could not verify platform
   - Recommendation: Manual investigation

2. **Betsbk** (betsbk.com)
   - Status: No information found
   - Recommendation: Verify Spelinspektionen license

## Implementation Priority Recommendations

### Immediate (Ready to Add)
- None currently - all easy Kambi/Spectate/Gecko providers added

### High Priority (Public APIs)
1. Create Coolbet retriever
2. Create Smarkets retriever
3. Create Pinnacle retriever (if API access obtained)

### Medium Priority (Shared Platforms)
1. Create SBTech retriever (enables 3 providers: Bethard, ComeOn, Hajper)
2. Create Soft2Bet retriever (enables 2 providers: CampoBet, QuickCasino)

### Low Priority (Custom Implementation)
- Bet365, PokerStars, VBET (require complex custom integrations)
- Single-provider platforms (10bet/PlayTech, LuckyCasino/Altenar)

### Not Recommended
- Multi-provider aggregators (no unified sportsbook API)
- Unverified providers (Betinia, Betsbk)

## Notes

- Kambi providers are easiest to add (same API, different brand parameter)
- Spectate providers use consistent API across Mr Green and 888sport
- Gecko providers require browser automation but gecko_v2 handles it
- SBTech could unlock 3 providers with one retriever implementation
- Focus on providers with proper Swedish licenses from Spelinspektionen
