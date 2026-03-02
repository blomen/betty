# Provider Browser Pathways — Full Automation Discovery

> Discovery document mapping every browser pathway needed for full betting automation.
> Each platform documents: Place Bet, Deposit, Withdraw, Balance, My Bets, Scores/Results, Bonus Status, Login.
> Discovered 2026-03-01 via manual browser inspection.

## Functions to Automate

| Function | Description | Priority |
|----------|-------------|----------|
| **Place Bet** | Navigate to event → select market → enter stake → confirm | P0 |
| **Check Balance** | Read current account balance | P0 |
| **My Bets** | View active/pending bets and settled bet history | P0 |
| **Deposit** | Add funds → select payment method → enter amount → confirm | P1 |
| **Withdraw** | Cash out → select method → enter amount → confirm | P1 |
| **Settle/Results** | Check live scores or match results for bet settlement | P1 |
| **Bonus Status** | Check wagering progress, freebet availability | P2 |
| **Login** | Authenticate (for session bootstrap) | P2 |

---

## Platform Groups

| Platform | Canonical | Other Brands | Sportsbook Type |
|----------|-----------|--------------|-----------------|
| Kambi | unibet.se | leovegas, speedybet, x3000, goldenbull, 1x2 | Embedded JS widget (Kambi client-static) |
| Altenar | betinia.se | campobet, swiper, lodur, dbet, quickcasino | Embedded JS widget (Altenar WSDK) |
| Spectate (888/Evoke) | mrgreen.se | 888sport | Embedded iframe (Spectate) |
| Gecko V2 / OBG | betsson.com | nordicbet, spelklubben, bethard | Native SPA (OBG platform) |
| ComeOn Group | comeon.com | hajper, lyllo, snabbare | RSocket SPA (Sportradar MTS) |
| 10Bet (Playtech) | 10bet.se | — | Embedded JS widget (Mojito SPA) |
| VBet (BetConstruct) | vbet.se | — | Native SPA (Swarm WebSocket) |
| Interwetten | interwetten.se | — | SSR + SignalR live updates |
| Coolbet (GAN) | coolbet.com | — | Native SPA (GAN Sports) |
| Tipwin | tipwin.se | — | SPA with WebSocket offer feed |

---

## 1. Kambi Platform (unibet.se — canonical)

**Brands:** unibet.se, leovegas.com, speedybet.com, x3000.com, goldenbull.se, 1x2.se
**Deep-linking:** YES — event pages via `/betting/sports/event/{kambi_event_id}`
**Verified:** 2026-03-02 via live browser session (Unibet)

### Login (VERIFIED)
```
Flow (Unibet — BankID):
1. Navigate to landing page: /betting/sports/home
2. Cookie banner: OneTrust — click button "Avvisa alla" (decline all)
3. Click button "Spela här" → BankID modal opens
4. Click button "Starta BankID" → QR code displayed
5. User scans QR code on phone → wait up to 120s
6. Post-login: "MINA SPELGRÄNSER" modal appears → click button "okej"
7. Logged in — "saldo" balance visible in header

Login detection indicators:
- Logged OUT: "SPELA HÄR" or "LOGGA IN" in page content
- Logged IN: "saldo" in content, "customerLoggedIn=true", "PLACERA SPEL", "SPELGRÄNSER"

Selectors:
- Cookie: page.get_by_role("button", name="Avvisa alla")
- Login trigger: page.get_by_role("button", name="Spela här")
- BankID start: page.get_by_role("button", name="Starta BankID")
- Post-login dismiss: page.get_by_role("button", name="okej")
```

