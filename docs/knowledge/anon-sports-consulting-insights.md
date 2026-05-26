# @AnonSportsConsulting — Distilled Insights & Betty Improvement Map

Source: 18 videos on [https://www.youtube.com/@AnonSportsConsulting](https://www.youtube.com/@AnonSportsConsulting)
Raw transcripts: [anon-sports-consulting-raw.md](anon-sports-consulting-raw.md)

> **Caveat first.** A large fraction of every video is sales pitch ("DM me 'lock'", "join my VIP", "I went 89-29 last year"). His public‑bet‑percentage and reverse‑line‑movement framing is solid retail content — none of it is alpha that an extraction platform like Betty doesn't already capture mechanically. The value of mining this channel is **vocabulary, framing, and a checklist of edges retail‑sharp bettors actually use**, which we can map to features we already have (mostly) or are missing.

> **Implementation status (audited 2026-05-26):** This doc was written before most of the proposals below were implemented. Of the 9 proposals in Part 2 below, 5 are fully shipped, 3 are partial/in-progress, 1 is still genuinely missing. See [profitable-strategies-survey.md](profitable-strategies-survey.md) Part 5 for the current map. Sections E and F (sport-specific edges, habits) are *informational vocabulary* — Betty consumes Pinnacle's pricing model rather than re-deriving sport-specific edges, so the bookmaker effectively does the modelling for us.

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

## Part 2 — Mapping to Betty (what we already do, what we're missing)

### Already strong (no change needed)

| Anon concept | Betty's implementation |
|---|---|
| Value betting | Core of the system — `scanner.scan_value()` vs devigged Pinnacle |
| Devig fair odds | `analysis/value.py` + `analysis/devig.py` — multiplicative devig from Pinnacle |
| Line shopping across many books | 40+ providers extracted, scanner finds best price per outcome |
| Flat units / no emotion | Automated Kelly-tiered sizing via `bankroll/`; humans don't choose stake |
| Track every bet | `bets` table, Stats tab, postmortem analysis on every settlement |
| Multi-currency book reality | Inline conversion helpers in `services/bankroll/repositories` (see CLAUDE.md currencies section) |
| Public-bias inefficiency | Implicit — soft books shading toward Cowboys/Lakers IS the edge we harvest |
| Pinnacle as sharp baseline | Whole pipeline. Pinnacle is the only sharp source by design (`SHARP_PROVIDERS`) |

### Gaps where Anon's framing points at real, implementable improvements

Originally ordered by ROI; each item now annotated with current implementation status (audited 2026-05-26).

#### 1. **Closing Line Value (CLV) tracking — highest ROI gap**  ✅ SHIPPED

He cites CLV as *the* long-term EV indicator.

**Original proposal:** snapshot Pinnacle closing odds for every bet, expose `clv_pct` on `bets`, aggregate by bucket.

**As shipped:** `bets.closing_odds` + `bets.clv_pct` (Pinnacle cross-market) + `bets.provider_closing_odds` + `bets.provider_clv_pct` (Polymarket same-market). `BetService.snapshot_closing_odds()` runs every analyzer pass ([pipeline/analyzer.py:109](../../backend/src/pipeline/analyzer.py#L109)) before odds-cleanup deletes stale rows; benchmark logic uses consensus-soft for Pinnacle bets, Pinnacle for soft bets, and same-market Polymarket for poly bets. `clv_confirmed` flag set when snapshot occurred within 12h of placement. Surfaced in Stats, postmortem, ML edge_quality feature.

#### 2. **Steam-move detector**  ✅ SHIPPED

**As shipped:** [analysis/steam_detector.py](../../backend/src/analysis/steam_detector.py), gated by `STEAM_DETECTOR_ENABLED`. Tracks `(event_id, market, outcome, point, scope)` cross-provider velocity from `odds_movements`. Surfaces a `steam_sig` joined into value-bet output in [scanner.py:288](../../backend/src/analysis/scanner.py#L288).

#### 3. **Bet-bucket P/L slicing in Stats tab**  ✅ SHIPPED (gated off)

**As shipped:** [bankroll/bucket_confidence.py](../../backend/src/bankroll/bucket_confidence.py) computes per-(sport, market) mean-CLV on a 90-day window and maps to a Kelly multiplier (1.0 above +0.5% mean CLV, 0.75 down to −0.5%, 0.5 down to −2%, 0.0 below). Gated by `BUCKET_CONFIDENCE_ENABLED` (default off) — inspection-ready before flipping on. **Currently dark** because the largest bucket (esports moneyline) has n=84, below the n=100 floor. Lowering the threshold would activate it on football 1x2, basketball moneyline, tennis moneyline, esports moneyline.

#### 4. **Market-lag exploit: F5 / first-half / period markets**  🟡 IN PROGRESS

**Status:** PR 1 shipped 2026-05-26 — `f5`/`f3` added to `VALID_SCOPES`, Pinnacle MLB period 1 → `scope="f5"`, period 3 → `scope="f3"`. PR 2 (scanner per-scope support — `SPORT_CANONICAL_SCOPE` becomes a set per sport, scanner iterates) pending. PR 3 (wire Kambi MLB F5 so scanner has a soft-book comparison surface) pending. See [pinnacle-period-codes.md](pinnacle-period-codes.md) for the detailed sequencing.

#### 5. **Key-number awareness for NFL spreads / totals**  🟡 PARTIAL

**As shipped:** [analysis/key_numbers.py](../../backend/src/analysis/key_numbers.py) — `NFL_SPREAD_KEY_NUMBERS = (3, 7, 6, 10, 14)` plus total clusters. **Annotation only, not used as stake math** (the module's own docstring: "We don't apply these to the edge math automatically — that risks double-counting whatever Pinnacle already prices in"). The conservative choice — but it means a soft-vs-Pinnacle spread that straddles a key number gets the same Kelly stake as one that doesn't.

#### 6. **Opening-line freshness signal**  ❌ NOT SHIPPED

**Status:** No `first_seen_at` column on `odds` rows. The freshness concept is unimplemented. Lowest-priority of the 9 because Betty's value scanner inherently catches "soft hasn't caught up to Pinnacle yet" — it's the gap, not the staleness of the Pinnacle row, that matters.

#### 7. **Specialization auto-detection (sport-level Kelly weighting)**  ✅ SHIPPED

**As shipped:** This is what `bankroll/bucket_confidence.py` (item #3) is. Same module, two effects: it's both the diagnostic and the auto-throttle. The Kelly multiplier in `get_multiplier(mean_clv_pct, n)` IS the data-driven specialization Anon described.

#### 8. **Tilt guardrails for the unlimited-cluster providers**  ✅ SHIPPED

**As shipped:** [bankroll/drawdown_guard.py](../../backend/src/bankroll/drawdown_guard.py). `UNLIMITED_PROVIDERS = {pinnacle, cloudbet, kalshi, polymarket}` is the Kelly stake basis; soft balances are holding pens that get arbed out and don't count toward the bankroll.

#### 9. **Per-bet rationale snapshot**  🟡 PARTIAL

**As shipped:** `bets.fair_odds_at_placement` and `bets.edge_at_placement` capture the two most critical numbers at placement. The full JSONB snapshot Anon proposed (devig_method, total_books_seen, line_velocity_5min, recent_clv_for_this_bucket) is not implemented. The postmortem analysis ([analysis/postmortem.py](../../backend/src/analysis/postmortem.py)) does compute `expected_win_pct`, `kelly_fraction`, and classification (`expected_loss` / `false_edge` / etc.) from the available fields, so most of the forensic question is already answerable.

---

### Net remaining work from this doc

1. **Period market scanning** — PR 2 + PR 3 of the F5 sequence (in progress).
2. **Key-number → stake math** — Either A: trust Pinnacle's pricing and leave key_numbers.py as annotation only (current stance), or B: add a calibrated multiplier on top of Pinnacle's price for spreads straddling 3/7. The conservative argument (don't double-count) is sound — the user would need to *measure* whether Pinnacle actually misprices key-number transitions before flipping this on.
3. **Full placement snapshot JSONB** — Useful but lower priority since `fair_odds_at_placement` + `edge_at_placement` already let postmortem answer most "why did this lose" questions.
4. **Opening-line freshness** — Lowest priority; the gap between books is what we exploit, not the age of either row.

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

---

## Part 4 — Addenda from full raw-transcript dissection (2026-05-26)

A focused re-read of [anon-sports-consulting-raw.md](anon-sports-consulting-raw.md) surfaced items the first distillation missed. Listing them by section they belong in:

### Addendum to A (Market structure)

- **Late line moves near kickoff are *usually public steam*, not sharp.** Inverts the naive read of any large pre-game move. Verbatim: *"flashy line moves right before game time... that isn't sharp action most of the time."* Implication for [steam_detector.py](../../backend/src/analysis/steam_detector.py): a steam signal in the final ~30 minutes before start is more likely to reflect public hammering than syndicate action, and should be classified separately (or suppressed). Currently the detector doesn't time-segment its signals.
- **Books profile individual bettors and weight respected accounts' action.** A "sharp signal" from the market isn't anonymous — books track win records per account and weight known winners' bets heavily. Verbatim: *"books keep data launch on every single player. How much do they win?"* Implication: this is *why* Betty's account-longevity discipline (round stakes, mug bets, sub-CLV restraint) matters; the book's profiling is the mechanism.
- **Sharp openers are hit when book limits are deliberately low.** Refines the opening-line concept — early sharp action gets accepted at small limits so the book learns from it cheaply. Verbatim: *"when the limits are low, they hit weak openers."* Implication for any future "opening-line freshness" implementation (#6 above): low-limit-at-open is the signal, not just early-in-time.

### Addendum to B (Edge signals)

- **Public-direction timing tactic for totals.** Bet overs *early* before public inflates the line, bet unders *late* after public has pushed it up. Verbatim: *"if you're betting an under, sometimes it's better to wait until the public pushes the number even higher."* Marginal relevance — Betty doesn't model the public independently and instead just acts on the gap to Pinnacle whenever it appears.

### Addendum to E (Sport-specific edges)

#### MLB
- **Key total numbers: 7, 8, 9.** Equivalent to NFL's 37/41/44/47. Verbatim: *"In the MLB, numbers like seven, eight, and nine matter a lot."* Implication: if `key_numbers.py` ever extends beyond NFL, these are the MLB totals to annotate.
- **Series-progression learning effect (refined).** The existing entry notes "3rd-game-with-tired-bullpen → over" — the underlying mechanism is *batter familiarity*, not just bullpen fatigue: repeated exposures teach hitters the bullpen's arsenal. Verbatim: *"now they know the type of pitches, the pitch arsenal and the tendencies."*

#### New: Esports / peer-pool staking
- **1v1Me-style peer staking** is a parallel product class — bettors stake against other bettors on individual player vs. player gaming matches, with the platform taking a fee rather than acting as counterparty. Verbatim: *"staking is not the same thing as a sports book... your money goes into a price pool."* Out of Betty's current scope (no bookmaker line to arbitrage) but worth flagging if peer-exchange markets ever become part of the strategy survey.

### Addendum to F (Workflow / habits)

- **Deposit limits as a bookmaker-side guardrail.** Books offer self-imposed deposit caps as a separate tool from internal loss limits. Verbatim: *"set deposit limits on your sports books as guardrails."* Not applicable to Betty's automated flow (no human deposit decisions in the loop) but flagged for completeness.
- **Baseball Reference and similar stat sources** named explicitly: baseball-reference.com, mlb.com, Covers, OddShark, Picket, Betlapse. Betty doesn't need these (Pinnacle prices the model for us) — the distilled doc's "do NOT need" table covers this, but the explicit source list is preserved here for anyone wanting to spot-check Pinnacle vs. public statistical models.

### Items deliberately skipped from the dissection

These appear in the raw but add nothing actionable:
- Implied-probability cheat sheet by American odds (already implicit in Betty's pipeline)
- Concrete breakeven math at −110 / −105 (already in [profitable-strategies-survey.md](profitable-strategies-survey.md) hold table)
- Anecdotal specialization stat ("49% → 67% in one season") — marketing-flavored, no methodology
- Accountability-partner habit — doesn't translate to an automated platform
- MLB batter-stat list (OBP/SLG/etc.) — Betty consumes Pinnacle's model, doesn't re-derive
