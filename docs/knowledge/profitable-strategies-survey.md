# Profitable Sports Betting — Strategy Survey

A landscape map of how betting is actually beaten, organized by edge source.
Companion to [anon-sports-consulting-insights.md](anon-sports-consulting-insights.md): that doc is one practitioner's framing of retail-sharp habits; this one is the wider menu, the math, and the realistic ceilings for each lane.

> **TL;DR for Betty.** Of the 9 strategy families below, Betty is structurally built to harvest 3 (soft-vs-Pinnacle value, arb, prediction-market arb) and is one step away from 2 more (boost EV, period/F5 markets). Player props, in-play, matched-betting promos, and syndicate-style modeling each require new pipelines — but the prop lane is the single largest unmined edge per dollar of dev time.

---

## Part 0 — What "profitable" actually requires

Two equations carry almost all of the math.

**1. The bet is +EV iff:** `your_probability × (decimal_odds − 1) > 1 − your_probability`
Rearranged: `edge = your_probability × decimal_odds − 1`. Anything else (CLV, hold, vig, line value) is a proxy for *whether you're correctly estimating `your_probability` better than the book is*.

**2. Long-run growth is bounded by Kelly:** for a single bet, `f* = (b·p − q) / b`, where `b = decimal_odds − 1`, `p = your_probability`, `q = 1 − p`. Bet `f*` of bankroll → maximal log-growth. Bet `2·f*` → ruin in expectation. Bet `0.5·f*` (half-Kelly) → ~75 % of the growth at ~25 % of the variance, which is why almost every pro uses fractional Kelly.

The catch: **`your_probability` is never observable**. The whole strategy stack below is just different ways to estimate it less wrongly than the book.

### Why CLV is the only honest scorecard

You can lose 50 bets in a row and still be a winning bettor; you can win 50 in a row and still be doomed. The only metric that converges before your bankroll does is **closing-line value** — the gap between the price you got and the consensus closing price (typically devigged Pinnacle, since Pinnacle accepts sharp action and moves on it).

> "Beating the closing line by +2 % on average, over 200+ bets, is the single most reliable signal that you have an edge." — generalised across every sharp-betting reference.

Betty currently doesn't snapshot closing lines per-bet — that's the #1 analytical gap (already itemised in the Anon insights doc, item #1).

### The vig math everyone glosses over

A −110/−110 two-way market = `1/1.909 + 1/1.909 = 1.0476` total implied probability → 4.76 % hold. That's the breakeven cost you must beat to net zero. Standard reference points:

| Market type | Typical hold | Implied breakeven win-rate at −110 / +100 |
|---|---|---|
| Major-league moneylines (sharp book) | 2–3 % | 50.0 % at +100 |
| Major-league moneylines (soft book) | 5–8 % | 52.4 % at −110 |
| Player props (soft book) | 6–10 % | 53.0–55.0 % |
| Same-game parlays | 15–30 % | varies, brutal |
| Live in-play | 6–12 % | varies |
| Polymarket (CLOB fee only) | 0–2 % | 50.5 % |
| Kalshi | 0.7–7 % depending on tier | 50.4–53 % |

The wider the hold, the larger the model error you need to overcome. **Sharp pros do not bet 15 %-hold markets**, full stop — your model would need to be that much better than the book's, and it isn't.

---

## Part 1 — The strategy families

Ordered roughly by edge size × scalability. Each family includes: how it works, who actually wins at it, what kills it, and where Betty stands.

### 1. Value betting vs. devigged sharp lines  *(Betty's core edge)*

**How.** Take a sharp source (Pinnacle for major sports, Circa for US sports, BookMaker for niches), strip the vig, treat the resulting probabilities as the "true" market. Any soft book offering odds whose implied probability is *lower* than the sharp-derived fair probability is a +EV bet. Edge size = `soft_odds / sharp_fair_odds − 1`.

**Why it works.** Soft books shade their lines toward popular sides to extract more vig from public lean (Cowboys, Lakers, brand names, public favorites in heavy news cycles). Sharp books don't, because they're price-takers from syndicate flow. The gap is the recreational/retail premium being arbitraged away in real time.

**Realistic edge.** Per-bet edges average 1–4 % for the bulk of opportunities, occasionally 8–15 % around fresh injury/lineup news that hasn't propagated. On a 1000-bet/year volume at 3 % avg edge with 0.5-Kelly, expected ROI ≈ 1.5 %/bet, with a 95 % confidence band that still includes losing months.