### Place Bet (VERIFIED)
```
URL: https://{domain}/betting/sports/event/{kambi_event_id}
Example: https://www.unibet.se/betting/sports/event/1024182463

Flow:
1. Navigate to event page via deep link
2. Wait for Kambi widget to render (~3s after domcontentloaded)
3. Click odds button (e.g. "Real Madrid 1.35") → bet slip appears at BOTTOM
4. Bet slip structure:
   - Header: "Singlar" with count (1) and total odds "@ 1.35"
   - Selection: team name + "Cash Out" badge + "Fulltid" (market type)
   - Remove: button "Avlägsna resultat {team}"
   - Odds display: current odds value
   - Stake input: textbox placeholder="0.00 kr"
   - Payout: "Möjlig utbetalning: {amount} kr"
   - Submit: button "Lägg spel" (disabled until stake entered)
5. CRITICAL: Must use pressSequentially (character-by-character typing) for stake input.
   fill() bypasses Kambi's React event handlers, leaving payout at "0.00 kr".
6. If odds change during fill: "Godkänn förändrat odds" button replaces "Lägg spel"
7. User confirms manually — we do NOT click "Lägg spel"

Selectors (verified):
- Odds buttons: [class*="outcome__odds"], [class*="mod-outcome"] button, button[class*="outcome"]
- Stake input: input[placeholder*="0.00"], input[aria-label*="insats" i], [class*="betslip"] input
- Place bet: button "Lägg spel"
- Accept changed odds: button "Godkänn förändrat odds"

API interception:
- Kambi offering API: https://eu1.offering-api.kambicdn.com/offering/v2018/{brand}/...
```

### Check Balance
```
Location: Top right header area (visible when logged in)
Shows "Saldo X kr" after login
```

### My Bets (VERIFIED)
```
URL: /betting/sports/bethistory (confirmed working)
Tabs: Singlar, Kombination, System
Shows settled + open bets with outcomes
```

### Deposit (VERIFIED)
```
URL: /myaccount/cashier → RETURNS 404 (DEAD!)
ACTUAL: Deposit is a modal overlay triggered by clicking the deposit icon button
        in the header bar (not a separate URL page).
Navigate to landing page and user deposits manually via header icon.
```

### Withdraw
```
Same as deposit — modal overlay from header, NOT a URL page
```

### Settle/Results
```
No dedicated results page observed.
Live scores visible on match pages during play.
```

### Bonus Status
```
Under /myaccount/ section (requires login)
```

### Brand URL Patterns
```
unibet:    /betting/sports/event/{id}  | landing: /betting/sports/home     | my bets: /betting/sports/bethistory
leovegas:  /sv-se/betting/event/{id}   | landing: /sv-se/betting           | my bets: /sv-se/betting/bethistory (unverified)
speedybet: /sv/betting/event/{id}      | landing: /sv/betting              | my bets: /sv/betting/bethistory (unverified)
x3000:     /betting/event/{id}         | landing: /betting                 | my bets: /betting/bethistory (unverified)
goldenbull:/en/betting/event/{id}      | landing: /en/betting              | my bets: /en/betting/bethistory (unverified)
1x2:       /en/betting/event/{id}      | landing: /en/betting              | my bets: /en/betting/bethistory (unverified)
```

---

## 2. Altenar Platform (betinia.se — canonical)

**Brands:** betinia.se, campobet.se, swiper.se, lodur.se, dbet.com, quickcasino.se
**Deep-linking:** YES — event pages via `/sportsbook/event/{altenar_event_id}` (dbet: `/sports/#/event/{id}`)

### Place Bet
```
URL: https://{domain}/sv/sport (sportsbook landing)
     https://{domain}/sportsbook/event/{altenar_event_id} (deep link)
Flow:
1. Navigate to /sv/sport
2. Altenar WSDK widget loads (WebSocket connection to sb2wsdk-altenar2.biahosted.com)
3. Browse via:
   - Sport icon row: Live, Ishockey, Fotboll, Tennis, Basket, Bordtennis, Handboll, etc.
   - Left sidebar "TOPPLIGOR": SHL, Premier League, La Liga, etc.
   - Left sidebar "MENY": time filters (Alla, Idag, 3H, 6H, 24H, I Morgon)
   - Search box: "Fyll i lag- eller mästerskapsnamn"
4. Odds displayed as separate buttons per outcome:
   - Format: "{Team} {odds}" (e.g., "IK Sirius 1.04", "X 10.50")
5. Click odds button → bet slip opens
6. Enter stake → confirm

API:
- REST API at https://sb2frontend-altenar2.biahosted.com/api/...
- WebSocket SDK: sb2wsdk-altenar2.biahosted.com/altenarWSDK.js
```

### Check Balance
```
Visible in header when logged in
```

### My Bets
```
Accessible from account menu after login
```

