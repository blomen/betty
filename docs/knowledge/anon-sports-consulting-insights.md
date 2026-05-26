# @AnonSportsConsulting — Distilled Insights & Arnold Improvement Map

Source: 18 videos on [https://www.youtube.com/@AnonSportsConsulting](https://www.youtube.com/@AnonSportsConsulting)
Raw transcripts: [anon-sports-consulting-raw.md](anon-sports-consulting-raw.md)

> **Caveat first.** A large fraction of every video is sales pitch ("DM me 'lock'", "join my VIP", "I went 89-29 last year"). His public‑bet‑percentage and reverse‑line‑movement framing is solid retail content — none of it is alpha that an extraction platform like Arnold doesn't already capture mechanically. The value of mining this channel is **vocabulary, framing, and a checklist of edges retail‑sharp bettors actually use**, which we can map to features we already have (mostly) or are missing.

---

## Part 1 — The Distilled Concepts (everything he says, deduplicated)

### A. Market structure (how books actually work)

| Concept | Anon's framing | What it really means |
|---|---|---|
| Vig / juice | "Built-in edge, -110 means $10 spread either side" | Bookmaker margin. On 2-way -110/-110 → ~4.5% hold |
| Books balance action, don't predict | They want 50/50 money so they collect the vig risk-free | True for retail books. Sharp books (Pinnacle, Circa) post-and-adjust based on sharp flow, not retail balance |
| Lines are about predicting *the bettor*, not the outcome | "Masters of human psychology" | Retail books shade by `bet_pct - true_pct` to extract more vig from public lean. Sharp books don't |
| Line shading on name brands | Cowboys/Lakers/Yankees always juiced | Brand premium — public over-bets favorites and brands; soft books inflate them ~0.5–1pt |
| Opening line vs closing line | Sharps hit weak openers at low limits; line "refined" by close | Opening lines are softer; closing line approximates the consensus fair price after sharp flow |
| Trap games are myth | Books don't trap — sharp money set the suspicious line | Correct. Inefficient lines exist but they're sharp-driven, not deliberate retail traps |

### B. The four edge signals he repeats

1. **Value betting** — bet when your model probability > implied probability of the line. *"You don't have to win 70%, you just need to consistently bet where odds are in your favor."*
2. **Closing Line Value (CLV)** — *"If you consistently beat the closing number, you're going to be on the right side long term."* This is the single most-cited pro metric across all 18 videos.
3. **Reverse Line Movement (RLM)** — line moves against the public bet% (75% on Lakers -4 but line drops to -3.5 → sharp money on the other side).
4. **Steam moves** — sudden uniform line move across *multiple* books simultaneously → syndicate-level money just hit.

### C. Public-vs-sharp signals (he gives 3 specific reads)

| Pattern | Public/sharp split | Action |
|---|---|---|
| 80% bets / 50% money on Team A | Lots of tickets, little cash → public hammering, sharps quiet | Likely shaded line; lean fade |
| 67% bets / 90% money on Team A | Public + sharp aligned | High-confidence one-side bet |
| 20% bets / 50% money on Team B | Few tickets, big cash | Whale/syndicate on B; follow |

### D. Bankroll & discipline (most repeated topic)

- **Unit system:** 1 unit = 1–2% of bankroll. Cap at 5% on highest-conviction.
- **Flat-bet most plays.** Vary by conviction within a narrow band, not by mood.
- **Monthly bankroll reset** to lock in compounding without emotion contaminating the schedule.
- **No-action > forced action.** Cap at 2–3 plays/day max for him personally.
- **Parlays = lottery tickets** — each leg multiplies the house edge.
- **Loss limits** (daily/weekly) with mandatory 24h break after big-bet loss.
- **Tilt patterns to recognize:**
  - Chasing losses (forcing afternoon plays after morning miss).
  - FOMO (jumping on social-media pick against your own read).
  - **Winning tilt** (going 5–0 → doubling unit size next day).
- **Treat each bet independently.** Yesterday's outcome has no bearing on today's EV.

### E. Sport-specific edges

#### NFL
- **Key spread numbers: 3, 7, 6, 10** (because of FG=3, TD=6, TD+PAT=7). Books charge -120/-130 to stay on the 3, willing to pay -105 to move off it. *Moving off -3 to -3.5 is "massive value", not minor.*
- **Key total numbers: 37, 41, 44, 47** (common landing spots).
- **Injury depth chart matters beyond QB:** missing left tackle → pass pressure spike, missing CB1 → WR2 props pop, RB1 out → backup RB rushing yards before line catches up (the "market lag" on Friday → Sunday).
- **Travel/rest:** west→east 1pm ET = historically worse; teams off Monday-night or London game = fatigued; post-bye = rested advantage.
- **Weather:** open-air + 25mph wind = unders; domes = overs.
- **Game script correlation:** if you like the under → RB carry overs; if you like a blowout → backup pass-catcher garbage-time overs.

#### MLB
- **Bullpen fatigue is half the game.** Track bullpen IP over last 5 days, closer overused, setup man resting.
- **First-5-innings (F5) bets** bypass bullpen variance — pure starting-pitcher matchup.
- **Travel/road-trip leg:** fade teams on last leg of a long road trip, especially cross-timezone.
- **Park factors:** Coors / Great American / Fenway are radically different from Tropicana / Oracle.
- **Weather:** wind out + 10mph = overs, high humidity = ball carries, cold = dead bats.
- **Series progression:** 3rd game of a series with tired bullpen → over-leaning.
- **Pitcher stats to watch:** recent form, ERA, WHIP. Hot/cold streaks have inertia.
- **Run-line (-1.5 / +1.5):** baseball's spread equivalent.

#### NBA (mentioned briefly)
- Back-to-backs → tired legs → defensive teams might over (tired feet on defense), shooting teams under.
- Some referees lean home-team or over-totals consistently.

### F. Workflow / habits checklist (his "6 habits")

1. Strict bankroll management, 1–5% per play.
2. Specialize in one sport at a time before adding more.
3. Track every bet — date, sport, teams, type, odds, stake, P/L, **and the rationale**.
4. Line shop across many books — `-105 vs -110` saves you ~1.16% on breakeven.
5. Emotional control / tilt management.
6. Continuous learning — review every loss weekly: bad analysis, missed info, or variance?

---

## Part 2 — Mapping to Arnold (what we already do, what we're missing)

### Already strong (no change needed)

| Anon concept | Arnold's implementation |
|---|---|
| Value betting | Core of the system — `scanner.scan_value()` vs devigged Pinnacle |
| Devig fair odds | `analysis/value.py` — multiplicative devig from Pinnacle |
| Line shopping across many books | 40+ providers extracted, scanner finds best price per outcome |
| Flat units / no emotion | Automated Kelly-tiered sizing via `bankroll/`; humans don't choose stake |
| Track every bet | `bets` table, Stats tab |
| Multi-currency book reality | `money.convert` / `to_sek` (per CLAUDE.md) |
| Public-bias inefficiency | Implicit — soft books shading toward Cowboys/Lakers IS the edge we harvest |
| Pinnacle as sharp baseline | Whole pipeline. Pinnacle is the only sharp source by design |

### Gaps where Anon's framing points at real, implementable improvements

Ordered by ROI for Arnold specifically:

#### 1. **Closing Line Value (CLV) tracking — highest ROI gap**

He cites CLV as *the* long-term EV indicator. Arnold doesn't compute it.

**Proposal:**
- At extraction time T_kickoff − 60s (and one final pull at T_kickoff − 5s), snapshot the closing price for every event we have a bet on, for both the provider we bet at AND Pinnacle.
- New columns on `bets`: `closing_provider_odds`, `closing_pinnacle_odds`, `closing_fair_odds`, `clv_pct = (placed_odds / closing_fair_odds - 1) * 100`.
- Aggregate CLV by sport / provider / market / edge-bucket in Stats tab. Negative CLV on a sub-bucket = leak.

This is the single biggest analytical upgrade. We already have all the raw data; we just don't snapshot at close.

#### 2. **Steam-move detector**

When 3+ providers move >1 tick on the same outcome inside a short window, that's a syndicate-style signal. Arnold extracts from 40+ books — we're uniquely positioned to detect this *before* the slow books finish adjusting.

**Proposal:**
- New analyzer that watches `odds` table for `(event_id, market, outcome)` cross-provider velocity.
- Threshold: ≥3 books move in same direction within 5 minutes by ≥1.5% in implied probability.
- Emit a `steam_move` signal that:
  - Suppresses placement on the *trailing* side (we'd be catching the move late).
  - Boosts confidence on the *leading* side at any soft book that hasn't moved yet — that lag is pure value.

#### 3. **Bet-bucket P/L slicing in Stats tab**

His example: he tracked NFL totals +15u, NFL spreads +1.7u, parlays -2.3u → cut parlays, ROI exploded. We currently track totals at the bet level but probably not slice by `(sport, market, edge_bucket, provider)`. Adding this lets us answer "which segments of our pipeline actually print money."

**Proposal:**
- Stats tab gains a "Bucket Analysis" view: pivot by sport × market × provider × edge_bucket → ROI, hit-rate, units, CLV, count.
- Sort by negative ROI to find leaks. Sort by negative CLV to find systematic mispricing of our model (vs. simply bad variance).

#### 4. **Market-lag exploit: F5 / first-half / period markets**

He specifically calls out F5 (first-5-innings) bets as lower-variance because they cut bullpen volatility. Pinnacle ships F5 lines. We currently only extract full-game 1x2/spread/total. F5 / 1H / period markets are a parallel product line where the same edge logic applies with lower variance.

**Proposal:**
- Add F5 (MLB), 1H (NFL/NBA), first-period (NHL) to `ALLOWED_MARKETS` for sharp sources only.
- Run scanner against soft books that also ship these markets.
- Treat them as their own edge bucket (different variance profile → different Kelly multiplier).

#### 5. **Key-number awareness for NFL spreads / totals**

If a soft book has -3 and Pinnacle has -3 with same juice, our scanner sees no edge. But if soft has -2.5 and Pinnacle has -3, we have an edge much bigger than the price-implied edge because **3 is a key number** — getting `-2.5` is buying a half-point through the highest-frequency margin in the NFL.

**Proposal:**
- Add `key_number_bonus` to NFL spread scanner: when `(soft_spread, pinnacle_spread)` straddles a key number (3, 7, 6, 10 for spreads; 37/41/44/47 for totals), boost the edge score (or apply a probability bump — every half-point through key numbers shifts cover rate by ~1.5–3% depending on the number).
- Implement as a lookup table from historical NFL margin distributions (publicly known data).

#### 6. **Opening-line freshness signal**

His framing: "sharp money hits weak openers at low limits." Arnold has extraction tiers but no concept of "this provider just opened this market in the last N minutes." We could prioritize newly-published markets at sharp books — those are most likely to be off.

**Proposal:**
- Add `first_seen_at` to `odds` rows.
- When `first_seen_at` is within N minutes (start with 30) AND the soft has not yet posted that market, flag the Pinnacle line as "fresh-sharp" — useful when we eventually post-bet against soft books that lag.

#### 7. **Specialization auto-detection (sport-level Kelly weighting)**

He says: "Specialize in one sport." We bet 40+ providers × many sports indiscriminately. Some of those sport/provider combinations almost certainly print money; others might be flat or negative. Currently we weight Kelly only by edge%, not by historical realized ROI per `(sport, market)` bucket.

**Proposal:**
- Quarterly recompute realized ROI per `(sport, market)` over last 90 days.
- Apply a "confidence multiplier" to Kelly stake: bet full Kelly on buckets with >+3% ROI over ≥200 bets; half-Kelly on neutral; zero on negative until 200-bet rolling window goes positive again.
- This is sport-specialization without the human bias of picking one — driven by data.

#### 8. **Tilt guardrails for the unlimited-cluster providers (Pinnacle / Cloudbet / Kalshi / Polymarket)**

Soft books cap us automatically (10/day per CLAUDE.md). Unlimited providers don't. Anon's loss-limit / 24h-break advice applies more to humans than to Arnold, but the system equivalent is **circuit-breaker on drawdown.**

**Proposal:**
- Per-provider rolling P/L circuit: if 7-day P/L breaches `-N × bankroll_pct`, pause new placements on that provider for 24h.
- Sanity check: this is *not* tilt — it's a guard against a systematic model drift on a specific book (a provider changed its pricing model overnight and we're flat-out wrong about its lines).

#### 9. **Per-bet rationale snapshot**

He keeps a betting journal with the *why*. We log the bet but probably don't capture the full state-at-placement (Pinnacle implied prob, soft implied prob, devigged fair prob, sharp_book_count, recent price velocity). With that snapshot, the retrospective question "why did this bet lose — bad edge, variance, line moved against us, or model bug?" becomes answerable.

**Proposal:**
- `bets.placement_snapshot` JSONB: fair_odds, raw_pinnacle_odds, devig_method, total_books_seen, line_velocity_5min, edge_pct, recent_clv_for_this_bucket.
- Stats tab uses this for the bucket analysis in #3.

---

### What we explicitly do NOT need from him

| He emphasizes | Why Arnold doesn't need it |
|---|---|
| Public bet-percentage data (Action Network, VSIN) | We harvest 40+ books' actual prices — that's the *result* of the bet flow, not noisy public-bet share |
| Weather / lineups / bullpen modeling | Pinnacle prices these in. We arbitrage Pinnacle vs soft, so we inherit the model for free |
| Recency-bias / brand-shading fades | Implicit in soft-vs-Pinnacle gaps — that *is* the brand premium being arbed |
| "Specialize in one sport" (manual) | Replaced by #7 above — data-driven sport weighting |
| Manual sizing by conviction | Kelly sizing already does this from edge% directly |

---

## Part 3 — Suggested implementation order

If we picked **one** thing to add, it would be **#1 CLV tracking** — it's the diagnostic backbone for evaluating everything else and the data we need is already in the extraction stream. Without CLV, every other improvement is harder to validate.

Recommended sequence:
1. **CLV tracking** (close-line snapshot, columns on `bets`, Stats panel) — diagnostic
2. **Bucket P/L slicing** in Stats — find the leaks
3. **Per-bet placement snapshot** — enables forensic post-mortems
4. **Specialization auto-weighting** — close the loop: leaks found in #2 + #3 automatically de-emphasize themselves
5. **Steam-move detector** — additive edge signal
6. **F5 / period markets** — new product line, lower variance
7. **Key-number bonus on NFL** — modest, sport-specific
8. **Drawdown circuit-breaker** — safety, not edge

Each of 1–4 is independently a 1–3 day implementation if scoped tightly. 5–8 are bigger.