**What kills it.**
- Account limits — soft books cap winners (see strategy #8 below).
- Sharp source being wrong (Pinnacle is wrong sometimes; outliers via fuzzy match are wrong much more often → see scanner guards `MIN_VALID_PROB_SUM = 0.90`, `MAX_ODDS_RATIO = 1.35`).
- Cross-currency drift — Betty bets in SEK/USD/USDC; a 0.5 % FX move can wipe out a 2 % edge (see CLAUDE.md currencies section).

**Betty's position.** This is the spine of Betty (`scanner.scan_value()` over 40+ providers vs. devigged Pinnacle). Mature.

### 2. Pure arbitrage (true arb)  *(Betty does this)*

**How.** Cover all outcomes of a market across two or more books at prices whose implied probabilities sum to < 1. Lock guaranteed return = `1 / Σ(1/odds_i) − 1`.

**Realistic edge.** 0.5–3 % per arb in major markets, occasionally 5–8 % in niche/early lines. Liquidity is the constraint — arbs evaporate in seconds in major markets, persist longer in niche.

**What kills it.**
- Both books shifting between leg-1 and leg-2 placement (latency risk). Betty's `arb_runner.py` anchor-then-hedge sequencing mitigates but doesn't eliminate.
- Currency mismatch — `MIN(stake × odds)` in mixed currencies is meaningless. Betty's lesson from prior incidents (CLAUDE.md "first hypothesis when sizing looks off by 5-10× is mixed currencies").
- One book voiding ("palpable error" clauses) — books reserve the right to cancel obvious errors; the other leg becomes naked.
- Account limits trigger faster on arb than on value (books detect 2-way symmetry).

**Betty's position.** Implemented end-to-end (`arb_runner.py`, cluster dedup, currency-aware hedging — partially; see CLAUDE.md). The remaining levers are speed and the soft-book inventory.

### 3. Middling

**How.** Bet both sides of a spread/total at *different* numbers. Worst case: 1-1, lose the vig. Best case: result lands between → 2-0.

Example: spread moves from −3 to −4. You took +3, then take −4 elsewhere. Anything that lands on 3 wins both.

**Why it works.** Spreads and totals cluster around key numbers (NFL margins concentrate at 3, 7, 10, 14; baseball R lines at 1; basketball at integer multiples). When a line crosses one of these clusters, the middle is more probable than either side's implied vig would suggest.

**Realistic edge.** A middle with 5 % hit-rate on a −110/−110 pair returns: `0.05 × +1900 + 0.95 × −10 = +85 cents on every $10 of total stake ≈ 8.5 % ROI on stake. NFL key-number middles between 2.5 and 3.5, or 6.5 and 7.5, hit closer to 8–12 %, dwarfing the breakeven of ~5.3 % needed.

**What kills it.** Sample-size variance is huge; you need hundreds of middles to converge. Books increasingly price near key numbers asymmetrically (charging −120/+100 on the 3) to discourage exactly this.

**Betty's position.** Not implemented. Adding middling requires the scanner to detect spreads/totals straddling key numbers and prioritise those even when neither side has positive Pinnacle-derived edge. Tractable.

### 3b. Position-management hedging  *(open-position re-evaluation — distinct from §3)*

**How.** §3 looks for fresh middle pairs from scratch in the live market. §3b instead looks at the bets Betty **already has open** in `bets` with `result='pending'`, and asks for each one whether the *current* market offers a better-than-break-even action on the other side. Three sub-cases, in order of frequency:

1. **Line moved through a key number after we placed.** We bet `home -2.5`. Pinnacle and 3 soft books now post the same game at `away +3.5`. Wins-both window = margin exactly 3. The hedge wasn't visible at placement; it became visible after the line moved.
2. **Sharp odds drifted in our favour.** We bet `over 41` at +120 because Pinnacle's devigged fair was +135. Pinnacle now prices the same line at +160 (line agrees with us harder). On a soft book we can lay the under at decent juice to either lock guaranteed profit or trim variance without giving back the full edge.
3. **Sharp odds drifted against us (CLV inversion).** We bet `home ML` at 2.10; Pinnacle now thinks 2.40. The bet is now −EV in expectation. Hedging with the opposite side at any book where the combined return is > 0 transitions us from a still-pending −EV bet to a settled +small-cash position.

**Why it's distinct from §3 and §2.** §3 (middling scanner) starts from `current_market` and looks for straddle-pairs. §2 (true arb) starts from `current_market` and looks for sum-of-implied < 1. §3b starts from `bets WHERE result='pending'` and joins against current market — i.e. the search space is what we already own, not the universe. That changes the data flow (the open-position table drives, not the odds feed) and the sizing math (stake on side B is constrained by stake already locked on side A, not by Kelly on independent EV).

**Realistic edge.** Mostly variance reduction at zero or small EV cost, occasionally a free middle when a key number gets crossed post-placement. Concrete payoffs to expect:
- **Case 1 (post-placement middle):** 0.5–2 free middles per 100 NFL spread/total bets that originally straddled a key number (very rough — depends on how often we bet straddle-adjacent in the first place).
- **Case 2 (favourable drift):** rarely worth executing — locking 60% of an edge that's still alive throws away the other 40%. Usually correct to hold.
- **Case 3 (CLV inversion):** salvages ~20–40% of stake on bets that would otherwise lose in expectation. Most relevant after late injury news.

**What kills it.** Same things that kill §3, plus: (a) the open bet's provider may have already limited us, (b) the hedge sits on a *different* provider and currency, so the lock math is currency-sensitive (CLAUDE.md "first hypothesis when sizing looks off by 5-10× is mixed currencies"), (c) acting on case 2 too often is just churn that compounds vig.

**Betty's position.** Not implemented. Today Betty fires-and-forgets — once a bet lands in `pending`, no scanner re-evaluates it against the live market until settlement. Adding §3b means a periodic scan over `Bet WHERE result='pending' AND start_time > now` joined against current `Odds` rows. See sketch in [`docs/plans/2026-05-28-open-position-rehedge-scanner.md`](../plans/2026-05-28-open-position-rehedge-scanner.md). Tractable in 3-5 days; the primitives (`arb_runner` for placement, `arb_math.py` for sum-implied math, currency conversion in `bankroll_service`, `key_numbers.annotate` for spread/total context) already exist.

### 4. Boost / promo EV picking  *(Betty has the data, no scanner yet)*

**How.** Books regularly post "boosted" odds on selected markets — same outcome, higher payout, often capped at $25–$100. Most are −EV traps designed to feel sharp ("Lakers ML boosted to +180!" when fair is +250). A minority are genuine +EV when the boost overshoots fair value.

**Realistic edge.** 3–25 % on the bets that clear the threshold, but volume is constrained: each boost is one-shot per account per day. Volume × edge × max-stake-cap → maybe $20–$200/account/day at full saturation. Not life-changing, but stackable across many accounts and **uncorrelated with main value-bet variance**.

**What kills it.**
- Per-account stake caps.
- Account limits (boost-hammering is one of the fastest paths to a ban).
- Detection: boost markets often aren't in the standard API feed and need separate scraping.

**Betty's position.** `specials/boosts` pipeline exists with EV enrichment (CLAUDE.md "Specials / Odds Boosts Pipeline"). Already filters PROP_KEYWORDS and de-vigs Pinnacle. The remaining gap is the loop from `is_positive_ev=True` into the placement queue.

### 5. Prediction-market arbitrage (Polymarket, Kalshi)  *(Betty does this partially)*

**How.** Prediction markets are exchanges, not bookmakers. Polymarket charges ~0 % platform fee on majors; Kalshi tops out around 7 % maker fee tier (lower per volume). Sportsbooks charge 5–8 % vig. The structural fee gap = persistent 2–5 pp arbitrage surface against soft books, and ~0 % against Pinnacle for markets that overlap.

**Two distinct edges:**
- **Cross-market arb:** Polymarket says NO at 28 ¢, sportsbook offers YES at +280 (decimal 3.80 → implied 26.3 %). Combined: 28 ¢ + 26.3 ¢ = 54.3 ¢ → buy both, lock 84.5 % return on staked. (Academic research documented >$40 M extracted from Polymarket alone between Apr-2024 and Apr-2025.)
- **Slow-news edge:** Polymarket reacts to news in minutes, not seconds. After an NFL injury report drops, sportsbooks reprice in <5 s, Polymarket can take 10–30 minutes. Take the lagging side.

**What kills it.**
- Latency arms race — most large Polymarket arbs are now bot-captured within seconds.
- Liquidity holes — a 1 % printed arb often has only $200 of liquidity at the edge price.
- Settlement risk on Polymarket = oracle disputes, occasional 0.5 % haircut.
- Polymarket order placement frequently bypasses HTTP intercept (CLAUDE.md "Polymarket CLOB caveat"); the reliable capture is reactive sync via positions endpoint.

**Betty's position.** Polymarket and Kalshi both extracted; cross-arb scanner runs in `arb_runner.py`. The slow-news lane (window of 10–30 min after sportsbook reprice) is **not** systematically exploited because Betty's scheduler runs Polymarket on a 5-min cooldown — the same window the edge lives in. Tightening Polymarket cadence specifically around external news triggers (or any sportsbook >0.5 % move on a matched event) would convert this from incidental to systematic.

### 6. Player props (the largest unmined market)  *(Betty does NOT extract)*

**How.** Player props are individual-statistic markets (Tatum over/under 28.5 points, Mahomes over/under 295.5 passing yards, etc.). They are:
- The biggest US sportsbook revenue lane.
- The thinnest market — Pinnacle posts limited prop coverage with low limits; soft books quote 6–10 % hold but get the lines wrong far more often than they do on majors.
- The fastest to get you limited — every sharp prop bettor *will* be capped within weeks-to-months.

**Edge size.** Average +EV prop bet runs 4–8 % edge; specialist modelers (Sabersim for MLB, anybody serious about NBA usage models) report sustained 6–12 % ROI on selected sub-segments. **This is double Betty's typical value-bet edge.**

**What kills it.**
- Data: requires roster, lineup, minutes-projection, usage-rate, defense-vs-position, opponent pace. Pinnacle doesn't provide enough to devig; Circa props are sharper, and DraftKings/FanDuel actually have the sharpest prop pricing in 2025-2026 (per public market analysis).
- Limits hit faster than on sides/totals (books cap props specifically because they know they're getting them wrong).
- Correlation hell — a Tatum points-over correlates with a Celtics-team-total-over correlates with a Tatum-Brown-combined-points; sizing all three at independent Kelly massively overbets the underlying event.

**Betty's position.** Explicitly out of scope (`ALLOWED_MARKETS` = 1x2 / moneyline / spread / total only; CLAUDE.md "Extraction Scope"). Adding props is the **single largest expansion of EV surface available** but is also the largest engineering lift: extractor rewrites, a sharp-source substitute for Pinnacle on props (Circa is the candidate), and a prop-specific correlation-aware sizer.

### 7. Period / first-half / F5 markets  *(adjacent expansion)*

**How.** Same logic as 1x2/spread/total, applied to periods: MLB first 5 innings (F5), NFL first half, NBA first quarter, NHL first period. Variance is lower (you don't pay the closer/bullpen lottery on F5; you don't pay the garbage-time scoring on 1Q-NBA).

**Edge size.** Comparable to full-game (1–4 % per bet) but with **half the variance**, which means higher growth rate at the same Kelly fraction.

**What kills it.** Soft-book coverage is uneven — many books only post period markets late, with worse limits. Pinnacle does cover them (see `pinnacle-period-codes.md` for period 0 = full game, period 1 = 1H, period 2 = 2H, etc.).

**Betty's position.** Pinnacle period extraction exists but `ALLOWED_MARKETS` is full-game only. Whitelisting periods at Pinnacle + the subset of soft books that quote them = 1-2 day implementation, real EV uplift.

### 8. Matched betting / promo conversion

**How.** Use a sportsbook's promo (deposit match, "bet $X get $Y in bonus bets", risk-free bet) and structure offsetting bets at sharp books to lock in a portion of the promo as cash regardless of outcome.

- **Free bet (stake not returned):** standard conversion = ~70 % of face value (e.g., $100 free bet → $70 banked).
- **Risk-free bet:** typically converts at 60–75 %, depending on the rebate mechanism.
- **2-up / cash-out promos:** the Bet365 "2-up" (paid out if your team leads by 2 goals at any point) is still profitable in 2026 per public matched-betting writeups.

**Realistic profit.** $5K–$15K in the first year of US matched-betting per the public ProfitDuel/Outlier writeups; rapidly diminishing as accounts get gubbed (limited/closed). The post-promo phase is the +EV / arb work above.

**What kills it.** Aggressive gubbing — many US books gub within 5–10 promotions if you fire only on the boosted side. Mug betting (placing some −EV recreational bets) is the standard counter.

**Betty's position.** Out of scope of the current platform — promos aren't extracted, and Betty doesn't model human-driven sign-up flows. Could be a separate "promo harvester" workflow but conflicts with Betty's account-longevity goal.

### 9. Syndicate / modelling

**How.** Build a proprietary probability model that beats the closing line on at least one market segment. Stake into low-limit early lines (Pinnacle Tuesday-night openers, Circa overnights), pyramid into deeper liquidity as the market converges. Often executed via runners (people who bet on behalf of the syndicate at retail books to avoid limits).

**Realistic edge.** The best documented are ~3–5 % long-term ROI on enormous volume. Bottom line: turning a model into money requires (a) the model genuinely beats CLV, (b) you can place enough volume before limits, (c) bankroll deep enough to weather variance.

**What kills it.** Model decay (the market always converges to your edge), capacity limits (anything modelled well enough to win at $1 K stakes gets noticed at $20 K stakes), regulatory restrictions on syndicate accounts.

**Betty's position.** Betty isn't modelling — it's arbitraging Pinnacle's model. That's a fine business and easier to scale than syndicate modelling, but it caps Betty's ROI at "what Pinnacle's model knows" minus "what fast bots already extracted." A custom prop model (see #6) would be Betty's only realistic path into a true modelling lane.

---

## Part 2 — The math nobody wants to redo

### Kelly under simultaneous bets

Standard Kelly assumes one bet at a time. In practice, Betty places 5–30 simultaneous bets in flight. Three realistic regimes:

1. **Uncorrelated bets** (different sports, different events). Independent Kelly per bet, but total exposure capped at ~20–25 % of bankroll to leave room for variance pile-ups. Mathematically: simultaneous Kelly is well-approximated by independent Kelly + a hard cap.
2. **Correlated bets** (same event different markets, or two legs of an arb). Treat as a single combined position. For an arb specifically, the "edge" is the arb % and the "Kelly" is essentially unbounded by EV — it's bounded by execution risk, currency drift, and book voiding.
3. **Anti-correlated bets** (Betty's hedge legs). Same event different sides at different books. The combined position is delta-neutral, so Kelly doesn't apply — size it by the locked return, not by the variance.

**Practical heuristic Betty uses today:** half-Kelly per opportunity × a 25 % bankroll cap on aggregate exposure. The cap is doing more work than people realise — when 30 opportunities all show 3 % edge, full Kelly says 30 × ~6 % = 180 % bankroll. The cap is what keeps you solvent.

### Variance: when "+EV but I lost" is actually expected

For 1000 bets at 3 % avg edge, fair-odds ~2.0 (50-50 outcome), half-Kelly stakes of 1.5 % bankroll:

- **Expected profit:** +45 % bankroll/year
- **σ of profit:** ~30 %
- **Probability of a losing year:** ~7 %
- **Probability of a 20 %+ drawdown at some point during the year:** ~50 %

That 50 % drawdown probability is the brutal truth — most pros who quit do so during a drawdown they were statistically guaranteed to see. A bankroll log + a no-touching rule during drawdowns is the discipline answer. The math doesn't change either way.

### When your edge is fake (the hardest math)

You think you have a 3 % edge. You bet 500 bets and end up −1 %. Two possibilities:

(a) Your edge is real; you got unlucky. P(−1 % or worse over 500 bets at 3 % true edge) ≈ 15 %.
(b) Your edge isn't real; you got lucky to lose only −1 %. P(this outcome under 0 % true edge) ≈ 20 %.

Bayesian update: even after 500 bets of disappointment, you can't reject the edge hypothesis. You need ~2000 bets per `(sport × market × edge-bucket)` cell to distinguish a real 3 % edge from luck with >90 % confidence. **That's the cell-size requirement for bucket analysis** (Anon insights item #3) and is the reason Betty's bucket P/L slicing matters: it forces honest per-cell sample-size accounting instead of one pooled "we're +X % overall" number that hides which cells are leaking.

---

## Part 3 — Account longevity (the meta-game)

Almost every profitable bettor's largest cost isn't variance — it's getting limited or banned. The standard playbook:

| Tactic | Why | Cost |
|---|---|---|
| **Round bet sizes** ($25, $50, $100, $250 — never $147.32) | Sharp Kelly sizing screams "sharp." Round = recreational. | <5 % EV (rounding to the nearest $25) |
| **Mug bets** (recreational bets between value bets) | Books look at *bet pattern entropy*. All-sharp = flag. 20 % recreational = look human. | Direct −EV on the mug bets; pros budget this as a cost |
| **Avoid props in the first 20 bets** | Books monitor early activity hardest; props are the highest-detection market | Defer prop strategy by 1-2 weeks |
| **Don't withdraw fast** | Quick deposit→bet→withdraw triggers fraud screens | Liquidity tied up for weeks |
| **Limit CLV exposure** (don't always bet 3+ % CLV) | CLV is the strongest signal books use to identify sharps | Lose ~10-20 % of best plays |
| **Spread across many accounts** | Per-account limits are the constraint; many small accounts > one big account | High account-onboarding cost; some jurisdictions ban multi-accounting |
| **Stay off boost-only patterns** | Booking only the +EV boosts is the fastest gub signal | Lose some +EV inventory |

**Unlimited providers don't need this dance.** Pinnacle (by design), Cloudbet, Polymarket, Kalshi don't limit winners — they accept the action and reprice. CLAUDE.md already flags these as "uncapped" — they're the right place to bet your largest stakes. The 10/day/soft-provider cap encoded in `play_loop.py` is the longevity guardrail for the books that do limit.

---

## Part 4 — In-play / live betting

Betty doesn't extract live (CLAUDE.md "Live events: Skipped entirely - only pre-match odds"). This is the right call for the platform's current edge model — but the live lane is where the most extreme edges still exist, with the most extreme operational requirements.

**The live edge breakdown:**

| Edge | Window | Operational cost |
|---|---|---|
| Stream/data latency arb | 0.5–5 s between fast book and slow book | Sub-second placement infrastructure |
| Score-feed lag (slow book hasn't priced a score yet) | 5–30 s | Direct stadium-feed access (the syndicate game) |
| Tennis between-point gaps | 1–10 s | Specialist single-sport |
| Overreaction fade (book overcorrects after big play) | 30 s – 5 min | Pre-built per-sport model |
| Live-correlated hedging (cash out + re-bet) | minutes | Per-book bonus / cash-out edge case |

The first two are syndicate games — they require infrastructure Betty doesn't have and shouldn't try to build. The overreaction-fade and tennis between-point lanes are tractable but each requires a dedicated workflow with a dedicated sport model. Punt on live until value/arb runway is exhausted.

---

## Part 5 — Betty's current build state vs. remaining gaps

The following items from the strategy landscape were initially listed as "gaps" but a code audit (May 2026) found most already shipped. Documenting reality here so future readers don't repeat the mistake.

### Already shipped (do not re-propose)

| Item | Where it lives |
|---|---|
| **CLV tracking** (Pinnacle cross-market + Polymarket same-market) | `bets.closing_odds` / `clv_pct` / `provider_closing_odds` / `provider_clv_pct` columns; `BetService._calculate_clv()` and `snapshot_closing_odds()`; snapshot runs every analyzer pass (`pipeline/analyzer.py`). Pinnacle bets use consensus soft books as the benchmark; soft bets use Pinnacle. `clv_confirmed` flag set when snapshot occurred within 12h of placement. |
| **Bucket P/L (sport × market) with auto Kelly throttle** | `bankroll/bucket_confidence.py` — computes mean-CLV per bucket on a 90-day window, maps to Kelly multiplier ∈ {0, 0.5, 0.75, 1.0} keyed on bucket size + sign of mean CLV. **Gated by `BUCKET_CONFIDENCE_ENABLED` env var (default off).** Inspection-ready before flipping on. |
| **Boost / promo EV pipeline** | `analysis/ev_enrichment.py` runs at scrape time, populates `specials` table with `edge_pct` / `is_positive_ev`. `ml/models/boost_calibrator.py` adds calibration. Surfaced via `/api/specials`. |
| **Drawdown circuit breaker** | `bankroll/drawdown_guard.py` |
| **Steam-move detector** | `analysis/steam_detector.py`, gated by `STEAM_DETECTOR_ENABLED` |
| **Key-number annotation (NFL spread/total)** | `analysis/key_numbers.py` — annotates proximity to 3/6/7/10/14 (spreads) and clusters around 37/41/44/47 (totals). Currently informational, **not used as stake math** — see "Real gaps" below. |
| **Period-scope vocabulary + per-event scope enforcement** | `constants.py` defines `VALID_SCOPES = {ft, reg, 1h, 2h, q1..q4, p1..p3, set_1..5, map_1..5}`. Scanner enforces scope-matching to prevent cross-scope phantom arbs (the IIHF regulation-vs-OT bug). |
| **Bankroll-basis discipline (unlimited bookmakers only)** | `UNLIMITED_PROVIDERS = {pinnacle, cloudbet, kalshi, polymarket}` is the Kelly stake basis. Soft balances are holding pens — they don't expand the bankroll, they get arbed out. |
| **Platform deduplication** | `PLATFORM_GROUPS` collapses Kambi tenants, Altenar tenants, Gecko/OBG, Spectate, ComeOn group into one canonical extraction each, fanning out opportunities to members. |

### Real remaining gaps (audited and confirmed missing)

Ordered by `expected EV uplift × prob of shipping`:

| # | Gap | Why it's a real gap | Cost | Risk |
|---|---|---|---|---|
| 1 | **Turn on `BUCKET_CONFIDENCE_ENABLED`** after data inspection | Bucket throttle exists but is dark. First step: query the current bucket table, see which (sport, market) cells have ≥100 settled bets with computed CLV, decide whether the multiplier schedule is sane for our data. Then flip on. | 0.5 day | Low — strictly deflationary by design |
| 2 | **Period market scanning** (1H/Q1/Q2/P1/P2 + MLB F5) | Infrastructure quarter-built and progressing. Detailed audit in [pinnacle-period-codes.md](pinnacle-period-codes.md). Pinnacle's own extractor was silently dropping every non-(0/6/esports-1-5) period. **PR 1 shipped (2026-05-26):** `f5`/`f3` added to `VALID_SCOPES`, Pinnacle MLB period 1 → `scope="f5"`, period 3 → `scope="f3"`. PR 2 (pending): scanner per-scope support — `SPORT_CANONICAL_SCOPE` becomes a set per sport (`baseball` → `{ft, f5, f3}`), scanner iterates scopes instead of dropping non-canonical. PR 3 (pending): wire Kambi MLB F5 so the scanner has soft-book comparison surface. | 2-3 days remaining | Low — per-sport gating preserves IIHF-style cross-scope safety. |
| 3 | **Polymarket event-driven repull** | Polymarket is on a 5-min interval cooldown. The slow-news edge (sportsbook adjusts in seconds, Polymarket lags 10-30 min) is the highest per-bet EV lane on Polymarket — and it lives entirely inside Betty's current 5-min refresh window. Trigger: when Pinnacle moves >X% on a matched event, kick off a Polymarket refresh for that event within 60s. | 1-2 days | Medium — needs care not to thrash Polymarket's rate limits or our local scheduler. |
| 4 | **Middling scanner** | `key_numbers.py` annotates proximity but doesn't actively look for middles. A middling scan needs: pairs of spread/total quotes across providers that straddle a key number, expected-hit-rate estimate from public NFL margin data, stake-pair sizer that hits the middle at break-even-or-better on the wings. | 3-5 days | Medium — middle hit-rate estimates from public margin data are noisy; expected ROI is real but variance is high (small-N until hundreds placed). |
| 4b | **Open-position re-hedge scanner** (§3b) | Today Betty fires-and-forgets every value/arb bet. No scanner re-evaluates `bets WHERE result='pending'` against the live market, so post-placement middles, favourable-drift locks, and CLV-inversion salvage all get missed. Distinct from #4 because the search starts from already-placed bets, not fresh market scans. Reuses `arb_math`, `key_numbers.annotate`, `arb_runner` for the second-leg placement. | 3-5 days | Medium — case 2 (favourable drift) is easy to over-execute and churn vig; needs a strict gating rule to act only on key-number crossings (case 1) and CLV inversions ≥ X pp (case 3). |
| 5 | **Player props pipeline** | Out of `ALLOWED_MARKETS`. Largest unmined per-bet EV (~2× current value lane) but requires: prop extractor rewrites, a sharp prop reference (Pinnacle is thin → Circa or sportsbook consensus), prop-aware Kelly that handles within-game correlation (Tatum points + Celtics total are correlated), and acceptance that prop accounts get gubbed faster. | 4-8 weeks | High — engineering lift is real, account-longevity cost real, but the EV ceiling is genuinely higher than anything else on this list. |
| 6 | **Live betting (tennis between-point only)** | Out of scope. Single most tractable live lane (1-10s windows) but requires dedicated sport-specific workflow and sub-second placement infrastructure Betty doesn't have. | months | Highest — defer until 1-5 fully exploited. |

### Honest sequencing

The lowest-risk, highest-information step is **#1 (turn on bucket confidence after inspection)**: it costs almost nothing, it pressure-tests whether the existing CLV pipeline has enough bucket coverage to feed a Kelly throttle, and if it does, it locks in a permanent risk discount on segments we're systematically wrong about — without changing the EV-generating side of the pipeline at all.

After #1, **#2 (period scanning)** is the largest additive EV per dev-day, because the extraction work is already done — only the scanner gate has to change.

#3-#4 are real but each independently moves the bankroll less than #1+#2 combined.

#5-#6 are next-quarter (or next-year) decisions that depend on whether the bankroll has grown enough to justify the engineering, *and* on what #1-#4 reveal about which sport/market cells are saturated vs. underexploited.

---

## Part 6 — Bet-by-bet checklist (the model on a postcard)

For every bet Betty considers placing:

1. Does it pass the sharp-line filter? (Pinnacle prob × odds − 1 > threshold)
2. Does the market sum match (`MIN_VALID_PROB_SUM`)?
3. Does the odds ratio sanity-check (`MAX_ODDS_RATIO`)?
4. Are all relevant currencies converted to one base?
5. Is the provider in the daily-cap budget (10/day for soft, uncapped for sharp)?
6. Does cluster-dedup pass (no sibling bet already placed)?
7. Is the live price within 1 tick of the price the scanner saw? (auto-skip otherwise)
8. Is the planned stake within the account's longevity envelope (round sizes, mug-bet ratio)?
9. Has settlement on prior pending bets at this provider completed?
10. Snapshot edge_pct + Pinnacle prob + bookcount **before** placement (for CLV reconciliation later)

Most of 1-9 are already implemented in `play_loop.py`. #10 is the CLV gap and the single most leveraged change.

---

## Sources (external, May 2026)

- [Closing Line Value — VSiN](https://vsin.com/how-to-bet/the-importance-of-closing-line-value/)
- [How to Track CLV — Pikkit](https://pikkit.com/blog/how-to-track-closing-line-value-clv-in-sports-betting)
- [Sports Betting Syndicates — Casino.org](https://www.casino.org/blog/inside-sports-betting-syndicates/)
- [Are Betting Syndicates Beatable — SportsTrade](https://www.sportstrade.io/blog-detail/362/are-betting-syndicates-beatable-understanding-their-market-influence.html)
- [Pinnacle as Sharp Reference — Bet Hero](https://betherosports.com/blog/how-to-use-pinnacle)
- [Why Sportsbooks Limit Prop Bettors — BetPredictionSite](https://betpredictionsite.com/blog/why-sportsbooks-limit-prop-bettors/)
- [Sharp Sportsbooks — PickTheOdds](https://picktheodds.app/en/blog/sharp-sportsbooks-what-they-are-and-how-to-use-them-to-find-edges)
- [Promo / Bonus Conversion — XCLSV](https://xclsvmedia.com/how-to-convert-bonus-bets-in-2026-complete-guide-to-maximizing-free-bet-value/)
- [Matched Betting Without Free Bets — Caan Berry](https://caanberry.com/matched-betting-without-free-bets/)
- [Prediction Markets vs Sportsbooks: 4.5% Vig Gap — Tech-Insider](https://tech-insider.org/prediction-markets/prediction-markets-vs-sportsbooks/)
- [How Polymarket / Kalshi Arbitrage Works — Trevor Lasn](https://www.trevorlasn.com/blog/how-prediction-market-polymarket-kalshi-arbitrage-works)
- [Polymarket Sports Arbitrage Bot (GitHub)](https://github.com/CrewSX/Polymarket-Sports-Arbitrage-Bot)
- [Avoid Sportsbook Limits — XCLSV](https://xclsvmedia.com/how-to-avoid-getting-limited-by-sportsbooks-in-2026-complete-guide-for-profitable-bettors/)
- [Why Sportsbooks Limit — Outlier](https://outlier.bet/sports-betting-strategy/positive-ev-betting/why-sportsbooks-limit-your-bets/)
- [Don't Get Limited — DarkHorse](https://about.darkhorseodds.com/guides/dont-get-limited)
- [Avoid Bookmaker Limitations — RebelBetting](https://www.rebelbetting.com/blog/how-to-avoid-bookmaker-limitations)
- [Middling Guide — OddsJam](https://oddsjam.com/betting-education/middles)
- [Arbitrage & Middling — TheSpread](https://www.thespread.com/betting-guides/arbitrage-middling-explained/)
- [Kelly Criterion for Sports — Market Math](https://marketmath.io/blog/kelly-criterion-guide)
- [Multiple Simultaneous Bets Kelly — Vegapit](https://vegapit.com/article/numerically_solve_kelly_criterion_multiple_simultaneous_bets/)
- [Live Betting Latency — Tony's Picks](https://www.tonyspicks.com/2026/05/12/live-betting-latency-which-sportsbooks-update-fastest-during-play/)
- [Live Betting Strategy — OddsIndex](https://oddsindex.com/guides/live-betting-strategy-guide)
- [Sports Betting Profitability Stats — SportBot AI](https://www.sportbotai.com/stats/sports-betting-profitability)