### Deposit
```
URL: /account/deposit (betinia, campobet, swiper, lodur, quickcasino)
     /cashier (dbet)
Button: "Kassa" in nav (opens deposit overlay)
Payment: Zimpler, Swish
```

### Withdraw
```
URL: /account/withdraw (same account area)
```

### Settle/Results
```
No dedicated results section found in nav.
Live section "LIVE NU" shows ongoing matches with live scores.
```

### Bonus Status
```
URL: /sv/promotions (campaigns/offers page)
```

### Login
```
Button: "Spela Här" (top right)
Hamburger menu: "Open burger menu" (top left)
Method: BankID
```

### Brand Differences
```
- dbet: uses hash routing /sports/#/event/{id} and /cashier
- All others: /sportsbook/event/{id} and /account/deposit
- All share same Altenar WSDK widget
```

---

## 3. Spectate / 888/Evoke Platform (mrgreen.se — canonical)

**Brands:** mrgreen.se, 888sport.se
**Deep-linking:** NO — sportsbook loads as embedded iframe

### Place Bet
```
URL: /sport/ (landing page — sportsbook loads as embedded Spectate iframe)
Note: The actual sportsbook content is inside a Spectate iframe.
      Direct DOM interaction requires frame switching.
      Spectate API: https://spectate-web.{domain}/spectate/...
Flow:
1. Navigate to /sport/
2. Wait for Spectate iframe to load
3. Within iframe: browse sports → tournaments → matches
4. Click odds → bet slip within iframe
5. Enter stake → confirm

Alternative URLs:
- /sport/fotboll/ (football)
- /sport/ishockey/ (ice hockey)
- /sport/tennis/
- /sport/livespel/ (live betting)
```

### Check Balance
```
Shown in top header (outside iframe) when logged in
```

### My Bets
```
Within Spectate iframe — accessible after login
```

### Deposit
```
mrgreen: /insattning
888sport: /cashier
Payment: Visa, Trustly, MasterCard
```

### Withdraw
```
Same cashier area, different tab
```

### Login
```
mrgreen: buttons "Skapa konto" + "Logga in" in header
888sport: similar pattern
Method: BankID
```

### Brand Differences
```
- mrgreen: /sport/, /insattning
- 888sport: /betting, /cashier
- Both use same Spectate iframe for sportsbook
```

---

## 4. Gecko V2 / OBG Platform (betsson.com — canonical)

**Brands:** betsson.com, nordicbet.com, spelklubben.se, bethard.com
**Deep-linking:** NO — native SPA, navigation via internal routing

### Place Bet
```
URL: /sv/odds (betsson, nordicbet)
     /sv/sports (bethard)
     /sv/betting (spelklubben)
Flow:
1. Navigate to odds page
2. Browse via:
   - Sport icon row: LIVESPEL, PL, SHL, SVENSKA CUP, LA LIGA, NHL
   - Breadcrumb: Sporter A-Ö > Hem > {Sport} > {League}
   - Search: "Sök" in breadcrumb bar
3. Odds buttons: "{Team} {odds}" format (e.g., "Arsenal 1.49", "Oavgjort 4.45")
4. Click odds → RIGHT SIDEBAR "Kupong" (bet slip) populates
   - Tabs: Singel, Kombination, System
   - "Du behöver minst 1 val i din kupong"
5. Enter stake → click submit

Bet slip features:
- Right sidebar always visible (can toggle with "Dölj kupong")
- Tabs: Singel / Kombination / System
- "Logga in för att lägga spel" when not authenticated
```

### Check Balance
```
Shown in header after login (replaces "Logga in" / "Skapa konto" buttons)
```

### My Bets
```
Tab in right sidebar: "Öppna spel" (open/active bets)
Link: "Fullständig spelhistorik" (full bet history) under the bet slip
```

### Deposit
```
betsson: /sv/konto/insattning
nordicbet: /sv/konto/insattning
bethard: /sv/account/deposit
spelklubben: /account/deposit
```

### Withdraw
```
Same account area — /sv/konto/uttag or /account/withdraw
```

### Settle/Results
```
No dedicated results page. Live scores on match pages.
"Populära spel" section on home with pre-built combos.
```

### Login
```
betsson: "Logga in" → /sv/logga-in
         "Skapa konto" → /sv/oppna-konto
Method: BankID
```

