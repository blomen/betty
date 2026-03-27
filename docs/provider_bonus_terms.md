# Provider Sport Betting Bonus Terms — Raw Audit (2026-03-27)

> Scraped from each provider's official bonus/promotions pages. Sport betting section ONLY (not casino).
> Some providers use JS-rendered SPAs and couldn't be scraped directly — noted where applicable.

---

## Table of Contents

- [Kambi Group](#kambi-group) — Unibet, LeoVegas, Expekt, BetMGM, SpeedyBet, X3000, Golden Bull, 1X2
- [Altenar Group](#altenar-group) — Betinia/10bet, CampoBet, Swiper, Lodur, Dbet, QuickCasino
- [Gecko / OBG](#gecko--obg) — Betsson, Betsafe, NordicBet, Spelklubben, Bethard
- [ComeOn Group](#comeon-group) — ComeOn, Hajper, Lyllo
- [Spectate / 888](#spectate--888) — Mr Green, 888sport
- [Independents](#independents) — 10Bet, Snabbare, Interwetten, Coolbet, Tipwin, VBet

---

## Kambi Group

### Unibet

**Source:** oddsbonusar.se, bettingsidor.se (unibet.se returns 503 — Cloudflare blocked)

- **Bonus:** 100% matchat gratisspel upp till 1 000 kr
- **Min insättning:** 100 kr
- **Trigger:** Placera ett spel på minst 100 kr med minsta odds 1.80 (single bet)
- **Omsättning på vinster:** Inga omsättningskrav på vinster från gratisspelet
- **Giltighet:** 60 dagar
- **Övrigt:** Välj välkomsterbjudande vid registrering. Gäller sedan ny licens oktober 2025.

**Verdict:** Config ✅ — `type: freebet, amount: 1000, wagering_multiplier: 1, min_odds: 1.80, trigger_mode: single`

---

### LeoVegas

**Source:** leovegas.com/sv-se/kampanjer/bonusar/s + /villkor — fetched directly, verbatim

**Fullständiga villkor:**
- Erbjudandet gäller nya kunder bosatta i Sverige som valt välkomsterbjudandet för sport.
- Gäller spelare registrerade from 27 november 2025.
- Första insättning minst 100 kr.
- Bonus i form av riktiga pengar som matchar första insättningen, upp till max 600 kr.
- **För att motta bonusen behöver man omsätta sin första insättning sex gånger.** (Exempel: 600 kr insättning → omsätt 3 600 kr → motta 600 kr i riktiga pengar)
- Endast spel med odds 1.80 eller mer räknas. Kombinationsspel sammanlagt minst 1.80 (ex. 1.4×1.4 = 1.96).
- Giltig 60 dagar från att man valt erbjudandet och satt in första insättningen.
- Klicka "Hämta" på bonuskortet under "Mina erbjudanden" för att få pengarna.
- Envägsspel, systemspel och cash out kvalificerar sig INTE.
- Satsning på båda sidor i ett event → erbjudande tas bort.
- Begränsat till ett per hushåll/e-post/telefon/betalmetod.

**Verdict:** Config ✅ — `type: bonusdeposit, amount: 600, trigger_multiplier: 6, trigger_odds: 1.80, wagering_multiplier: 0`
Wagering is on DEPOSIT only (6× deposit to unlock bonus as cash). No post-unlock wagering.

---

### Expekt

**Source:** expekt.se/promotions + third-party verification (sport-specific T&C page returned 404)

- **Bonus:** 100% upp till 1 000 kr + 64 kr på Expekt-Tipset
- **Min insättning:** 100 kr
- **Omsättning:** 20× insättningen (NOT bonus). Minsta odds 1.80.
- **Giltighet:** 90 dagar
- **Extra:** 64 kr Expekt-Tipset, inga omsättningskrav på vinster.
- **Begränsningar:** Envägsspel, systemspel, cash out kvalificerar sig inte.

**Verdict:** Config ✅ — `type: bonusdeposit, amount: 1000, trigger_multiplier: 20, trigger_odds: 1.80, wagering_multiplier: 0, deadline_days: 90`

---

### BetMGM

**Source:** betmgm.se/kampanjer/sport/valkomstbonus + /villkor — fetched directly, verbatim

**Fullständiga villkor:**
- Gäller nya kunder bosatta i Sverige registrerade from 17 december 2025.
- Välj Välkomsterbjudandet för Sport vid registrering.
- Första insättning minst 100 kr.
- Matchad bonus upp till 500 kr + extraspel MGM-Tipset värt 64 kr.
- **Omsätt din första insättning 10 gånger.** (Exempel: 500 kr → omsätt 5 000 kr → motta 500 kr)
- Odds minst 1.80 (kombination sammanlagt minst 1.80).
- Giltig 60 dagar.
- Klicka "Hämta" under "Mina erbjudanden".
- Envägsspel, systemspel, cash out kvalificerar sig INTE.
- Extraspelet 64 kr: system av 6 halvgarderingar. Giltigt 60 dagar efter omsättningskravet mötts. Inga omsättningskrav på vinster.

**Verdict:** Config ✅ — `type: bonusdeposit, amount: 500, trigger_multiplier: 10, trigger_odds: 1.80, wagering_multiplier: 0`

---

### SpeedyBet

**Source:** speedybet.com/en/bonus-offer — fetched directly, verbatim

**Betting Bonus villkor:**
- Matchar första insättning 100% upp till 500 kr.
- Aktiveras automatiskt vid första insättning.
- **Omsättningskravet: 12×.** Endast bettingspel avgjorda under perioden med minimiodds 1.8.
- Cash Out räknas INTE.
- Bidrag: 100% Betting, 0% alla andra kategorier.
- Min insättning 100 kr.
- Maxbet per spel 50 kr (gäller alla spel utom betting).
- 60 dagar att fullfölja. Erbjudandet giltigt 365 dagar efter registrering.
- Bonussaldo nollställs vid uttag.

**Verdict:** Config ✅ — `type: bonusdeposit, amount: 500, wagering_multiplier: 12, min_odds: 1.80`
Wagering is on BONUS only (12× bonus amount). No trigger needed — instant credit.

---

### X3000

**Source:** x3000.com/bonus-offer — fetched directly, verbatim

**Betting bonus villkor (identisk struktur som SpeedyBet):**
- 100% matchning upp till 500 kr.
- Aktiveras automatiskt vid första insättning.
- **Omsättningskrav: 12×.** Minimiodds 1.8. Cash Out räknas inte.
- Min insättning 100 kr. Maxbet 50 kr (utom betting).
- 60 dagar. Erbjudandet giltigt 365 dagar.

**Verdict:** Config ✅ — `type: bonusdeposit, amount: 500, wagering_multiplier: 12, min_odds: 1.80`

---

### Golden Bull

**Source:** goldenbull.se/en/bonus-info — fetched directly, verbatim

**Betting Bonus (identisk som SpeedyBet/X3000):**
- 100% matchning upp till 500 kr.
- Aktiveras automatiskt.
- **Omsättningskrav: 12×.** Minimiodds 1.8. Cash Out räknas inte.
- **⚠️ Min insättning 200 kr** (NOT 100 kr like the others!)
- 60 dagar. Erbjudandet giltigt 365 dagar.

**Verdict:** Config ✅ (but note higher min deposit of 200 kr)

---

### 1X2

**Source:** 1x2.se blog article about wagering + WebSearch

- 100% matchning upp till 500 kr + 64 kr 1X2-Tipset.
- **Omsättningskrav: 12× bonusbeloppet** (500 kr bonus = 6 000 kr).
- Minimiodds 1.80. Cash Out räknas inte.
- 1X2-Tipset bidrar 100% till omsättning.
- Min insättning 100 kr. Maxbet 50 kr (utom betting).
- **⚠️ 90 dagars giltighet** (blog article says 90; may vary)

**Verdict:** Config needs fix — `deadline_days: 90` missing (defaults to 60)

---

## Altenar Group

### Betinia → 10bet.se (⚠️ PLATFORM CHANGE)

**Source:** 10bet.se/promotion/38111 (betinia.se now redirects to 10bet.se!)

**⚠️ IMPORTANT: Betinia has been migrated to 10bet.se (Blue Star Planet Limited). Completely different platform and terms from old Soft2Bet/Romix.**

**Fullständiga villkor:**
1. Välj bonus vid registrering.
2. Minsta insättning 100 kr. Skrill/Neteller undantagna.
3. 100% bonus av kvalificerande insättning, upp till 1 000 kr, krediteras omedelbart till Sportbonuskonto.
4. **Insättning och bonus måste omsättas 15 gånger:** singel minsta odds 1.80, flerval minsta odds 1.40 per val.
5. Maximala bonusvinster: 10 000 kr.
6. Ogiltiga/avbrutna/cash out/freebet-spel räknas INTE.
7. Endast första spelet per marknad och evenemang räknas.
8. 60 dagar. Uttag innan omsättning = bonus förverkas.
9. Begränsat till ett per person/familj/adress/telefon/IP.

**Verdict:** ⚠️ CONFIG WRONG — Currently `wagering_multiplier: 6, trigger_odds: 1.50`. Should be `wagering_multiplier: 15, min_odds: 1.80` with NO trigger (immediate credit). Betinia is now 10bet platform!

---

### CampoBet

**Source:** campobet.se/sv/promotions/sport/welcome-bonus — fetched directly, verbatim

**Fullständiga villkor:**
1. Nya spelare, ej tidigare spelat på Romix Limited.
2. Minsta insättning 100 kr.
3. Första insättningen bestämmer bonusens värde.
4. **Trigger:** Omsätt insättningen 1× till minsta odds 1.50. → Bonus låses upp.
5. Max bonus 500 kr.
6. **Omsättning:** Insättning och bonus 6× i: singel minsta odds 1.80, multipelspel minsta 1.40 per urval.
7. Cash-out, systemspel, void, casino räknas INTE.
8. **Max insats mot omsättning: 500 kr per spel.**
9. Bara första rattade spelet per evenemang räknas.
10. Välj sport ELLER casino bonus.
11. Uttag medan bonus aktiv = förlorar bonus.
12. **60 dagar.**

**Verdict:** Config needs review — `wagering_multiplier: 6` but actual base is (insättning + bonus) × 6.

---

### Swiper

**Source:** swiper.se/sv/promotions/sport/welcome-bonus-sport — fetched directly, verbatim

**Identisk struktur som CampoBet men:**
- Max bonus: **1 000 kr** (vs 500 kr CampoBet)
- Trigger: 1× insättning vid odds 1.50
- **Omsättning: Insättning och bonus 6× vid odds 1.80 singel / 1.40 per multipelval**
- Max insats mot omsättning: 500 kr per spel
- 60 dagar

**Verdict:** Config needs review — same (dep+bonus)×6 issue.

---

### Lodur

**Source:** lodur.se/sv/promotions/sport/welcome-bonus-sport — fetched directly, verbatim

**Identisk som Swiper med en skillnad:**
- Max bonus: **1 000 kr**
- **⚠️ Punkt 5: Manuell aktivering krävs** — "måste du aktivera välkomstbonusen för sport i sektionen Mina bonusar i din profil"
- Trigger: 1× insättning vid odds 1.50
- **Omsättning: Insättning och bonus 6× vid odds 1.80 / 1.40**
- Max insats mot omsättning: 500 kr
- 60 dagar

**Verdict:** Config needs review — same (dep+bonus)×6 issue.

---

### Dbet

**Source:** dbet.com/welcome-bonus-sports/ — fetched directly, verbatim

**Fullständiga villkor:**
1. Nya spelare, första insättning minst 100 kr.
2. Gratisspel tillgängligt from 1 oktober 2025.
3. Giltigt 60 dagar från aktivering.
4. Aktivera via spelkupongen innan spelet placeras.
5. **Trigger:** Första spel med riktiga pengar, minimumodds 1.80 singel eller total 1.80 kombo.
6. Systemspel och enkelriktade spel kvalificerar sig INTE.
7. Deluttag/uttagna spel kvalificerar sig inte.
8. Avgör första spelet inom 60 dagar.
9. Gratisspel = insats för första spelet ELLER första insatta beloppet (det lägsta), max 500 kr.
10. Måste användas vid ett tillfälle (singel eller kombo).
11. Om event ställs in → nytt gratisspel.
12. **Inga omsättningskrav på vinster.** Tillgängliga för uttag direkt.
13. Ett per kund/hushåll. Kan kombineras med casino-bonus.

**Verdict:** Config ✅ — `type: freebet, amount: 500, wagering_multiplier: 1, min_odds: 1.80, trigger_mode: single`

---

### QuickCasino

**Source:** quickcasino.se/sv/promotions/sport/welcome-bonus-sport — fetched directly, verbatim

**Identisk som CampoBet med en skillnad:**
- Max bonus: **500 kr**
- Trigger: 1× insättning vid odds 1.50
- **Omsättning: Insättning och bonus 6× vid odds 1.80 / 1.40**
- Max insats mot omsättning: 500 kr
- 60 dagar
- **⚠️ Punkt 18 (extra):** "måste du göra en insättning med din föredragna betalningsmetod för att möjliggöra uttag" — kräver extra insättning efter omsättning!

**Verdict:** Config needs review — same (dep+bonus)×6 issue.

---

## Gecko / OBG

### Betsson

**Source:** FAILED — betsson.com returns compressed/garbled content (heavy SPA, Brotli encoding). Could not scrape.

**From sister brands (Betsafe/NordicBet) and third-party sources:**
- Likely freebet format, 250 kr, single trigger at 1.80+
- Needs manual verification in browser.

**Verdict:** Config assumed correct — `type: freebet, amount: 250, min_odds: 1.80, trigger_mode: single`

---

### Betsafe

**Source:** betsafe.com/sv/promotions — partial extraction

**Synligt på kampanjsidan:**
> Odds - Få ett 100 kr gratisspel + 50 freespins!
> Sätt in och lägg ett spel för 100 kr med minst ett val till min. odds 1.80 och få ett gratisspel värt 100 kr.
> Freespins gäller Pirots 4 och ev. vinster måste omsättas 35 gånger.
> Giltig i 60 dagar.

**Verdict:** Config ✅ — `type: freebet, amount: 100, min_odds: 1.80, trigger_mode: single`

---

### NordicBet

**Source:** nordicbet.com/sv/kampanjer — partial extraction

**Identisk som Betsafe:**
> Odds - Hämta ett 100 kr gratisspel + 50 freespins!
> Sätt in och lägg ett spel för 100 kr med minst ett val till min. odds 1.80.
> 60 dagar.

**Verdict:** Config ✅ — `type: freebet, amount: 100, min_odds: 1.80, trigger_mode: single`

---

### Spelklubben

**Source:** spelklubben.se/sv/welcome-bonus — full terms extracted

**Fullständiga villkor:**
- 100% matchad bonus upp till 500 kr.
- Min insättning 100 kr.
- Välj Casino eller Sport bonus FÖRE första insättningen.
- **Omsättning: Insättning och bonus 15× med min. odds 1.90.**
- Bara sportspel räknas.
- **Bara ditt första avgjorda spel per match/marknad räknas.**
- Cash-out/avbrutna/void räknas inte.
- 60 dagar.
- Real money spelas först, bonuspengar aktiveras efter.

**Verdict:** Config needs review — `wagering_multiplier: 15` but actual base is (dep+bonus)×15

---

### Bethard

**Source:** FAILED — SPA-rendered, no content extractable

**From main page inline promo text:**
> Min. insättning 100 kr. Max bonus 500 kr. Omsättning: 15x Insättning + Bonus. Min. odds 1,90. Inom 60 dagar.

**Verdict:** Config needs review — same as Spelklubben: (dep+bonus)×15

---

## ComeOn Group

### ComeOn

**Source:** FAILED — SPA-rendered. Title visible: "Insätt 250 kr — få 1000 kr + spins och free bet!"

**From promotions.comeon.com (first round research):**
- 100% deposit match up till 500 kr.
- Bonus aktiveras vid insättning.
- **Omsättning: 6× (bonus + deposit).** Min odds 1.80.
- **Max insats: 250 kr per spel.**
- ComeOn Tipset räknas INTE. Retrobet räknas INTE. Cash Out räknas INTE.
- 60 dagar.
- Sticky bonus (insättning + bonus låsta ihop).

**Verdict:** Config needs review — `wagering_multiplier: 6` but base is (dep+bonus)×6

---

### Hajper

**Source:** hajper.com/sv/bonusvillkor — fetched directly

**Fullständiga villkor:**
- **100% matchat GRATISSPEL upp till 500 kr** (NOT deposit match!)
- Min insättning 100 kr.
- Min odds 1.80.
- Tipset ingår ej i omsättning.
- **Vinster från gratisspel är omsättningsfria.**
- Gratisspelet kan inte tas ut som kontanter — bara vinster.
- 60 dagar.

**Verdict:** ⚠️ CONFIG WRONG — Currently `type: bonusdeposit, wagering_multiplier: 6`. Should be `type: freebet, amount: 500, wagering_multiplier: 1, min_odds: 1.80, trigger_mode: single`

---

### Lyllo

**Source:** lyllocasino.com/sv/bonusvillkor — NO SPORT BONUS FOUND

Site only shows casino welcome bonus (300%, 20× wagering). No separate sport betting welcome bonus advertised.

**From third-party research (round 1):**
- 100 kr gratisspel
- Trigger: insätt 100 kr, spela 100 kr vid odds 1.80+
- 1× omsättning
- 60 dagar, 7 dagars deadline på gratisspelet

**Verdict:** Config ✅ — `type: freebet, amount: 100, min_odds: 1.80, trigger_mode: single, deadline_days: 7`

---

## Spectate / 888

### Mr Green

**Source:** FAILED — SPA-rendered, no bonus content extractable

**From third-party research (round 1):**
- 500 kr livebet (free bet for live betting only)
- **⚠️ Min insättning: 500 kr** (with code "SPORT")
- Trigger: Placera 500 kr vid minsta odds 1.80
- **Vinster omsättningsfria**
- Alla insättningar måste omsättas 1× före uttag
- 60 dagar

**Verdict:** Config partially correct — `type: freebet, amount: 500, min_odds: 1.80, trigger_mode: single`. Note: requires 500 kr deposit (not 100 kr) and livebet only.

---

### 888sport

**Source:** 888sport.se/kampanjer — fetched directly, full verbatim T&C

**Fullständiga villkor:**
- Nya spelare som ej nyttjat välkomsterbjudande hos 888 tidigare.
- **Kampanjkod: SPORTBONUS** vid första insättning.
- Minsta insättning: **100 kr** i en enskild transaktion.
- **100% matchning** av första insättningen, upp till **500 kr** i bonusmedel.
- **Omsättningskrav: 14× bonusbeloppet.** (500 kr bonus = 7 000 kr)
- Bonusmedel kan bara användas på sport, **minsta odds 1.80** räknas.
- **"Non-sticky" bonus** — riktiga pengar och bonusmedel inte kopplade. Spelar först med egen insättning, vinster från den kan betalas ut omedelbart.
- Uttag innan omsättning = bonus + vinster förverkas.
- **60 dagar.**

**Verdict:** Config needs fix — `trigger_multiplier: 2, trigger_odds: 1.01` is wrong (no trigger exists). Should be: `wagering_multiplier: 14, min_odds: 1.80` with no trigger.

---

## Independents

### 10Bet

**Source:** 10bet.se/promotion/38111 (same as Betinia above — they merged)

**See Betinia section above.** Same terms: 15× (dep+bonus), min odds 1.80, 60 dagar.

**Verdict:** Config needs fix — `wagering_multiplier: 15` but base is (dep+bonus). Also Betinia config needs complete overhaul since it redirects here.

---

### Snabbare

**Source:** snabbare.com/sv/erbjudanden — verbatim

> 18+, Bonuserbjudande gäller nya kunder vid första insättnings- och speltillfälle. Du kan inte ta del av fler än ett välkomsterbjudande. Kan du inte se din bonus på ditt spelkonto efter din första insättning och bonusaktivering, vänligen kontakta kundtjänst direkt. Bonusen kan endast aktiveras innan första speltillfället.
>
> Bonusalternativ "Sport": 100% i bonus på din första insättning upp till 600 kr. Min. insättning är 100 kr. Maxbonus är 600 kr. Dessutom får du 100 jackpottspins på Tome Of Dead till ett värde av 1 kr/spin. **Insättning + bonus måste omsättas minst 8 gånger på Sport, min. odds 1.80 singel eller kombobet.** (Ogiltigförklarade spel eller Cashout-funktionen räknas inte till omsättningen, kupongen måste vara slutförd). Totala maxvärdet av detta bonusalternativ med en insättning på 600 kr, med Jackpottspins är 700 kr.
>
> Vinster från spins kan inte tas ut förrän bonuserbjudandets samtliga villkor är uppfyllda. Har spelaren av någon anledning avslutat sitt bonuserbjudande i förtid kommer vinsterna från dessa spins att förverkas.
>
> Omsättningskravet måste uppfyllas inom 90 dagar efter att bonusen aktiveras. Eventuella bonuspengar som inte har omsatts under perioden förverkas.
>
> Maxinsats är 50 kr per spelomgång både i Casino och Live-Casino, för att omsättningen ska räknas.
>
> När du aktiverar en bonus låser du ditt saldo i två delar: insättningen och bonuspengarna. Du spelar med båda delar vid varje insats. Avbryter du din bonus förlorar du bonussumman och alla eventuella vinster som bonusdelen av ditt saldo givit upphov till.

**Verdict:** Config needs review — `wagering_multiplier: 8` but actual base is (dep+bonus)×8. 90 days ✅

---

### Interwetten

**Source:** interwetten.se/sv/content/promotions/welcome-bonus — **⚠️ BONUS EXPIRED**

Page returns: "Kampanjen missades tyvärr. Tack för ditt intresse för vår kampanj. Tyvärr har denna kampanj redan avslutats."

Footer only mentions Casino bonus (5 000 kr). **No active sport welcome bonus on interwetten.se as of 2026-03-27.**

Banner "BLI EN DEL AV SPELET MED DIN 1.000KR BONUS" is still visible on homepage but links to expired page.

**From config (unverified, bonus may no longer exist):**
- Sport bonus upp till 1 000 kr
- Staged unlock: 5 steg × 20% vardera
- Trigger: Omsätt insättning ×5 vid odds 1.70+
- Omsättning: Varje steg 1× vid odds 1.70+
- 14 dagar deadline

**Verdict:** ⚠️ BONUS MAY BE EXPIRED. Config has `trigger_multiplier: 5, trigger_odds: 1.70, wagering_multiplier: 1, min_odds: 1.70, deadline_days: 14`. User manually set 5000 kr withdrawal wagering — this is separate from the welcome bonus.

---

### Coolbet

**Source:** coolbet.com/sv/valkomstbonus + /erbjudanden/regler/odds-bonus-regler — verbatim

> ODDS 100% VÄLKOMSTBONUS UPP TILL 1000 KR + 50 FREESPINS PÅ COOLBET SUGAR RUSH 1000
>
> - Detta erbjudande är endast tillgängligt för svenska spelare som inte tidigare har skapat något konto hos Coolbet och ännu inte placerat något spel.
> - Du kan endast välja en av bonusarna (sport eller casino) och minsta insättning för att erhålla en bonus är 100 kr. Bonusen kan enbart utnyttjas i samband med din första insättning.
> - **Bonusbeloppet måste omsättas på odds sex (6) gånger till minsta odds 1.50.** Din insättning har inga omsättningskrav, men det insatta beloppet är låst och kan inte tas ut innan omsättningskraven för bonusen har blivit uppnådda.
> - Singlar, kombinations- och systemspel accepteras, inklusive spel på livespel. Singelspel på odds lägre än 1.50 räknas INTE. Kombinations-/systemspel med lägre odds per leg räknas om totalodds ≥ 1.50. Spel satt till 1.00 räknas inte.
> - Free spins: lägsta möjliga värde, 60 dagar. Vinster krediteras som kontanter utan omsättningskrav.
> - Bonusen måste användas och omsättas inom 60 dagar.
> - Bonuserbjudanden gäller EJ insättningar via e-plånböcker (Neteller, Skrill).
> - Bonuskod: ODDS

**Verdict:** ⚠️ CONFIG WRONG — `min_odds: 1.80` should be `min_odds: 1.50`. Wagering is 6× bonus only ✅

---

### Tipwin

**Source:** tipwin.se/sv/info/bonus-terms — verbatim

> 100% välkomstbonus upp till 1000 kr
>
> Klient med antingen nytt eller befintligt konto kan få bonus vid första/nästa insättning.
>
> - Denna kampanj är exklusiv för alla klienter. Bonusen gäller endast för sport- och live-spelsatsning.
> - När du har registrerat och verifierat ditt konto läggs välkomstbonusen på din första/nästa insättning automatiskt till ditt konto. Kontot måste verifieras innan du begär bonus.
> - **Rollover - Det totala beloppet som innehas som bonusmedel måste satsas minst 7 gånger (7X) med alla satsningar avräknade innan insättningsbeloppet och bonusmedlen släpps för uttag.** Insättningar på spelkupong, på vilken cashout accepterades, ingår INTE i bonus rollover.
>   - Exempel: Kunden gör en insättning av 2000 kr och får hela bonusbeloppet av 1000 kr. Insättningsbeloppet av 2000 kr + bonusmedel av 1000 kr = 3000 kr. Hela beloppet av 3000 kr måste satsas minst 7 gånger (7X) innan medlen släpps för uttag.
> - **Den maximala insatsen per spelkupong som ingår i beräkningen av rollover är 1000 kr.** Minsta odds per spelkupong som ingår i beräkningen av rollover är 1.80.
> - Om du har mer än 10 kr på ditt kontobalans och du gör en ny insättning som du kräver bonusen med, kommer du inte att kunna lägga in en uttagsbegäran förrän bonusrollovervillkoren är uppfyllda.
> - Bonusen är begränsad till ett spelkonto per person, familj, hushåll eller dator.

**Verdict:** Config needs review — `wagering_multiplier: 7` but actual base is (dep+bonus)×7. Max 1000 kr/slip.

---

### VBet

**Source:** vbet.se/sv/promotions/all/810497 — verbatim

> 100% UPP TILL 800 SEK I VÄLKOMSTBONUS FÖR SPORT
>
> Registrera dig och gör en kvalificerande insättning för att få tillgång till bonusen.
>
> Hur kvalificerar du dig:
> 1. Registrera dig på vbet.se och verifiera ditt konto.
> 2. Gå till avsnittet "Sportbonus" och hämta bonusen.
> 3. Sätt in minst 100 SEK.
> 4. Satsa insättningen + bonus x10.
> 5. Få 100% Bonus upp till 800 SEK.
>
> Exempel: Sätt in 800 SEK och få en bonus på 800 SEK - totalt 1600 SEK att omsätta. **Omsätt hela beloppet på 1600 kr 10 gånger på kvalificerade sportspel (lägsta odds 1,8 för singelspel, 1,8 per val för kombinationsspel).** När du har slutfört överförs vinsterna till ditt kontantsaldo och kan tas ut **(upp till 10 gånger ditt insättningsbelopp).**
>
> - Sportbonusar tilldelas efter att du har gjort anspråk på dem och gjort din första kvalificerande insättning.
> - Högsta bonusbeloppet är 800 SEK.
> - När bonusen har tagits emot fryses insättningsbeloppet och måste först omsättas innan bonusmedlen kan användas.
> - Insättnings-och sportbonusen måste omsättas 10 gånger.
> - Min odds 1.8 singel, 1.8 per val kombo. Alla sporter/ligor/matcher.
> - Alla vinster krediteras som bonussaldo. Vid klart → automatiskt till kontantbalans.
> - **Max uttag: 10× insättningsbeloppet.**
> - 60 dagar.
> - Cash Out, Profit Booster, Free Bets, BetBuilder räknas INTE.
> - Void/avbrutet räknas inte.
> - Begränsat till en kund/familj/adress/delad IP.

**Verdict:** Config needs review — `wagering_multiplier: 10` but actual base is (dep+bonus)×10. Also max withdrawal cap.

---

## Summary: Config Discrepancies

### ❌ CRITICAL — Wrong type or wrong value

| Provider | Issue | Current Config | Correct Value |
|----------|-------|---------------|---------------|
| **Hajper** | Wrong type entirely | `bonusdeposit, wagering: 6×` | `freebet, no wagering on winnings` |
| **Coolbet** | Wrong min odds | `min_odds: 1.80` | `min_odds: 1.50` |
| **888sport** | Fake trigger exists | `trigger_multiplier: 2, trigger_odds: 1.01` | No trigger — just `wagering_multiplier: 14` |
| **Betinia** | Platform change | Old Soft2Bet 6× terms | Now 10bet.se — 15× (dep+bonus) |

### ⚠️ SYSTEMIC — Wagering base is (dep+bonus) not bonus-only

The code computes: `wagering_requirement = bonus_amount × wagering_multiplier`

These providers define wagering as **(insättning + bonus) × N**, not **bonus × N**. With 100% match (dep=bonus), the effective multiplier applied to bonus should be **2N**:

| Provider | Config × | Actual Terms | Effective × for code |
|----------|----------|-------------|---------------------|
| CampoBet | 6 | (dep+bonus)×6 | 12 |
| Swiper | 6 | (dep+bonus)×6 | 12 |
| Lodur | 6 | (dep+bonus)×6 | 12 |
| QuickCasino | 6 | (dep+bonus)×6 | 12 |
| ComeOn | 6 | (dep+bonus)×6 | 12 |
| Spelklubben | 15 | (dep+bonus)×15 | 30 |
| Bethard | 15 | (dep+bonus)×15 | 30 |
| 10bet | 15 | (dep+bonus)×15 | 30 |
| Snabbare | 8 | (dep+bonus)×8 | 16 |
| Tipwin | 7 | (dep+bonus)×7 | 14 |
| VBet | 10 | (dep+bonus)×10 | 20 |

### ✅ Wagering on bonus only (correct as-is)

| Provider | × | Notes |
|----------|---|-------|
| SpeedyBet | 12 | Explicit: "omsättningskravet 12×" on bonus |
| X3000 | 12 | Same Paf Group template |
| Golden Bull | 12 | Same Paf Group template |
| 1X2 | 12 | "12 gånger bonusbeloppet" |
| Coolbet | 6 | "6× bonusbeloppet" (but min_odds wrong) |
| 888sport | 14 | "14× bonusbeloppet" |

### ✅ Wagering on deposit only (trigger-to-unlock model)

| Provider | × | Notes |
|----------|---|-------|
| LeoVegas | 6 | 6× deposit → unlock bonus as cash |
| Expekt | 20 | 20× deposit → unlock bonus as cash |
| BetMGM | 10 | 10× deposit → unlock bonus as cash |

### 📝 Minor fixes needed

| Provider | Field | Current | Should Be |
|----------|-------|---------|-----------|
| 1X2 | deadline_days | (missing, default 60) | 90 |
| Mr Green | note | — | Requires 500 kr deposit, livebet only |
| Golden Bull | note | — | Min deposit 200 kr (not 100) |