### Brand Differences
```
- betsson: /sv/odds, /sv/logga-in, /sv/konto/insattning
- nordicbet: /sv/odds, similar paths
- bethard: /sv/sport, /sv/sports/oddsboost
- spelklubben: /sv/betting, /sport
- Init paths differ (bethard: /sv/sports, spelklubben: /sv/betting)
```

---

## 5. ComeOn Group (comeon.com — canonical)

**Brands:** comeon.com, hajper.com, lyllocasino.com, snabbare.com
**Deep-linking:** NO — RSocket SPA with internal routing

### Place Bet
```
URL: /sv/sportsbook (comeon, snabbare)
     /sv/odds (hajper, lyllo)
Flow:
1. Navigate to sportsbook
2. Left sidebar "Snabblänkar": Fotboll, Ishockey, Tennis, Basket, Odds Boost, Esport, Tipset, A-Ö Sporter
3. Center: featured matches with boost promotions
4. "Populära ligor" pills: Premier League, Svenska Cupen, LaLiga, Serie A, Bundesliga
5. Live section: "Populära Live" with odds buttons
6. Odds format: column headers "1" / "X" / "2" with decimal odds
7. Click odds → bet slip opens
8. Enter stake → confirm

Special features:
- "Lucky Bet" section: "Ange en insats och hur mycket du vill vinna"
- "Bonus Boost" / "FlashBoost" on featured matches
- "Odds Boost" in left sidebar navigation (sport/85-odds-boost)
```

### Check Balance
```
Header area after login
```

### My Bets
```
Accessible from account menu after login
URL pattern: /sv/sportsbook/my-bets (to be confirmed with login)
```

### Deposit
```
comeon: /sv/cashier/deposit
hajper: /sv/cashier/deposit
lyllo: /sv/cashier/deposit
snabbare: /sv/konto/insattning
Payment: Swish, Trustly (BankID required)
```

### Withdraw
```
Same cashier area: /sv/cashier/withdraw
snabbare: /sv/konto/uttag
```

### Login
```
Button: "Spela här" (top right, green)
All: BankID verification
Nav: Betting, Casino, Live Casino, Virtuellt | Erbjudanden, Kundtjänst
```

### Brand Differences
```
- comeon: /sv/sportsbook, full sidebar with Odds Boost + Lucky Bet
- hajper: /sv/odds, similar layout
- lyllo: /sv/odds, NO Odds Boost section in sidebar
- snabbare: /sportsbook (note: /sv/sport redirects to main page!)
  - snabbare has "SnabbSpelet", "SnabbTipset", "Blixtboost" exclusive features
  - Landing page has inline deposit form "Sätt in och Spela"
```

---

## 6. 10Bet (Playtech/Mojito)

**Deep-linking:** NO — DOM-based SPA with `ta-*` test attribute selectors

### Place Bet
```
URL: /sports
Flow:
1. Navigate to /sports
2. Tabs: Hem (Home), Liveodds
3. Left sidebar "Hett Just Nu": trending matches
4. Left sidebar "Sporter": Fotboll★, Tennis★, Basket★, Ishockey★, Handboll★ (favorite stars)
5. Sport filter pills: Fotboll (67), Tennis (7), Basket (26), Ishockey (27), Handboll (15)
6. Market dropdowns: "Slutresultat" (1x2), "Totala mål" (over/under)
7. Odds columns: 1, X, 2 with odds + Över/Under columns
8. Lock icons (🔒) on suspended markets
9. Click odds → bet slip opens
10. Enter stake → confirm

Technical:
- Playtech Mojito SPA loads via sportswidget.10bet.se
- Uses ta-* test attribute selectors for DOM elements
- DBX (DraftBoard) framework: "DBX: sportsbook is already initialized"
- Version tracking: "Ver. 25.12.3.0"
```

### Check Balance
```
Header area after login
```

### My Bets
```
Under account menu → "Logga in" required
```

### Deposit
```
URL: /account/deposit
```

### Login
```
Buttons: "Logga in" + "Bli Kund" (become customer)
Method: BankID
```

---

## 7. Snabbare (Sportradar MTS)

See ComeOn Group (Section 5) — same platform.

```
Sportsbook URL: /sportsbook (NOT /sv/sport!)
Deposit URL: /sv/konto/insattning
Landing page has inline deposit: "Sätt in och Spela" with amount input
Nav: Casino, Live Casino, Betting (/sportsbook), Lobby
Bottom nav: Casino, Live Casino, Betting, Snabbare
```

---

## 8. VBet (BetConstruct)

**Deep-linking:** NO — native SPA with Swarm WebSocket

### Place Bet
```
URL: /sv/pre-match (NOT /sv/sports — that gives 404!)
Flow:
1. Navigate to /sv/pre-match
2. Sub-nav tabs: Hem, Resultat, Livekalender, Statistik
3. Left sidebar: LIVE, ODDS, CASINO, LIVE CASINO, E-SPORTS
4. Odds columns: W1, X, W2, 1X, 12, X2 (Asian/double chance visible!)
5. Matches listed with date/time + league header
6. Click odds button → "Spelkupong" (bet slip) appears at bottom right
7. Enter stake → confirm

Technical:
- WebSocket: wss://eu-swarm-newm.vbet.se/ (BetConstruct Swarm)
- View switch: "Klassisk" dropdown with theme toggle (Light/Dark)
```

### Check Balance
```
Header area after login
```

### My Bets
```
Under account section after login
```

### Deposit
```
URL: /sv/account/deposit
```

### Settle/Results
```
UNIQUE: "Resultat" tab directly in sub-navigation!
URL: /sv/pre-match (with Resultat tab selected)
Also: "Statistik" tab for statistics
Also: "Livekalender" tab for upcoming live events
```

### Login
```
Buttons: "Öppna konto" (pink/magenta) + "Logga in"
Method: BankID
Spelgränser: /sv/sports?section=account&menu=personalInformation&nested=limits
```

---

## 9. Interwetten

**Deep-linking:** NO — SSR with SignalR live updates

### Place Bet
```
URL: /sv/sportsbook
Flow:
1. Navigate to /sv/sportsbook
2. Nav: Search, Odds, Live, Casino, Live Casino, App
3. Right side: "Spelkupong 0" (bet slip count) + "Mina spel 0" (my bets count)
4. Left sidebar "Odds": Europeiska toppligor, ATP/WTA Tour, TOPP Basket, Sverige
5. Left sidebar quick links: Liveodds, Last Minute, Odds under 2.00, Idag
6. Time filter: 1h, 2h, 4h, 6h, 12h + Go
7. Left sidebar "Alla bett": Fotboll, Tennis, Basket, Ishockey, etc.
8. Center: featured matches with ODDS BOOST prominently displayed
9. Right sidebar "Heta Bets": top 5 popular bets + payout calculator
10. Odds format: "1" / odds / "X" columns
11. Click odds → adds to "Spelkupong"
12. Enter stake → confirm

Technical:
- SSR (server-side rendered) — NOT a pure SPA
- SignalR WebSocket for live odds updates
- ODDS BOOST shown with strikethrough original + boosted odds (e.g., 3.60 → 4.00)
```

### Check Balance
```
Header area after login
```

### My Bets
```
DIRECTLY IN TOP NAV: "Mina spel 0" (My bets with count badge)
This is accessible without extra navigation — unique among providers!
```

### Deposit
```
URL: /sv/account/deposit
```

### Bonus Status
```
ODDS BOOST promotions visible directly on front page with
original odds (strikethrough) and boosted odds
```

### Login
```
Button: "REGISTRERA" (yellow, top right)
Method: BankID
Top bar: Spelgränser, Spelpaus, Självtest, Förebyggande av spelberoende
```

---

## 10. Coolbet (GAN Sports)

**Deep-linking:** NO — native SPA

### Place Bet
```
URL: /sv/odds (redirects to /sv/odds/recommendations)
Flow:
1. Navigate to /sv/odds
2. Nav icons: Sportmeny (hamburger), Odds (highlighted), Live (197 count), Favoriter (star)
3. Search bar: "Sök..."
4. Time filter pills: TOPPVAL, LIVE, STREAMING, 1H, 6H, 24H, HELGEN, MINA FAVORITER
5. Match cards (full width):
   - Team names as column headers
   - 1x2 odds in buttons: "Arsenal 1.54", "Oavgjort 4.58", "Chelsea 6.55"
   - Over/Under inline: "Ö 0.5" / "U 0.5" with odds
   - "+755" for additional markets count
   - "Early Win" special bet type
6. Odds buttons: green = selected, gray = normal
7. Click odds → bet slip opens
8. Enter stake → confirm

Technical:
- Uses Camoufox anti-detect Firefox for extraction (bypasses Imperva)
- "Mina Favoriter" personal favorites feature
```

### Check Balance
```
Header area after login
```

### My Bets
```
Behind login — accessible from account menu
```

### Deposit
```
URL: /sv/konto/insattning
```

### Login
```
Buttons: "REGISTRERA" + "LOGGA IN" (top right)
Language: sv (Swedish flag + "SV" label)
Top bar: KUNDTJÄNST, SPELGRÄNSER, SPELPAUS, SJÄLVTEST
Method: BankID
Bottom nav: Odds, Casino, Live Casino, Poker, Mer
```

---

## 11. Tipwin

**Deep-linking:** NO — SPA with WebSocket offer feed

### Place Bet
```
URL: /sv/sports/full
Flow:
1. Navigate to /sv/sports/ (redirects to /sv/sports/full)
2. Nav: Startsidan, Sportspel (current), Live-Sportspel, Kasino
3. Left sidebar sport icons: Fullständigt erbjudande, Top Euro Elite, Top Americas, Top Asia
4. Left sidebar sports: Fotboll (937), Tennis (104), Basket (131), Ishockey (60), Handboll (42)
5. Left sidebar leagues: country flags + league names with counts
6. Center: Market dropdowns: "3-vägs" (3-way), "Dubbelchans" (double chance)
7. Odds columns: 1x, 12, x2 (depending on selected market type)
8. Matches with date/time + favorite star + streaming/stats icons
9. Right sidebar "Spelkupong" (bet slip):
   - Tabs: Kombination, System, One-click spel
   - "Inga spel har valts"
10. Click odds → adds to Spelkupong
11. Enter stake → confirm

Special features:
- "Mina spel" (My bets) directly visible in RIGHT SIDEBAR!
  Shows "Logga in eller registrera dig för att se dina vad" when not logged in
- "Snabbt spel" (Quick bet) section in right sidebar
- "One-click spel" mode for rapid betting
- Theme toggle: "Ljus" (Light) mode available
```

### Check Balance
```
Header area after login
```

### My Bets
```
RIGHT SIDEBAR: "Mina spel" section directly below bet slip!
"Logga in eller registrera dig för att se dina vad" when not logged in
```

### Deposit
```
URL: /sv/account/deposit
```

### Login
```
Buttons: "Logga in" + "Skapa konto nu"
Account settings: /sv/account-settings/my-limits
Method: BankID
Top bar: Game pause (Spelpaus), Limit, Self test
```

---

## CRITICAL: Provider Migrations Discovered (2026-03-01)

**5 active providers in the extraction pipeline are dead/redirected. These need immediate action in `providers.yaml`.**

### Dead/Redirected Providers

| Provider | Status | Redirects To | Original Platform | Current Platform |
|----------|--------|-------------|-------------------|-----------------|
| **expekt** | DEAD | campobet.se | Kambi | Altenar (via campobet) |
| **campobet** | DEAD | speedybet.com | Altenar | Kambi (via speedybet) |
| **nordicbet** | DEAD | campobet.se | Gecko V2 | Altenar (via campobet) |
| **bethard** | DEAD | dbet.com | Gecko V2 | Altenar (via dbet) |
| **dbet** | DEAD | spelklubben.se | Altenar | Gecko V2 (via spelklubben) |
| **swiper** | DEAD | betmgm.se (404) | Altenar | DEAD |

### Platform Misclassifications

| Provider | Listed As | Actually Is | Notes |
|----------|-----------|-------------|-------|
| **betmgm** | Kambi | LeoVegas/MGM | Next.js SPA, Datadog, LaunchDarkly; sportsbook at `/sport` |
| **goldenbull** | Kambi | PAF platform | Login-gated, `?flowType=deposit`, owned by PAF New Tech Ltd |
| **1x2** | Kambi | PAF platform | Login-gated, `?flowType=deposit`, owned by PAF MT Ltd |
| **lyllo** | (implicitly Altenar) | ComeOn Group | Proprietary sportsbook at `/sv/sportsbook`, same as comeon.com |

**Impact:** 6 dead + 4 misclassified = **10 providers** in `active:` list that need attention.

**Recommended action:** Remove all 6 dead from `active:` and extraction tiers. Reclassify the 4 misclassified providers with correct platform configs. The brands they redirect to (speedybet, spelklubben) are already in the active list.

### Domain Changes

| Provider | Old URL | New URL | Notes |
|----------|---------|---------|-------|
| x3000 | x3000.se | **x3000.com** | .se redirects to .com |
| 888sport | /betting | **/** (root only) | /betting fails with ERR_ABORTED |

### Sportsbook URL Corrections (APPLIED to url_builder.py)

| Provider | Old URL | Corrected URL |
|----------|---------|---------------|
| vbet | /sv/sports | **/sv/pre-match** |
| snabbare | /sv/sport | **/sportsbook** |
| betinia | /sportsbook | **/sv/sport** |
| spelklubben | /sport | **/sv/betting** |
| hajper | /sv/odds | **/sportsbook** |
| lodur | /sportsbook | **/sv/sport** |
| quickcasino | /sportsbook | **/sv/sport** |
| lyllo | /sv/odds | **/sv/sportsbook** |
| betmgm | /betting | **/sport** |
| goldenbull | /betting | **/en/betting** |
| 1x2 | /betting | **/en/betting** |
| x3000 domain | x3000.se | **x3000.com** |
| speedybet deposit | /sv/myaccount/cashier | **/sv/betting?flowType=deposit** |
| x3000 deposit | /myaccount/cashier | **/betting?flowType=deposit** |
| goldenbull deposit | /myaccount/cashier | **/en/betting?flowType=deposit** |
| 1x2 deposit | /myaccount/cashier | **/en/betting?flowType=deposit** |

### Sibling Brands Discovered

- **SpeedyBet + X3000** are both owned by Paf (same platform, identical layout, identical `?flowType=deposit` pattern)
- **LeoVegas** sportsbook is login-gated (Kambi widget doesn't render without auth)

---

## Common UI Patterns

### All Swedish Sites Share
- **Top responsibility bar**: Spelgränser, Spelpaus, Självtest (order varies)
- **Cookie consent**: GDPR popup on first visit (must dismiss for automation)
- **Login**: BankID verification (cannot automate — user must authenticate)
- **Currency**: SEK everywhere
- **Language**: sv_SE / Swedish

### Bet Slip Patterns
| Platform | Bet Slip Location | Bet Types | Submit Button |
|----------|------------------|-----------|---------------|
| Kambi | Bottom floating panel | Singlar | "Lägg spel" |
| Altenar | Side panel / overlay | Singel, Kombo | varies |
| Betsson/OBG | Right sidebar "Kupong" | Singel, Kombination, System | varies |
| ComeOn | Overlay / side panel | Single, Combo | varies |
| 10Bet | Side panel | Single, Combo | varies |
| VBet | Bottom right "Spelkupong" | Single, Combo, System | varies |
| Interwetten | "Spelkupong" in nav | Single, Combo | varies |
| Coolbet | Side panel | Single, Combo | varies |
| Tipwin | Right sidebar "Spelkupong" | Kombination, System, One-click | varies |

### Key Automation Selectors Pattern
```
Cookie dismiss:
- "Acceptera alla cookies" / "Acceptera alla" / "Tillåt alla" / "Acceptera allt"

Odds buttons:
- Kambi: button elements within match row containing odds value
- Altenar: outcome buttons with team name + odds
- OBG: clickable odds cells
- Most: button or div with decimal odds text (1.75, 2.10, etc.)

Stake input:
- Usually: input[type="text"] or input[type="number"] in bet slip
- Placeholder: "0.00 kr", "Insats", "Ange belopp"

Place bet button:
- "Lägg spel" (Kambi, others)
- "Placera spel"
- Usually disabled until stake > 0
```

### Results/Settlement Paths
| Provider | Has Results Page | Path |
|----------|-----------------|------|
| VBet | YES | "Resultat" tab on /sv/pre-match |
| VBet | YES | "Statistik" tab on /sv/pre-match |
| Interwetten | PARTIAL | Live scores on match pages |
| Others | NO | Live scores only during matches |

### My Bets Access
| Provider | Access Method | Location |
|----------|--------------|----------|
| Interwetten | **Direct nav link** | "Mina spel" in top nav bar |
| Tipwin | **Sidebar section** | "Mina spel" in right sidebar |
| Betsson/OBG | **Tab in bet slip** | "Öppna spel" tab + "Fullständig spelhistorik" link |
| Others | **Account menu** | Behind login, in account dropdown |

---

## API-Only Automation Potential

| Platform | Bet Placement API | Balance API | My Bets API | Notes |
|----------|------------------|-------------|-------------|-------|
| Kambi | Possible (internal XHR) | /wallitt/mainbalance | /betting/sports/mybets | Needs auth cookies |
| Altenar | Via WSDK WebSocket | Via platform API | Via platform API | Rate limited |
| BetConstruct | Via Swarm WS | Via Swarm WS | Via Swarm WS | get_bet_history command |
| Spectate | Via Spectate API | Via platform | Via platform | Inside iframe |
| 10Bet | Via Mojito API | Via Mojito | Via Mojito | ta-* selectors for DOM |
| Coolbet | Via GAN API | Via GAN API | Via GAN API | Imperva protection |
| Tipwin | Via WS offer feed | Via API | Via API | WebSocket-based |
| ComeOn | Via RSocket WS | Via platform | Via platform | Binary RSocket protocol |

---

## Next Steps for Implementation

### Phase 0 — Cleanup (IMMEDIATE)
1. Remove dead providers from `providers.yaml` active list: expekt, campobet, nordicbet, bethard, dbet, swiper
2. Reclassify betmgm (LeoVegas/MGM platform, NOT Kambi)
3. Verify goldenbull + 1x2 platform (PAF — may still use Kambi API under the hood)
4. Fix x3000 domain from .se to .com across all config
5. Fix lyllo classification (ComeOn Group, not Altenar)
6. Fix 888sport extractor if it relies on /betting URL path
7. Remove dead providers from constants.py (PLATFORM_MAP, PLATFORM_GROUPS, PROVIDER_DOMAINS)
8. Remove dead providers from scheduler.py tier lists

### Phase 1 — Session Bootstrap
1. Implement BankID login handler (user authenticates, we capture session cookies)
2. Cookie/session persistence per provider in Chrome profile
3. Auto-detect session expiry and re-authenticate

### Phase 2 — Bet Placement (P0)
1. Implement Kambi BetPlacer (covers 6 brands via deep linking — expekt/betmgm removed)
2. Implement Altenar BetPlacer (covers 3 brands via deep linking — campobet/dbet/swiper removed)
3. Implement generic browser-based placer for remaining platforms
4. Odds verification before placement (compare vs expected odds)
5. Odds change handling (accept within tolerance, reject otherwise)

### Phase 3 — Account Management (P1)
1. Balance reading from each platform
2. Deposit automation (navigate to cashier, fill amount)
3. Withdrawal automation
4. My Bets scraping for settlement verification

### Phase 4 — Settlement & Monitoring (P1)
1. VBet Results tab scraping (best source — has dedicated results page)
2. Live score monitoring across platforms
3. Auto-settlement cross-reference with bet records

## Active Provider Count (Post-Cleanup)

| Platform | Active Brands | Names |
|----------|--------------|-------|
| Kambi | 4 | unibet, leovegas, speedybet, x3000 |
| Kambi (PAF) | 2 | goldenbull, 1x2 (PAF platform — verify Kambi API still works) |
| LeoVegas/MGM | 1 | betmgm (NOT Kambi — needs own extractor or removal) |
| Altenar | 3 | betinia, lodur, quickcasino |
| Gecko V2 | 2 | betsson, spelklubben |
| ComeOn | 4 | comeon, hajper, lyllo, snabbare |
| Spectate | 2 | mrgreen, 888sport |
| Standalone | 5 | vbet, interwetten, 10bet, coolbet, tipwin |
| Sharp | 1 | pinnacle |
| Prediction | 1 | polymarket |
| **Total** | **25** | (down from 31 after removing 6 dead providers) |
