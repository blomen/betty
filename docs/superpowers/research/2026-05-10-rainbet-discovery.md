# Rainbet/Betby protocol — discovery output 2026-05-10

Source artifacts: `c:/tmp/rainbet_discovery/` on the local capture box (ephemeral).
Spec: [`docs/superpowers/specs/2026-05-10-rainbet-provider-design.md`](../specs/2026-05-10-rainbet-provider-design.md).
Plan: [`docs/superpowers/plans/2026-05-10-rainbet-provider.md`](../plans/2026-05-10-rainbet-provider.md).

Capture metadata:
- `summary.json` — host counts + sport navigation results.
- `responses.jsonl` — 348 lines, JSON-per-line of `*.sptpub.com` HTTP responses (bodies truncated to 8000 chars per row).
- `ws_frames.jsonl` — 0 bytes; WebSocket frames were not surfaced by the harness.
- `capture.har` — 46 MB full HAR with base64-encoded full bodies (used to recover the 577 KB market description payload and the 528 KB prematch chunks that the JSONL truncated).

The brand id observed throughout the session is `2374656571012681728`. Outcomes are returned in **decimal** odds (`/api/v2/auth/brand/.../settings` → `"odds_format":"DECIMAL"`).

---

## 1. Sport URL slug map

Per-sport URL navigation under `https://rainbet.com/sportsbook/{slug}` returned title `"Just a moment..."` and an empty `body_sample` for every probed slug (`soccer`, `football`, `basketball`, `tennis`, `ice-hockey`, `american-football`, `baseball`, `mma`, `boxing`, `esports`, `esports/counter-strike`). That is the Cloudflare anti-bot interstitial; the iframe (the `start3.sptpub.com`-hosted Betby SPA) loads anyway and pulls the prematch snapshot regardless of which sportsbook landing page the user is on.

**We do NOT need per-sport URL navigation.** The endpoint `https://api-a-c7818b61-600.sptpub.com/api/v4/prematch/brand/2374656571012681728/en/0` returns a manifest (`top_events_versions` + `rest_events_versions`); each referenced version chunk contains ALL sports' events in one object (`d['events']`, dict keyed by event_id). Across the 5 chunks of one full snapshot we observed 1,886 distinct prematch events spanning 60 sports.

Each event carries `desc.sport` (string of an integer sport_id) AND a chain via `desc.category` → `categories[{category_id}].sport_id`. The two agree (verified across the dataset). Sports themselves are also returned in the same payload under `sports[{sport_id}] = {name, slug, inside_out, priority}`.

The mapping from arnold internal sport keys to Betby `sport_id`:

| arnold key | Betby sport_id | Betby `slug` | Betby `name` | source field | confirmed |
|---|---|---|---|---|---|
| football (soccer) | `1` | `soccer` | `Soccer` | `sports["1"]` in prematch payload | yes — 526 events seen |
| basketball | `2` | `basketball` | `Basketball` | `sports["2"]` | yes — sample event 2664852545721212974 |
| tennis | `5` | `tennis` | `Tennis` | `sports["5"]` | yes — sample event 2664749539319230505 |
| ice_hockey | `4` | `ice-hockey` | `Ice Hockey` | `sports["4"]` | yes — sample event 2665148250939592707 |
| american_football | `16` | `american-football` | `American Football` | `sports["16"]` | yes — sample event 2665260883927773227 |
| baseball | `3` | `baseball` | `Baseball` | `sports["3"]` | yes — sample event 2665111117478633495 |
| mma | `117` | `mixed-martial-arts` | `Mixed Martial Arts` | `sports["117"]` | yes — sample event 2665265931206402075 |
| boxing | `10` | `boxing` | `Boxing` | `sports["10"]` | yes — sample event 2653955729626632226 |
| esports — counter-strike | `109` | `counter-strike` | `Counter-Strike` | `sports["109"]` | yes — sample event 2665231067505635363 |
| esports — league of legends | `110` | `league-of-legends` | `League of Legends` | `sports["110"]` | yes — sample event 2663569846603751441 |
| esports — dota 2 | `111` | `dota-2` | `Dota 2` | `sports["111"]` | yes — sample event 2665264050971217950 |
| esports — valorant | `194` | `valorant` | `Valorant` | `sports["194"]` | yes — 11 events seen |
| esports — rainbow six | `125` | `rainbow-six` | `Rainbow Six` | `sports["125"]` | yes — 8 events seen |
| esports — call of duty | `118` | `call-of-duty` | `Call of Duty` | `sports["118"]` | yes — 3 events seen |
| esports — starcraft 2 | `112` | `starcraft-2` | `StarCraft 2` | `sports["112"]` | yes — 2 events seen |
| esports — mobile legends | `201` | `mobile-legends` | `Mobile Legends` | `sports["201"]` | yes — 6 events seen |
| esports — king of glory | `134` | `king-of-glory` | `King of Glory` | `sports["134"]` | yes — 2 events seen |

Other sports observed in the snapshot we are NOT extracting (out of arnold's scope per CLAUDE.md): `6` Handball, `9` Golf, `12` Rugby Union, `13` Australian Football, `17` Cycling, `21` Cricket, `22` Darts, `23` Volleyball, `26` Water Polo, `29` Futsal, `33` Chess, `34` Beach Volley, `37` Squash, `40` Formula 1, `44` Biathlon, `46` Cross-Country, `48` Ski Jumping, `59` Rugby League, `70` Nascar, `71` Padel Tennis, `90` Surfing, `115` World of Tanks, `123` Crossfire, `128` Rocket League, `129` Indy Racing, `137` FC 26, `138` Kabaddi, `153` NBA 2K26, `188` Touring Car Racing, `189` Formula 2, `210` Teqball, `238` Cricket 24, `242` Pickleball, `244` Footvolley, `246` Predictions, `250` Pro Wrestling, `300` eSoccer, `302` eBasketball, `303` eTennis, `305` eCricket, `309` eSoccer: Volta, `322` eCricket: X-Battle Bats, `323` eVaquejada.

**Implementation note for Task 7 (sport mapping):** the parser consumes ONE prematch snapshot and filters events whose `desc.sport` is in the allowed-sports table above. The arnold sport key is derived from the Betby `sport_id` via a static dict (the dynamic `sports` block of the payload is informational — names/slugs may shift per locale, the integer ID is the stable key).

---

## 2. Shard URL pattern

The WS opened in capture was `wss://api-a-c7818b61-600.sptpub.com/api/v1/ws_new?brand_id=2374656571012681728&lang=en`. The `c7818b61-600` substring looks dynamic.

**Answer: (a) — hardcoded in the SPA's main bundle.** The string `api-a-c7818b61-600.sptpub.com` appears 9 times in `https://start3.sptpub.com/static/js/App.2a4b4138.chunk.js` (1.52 MB Webpack chunk). It does NOT appear in `bt-app-static-themes.sptpub.com/master/rainbet/theme.json` (which is purely CSS/colour theme data) or in any of the layoutConfigs / locales chunks. The bootstrap call sequence is:

1. Browser loads `https://rainbet.com/sportsbook` → Cloudflare interstitial.
2. Page mounts an iframe pointing at the Betby SPA on `https://start3.sptpub.com/...`.
3. SPA fetches `https://bt-app-static-themes.sptpub.com/master/rainbet/theme.json` for theming.
4. SPA reads the hardcoded shard hostname from its own bundle constants and issues `GET https://api-a-c7818b61-600.sptpub.com/api/v2/auth/brand/2374656571012681728/settings?lang=en` to get the brand config.
5. From there, the SPA calls `/api/v4/prematch/brand/{brand}/en/0`, then each version chunk in `rest_events_versions`.

(NOTE: this section is informational only — the parser will use the same shard hostname `api-a-c7818b61-600.sptpub.com` directly. If Betby rotates the shard token, the `bt-renderer.min.js` bundle is the place to re-discover it; alternative discovery via the frontend could parse the App chunk for the regex `api-[a-z]-[0-9a-f]{8}-[0-9]+\.sptpub\.com`.)

---

## 3. Wire format for events/markets

Events arrive via REST. WS frames were empty in capture (the WS open succeeded but no payloads were captured; in the Betby protocol the `ws_new` channel carries odds deltas keyed by the same status hashes the REST snapshot returns, but for a pre-match extractor we want the REST snapshot anyway).

**REST endpoints with non-empty JSON bodies on `api-a-c7818b61-600.sptpub.com`:**

| URL | Description | Size (decoded) |
|---|---|---|
| `/api/v4/prematch/brand/2374656571012681728/en/0` | Prematch snapshot **manifest** — returns `top_events_versions` (1 entry) and `rest_events_versions` (4 entries) listing the chunk timestamps that together form the full snapshot. | ~1.6 KB |
| `/api/v4/prematch/brand/2374656571012681728/en/{version}` | Prematch **chunk** — `events`, `sports`, `categories`, `tournaments` dicts. Last chunk has `snapshot_complete: true`. | 320 KB – 530 KB per chunk; 5 chunks per snapshot, ~1,886 events total |
| `/api/v4/live/brand/2374656571012681728/en/0` | Live snapshot manifest (same shape as prematch `/0`). | ~few KB |
| `/api/v4/live/brand/2374656571012681728/en/{version}` | Live chunk (same schema as prematch chunk; `state.status` ≠ 0 distinguishes live events). | ~250 KB |
| `/api/v3/descriptions/brand/2374656571012681728/markets/en` | **Catalogue of every market_id Betby publishes**: name, market_type, sport-specific `order` and `main_order`, `specifiers` list, and outcome templates with `{$competitor1}`/`{+hcp}` placeholders. THE source of truth for Section 5. | 577 KB |
| `/api/v3/descriptions/brand/2374656571012681728/event/{event_id}/en` | Per-event override for outright/futures markets (custom outcome lists, e.g. World Cup group winners). Not needed for 1x2/spread/total parsing. | ~28 KB sample |
| `/api/v1/descriptions/statuses/en` | Event-state code table (e.g. `"0":"Not started"`, `"21":"In progress"`). | small |
| `/api/v2/auth/brand/{brand}/settings?lang=en` | Brand config: `odds_format:"DECIMAL"`, currency, geo block flag. | ~1.8 KB |
| `/api/v1/side/brand/{brand}/{ts}` | Sidebar prefetch (top events / top tournaments). | varies |
| `/api/v1/top/events/{brand}/country/SE/currency/USD/lang/en` | "Top events" widget data. | varies |
| `/api/v2/promo/banners/brand/{brand}/en` | Promo banner JSON. | ignore |
| `/api/v1/promo/widget/{brand}/en` | Promo widget. | ignore |
| `/api/v1/bonus/bonus/global` | Global bonus. | ignore |
| `/api/v1/promo/tournaments/brand/{brand}/lang/en/view` | Promo tournament list. | ignore |

**Conclusion:** the parser will GET the prematch manifest, then GET each chunk listed in `rest_events_versions` (and optionally `top_events_versions` if non-empty), assemble the union of `events`, and decode using the descriptions/markets catalogue. No WS subscription required; live-event filtering is done in-process by checking `state.status == 0`.

The bootstrap flow:

```
GET /api/v4/prematch/brand/{brand_id}/en/0
  → response includes:
       top_events_versions: [<ts1>, ...]   (often 1 entry)
       rest_events_versions: [<ts2>, <ts3>, <ts4>, <ts5>]
       version: {bootstrap_version}

For each ts in (top_events_versions + rest_events_versions):
  GET /api/v4/prematch/brand/{brand_id}/en/{ts}
    → response is a partial chunk with same shape (sports/categories/tournaments/events)
  Last chunk has "snapshot_complete": true.
```

Repeating the bootstrap call returns a NEW manifest with NEW chunk timestamps (each refresh produces a fresh snapshot). For a polling extractor running every N minutes, just repeat the whole flow — there is no need to track delta versions for prematch.

---

## 4. Event/market schema

### 4.1 Annotated example (real capture)

This is event `2664825045611843589` from the prematch snapshot — a Polish soccer match, with a 1x2 market (id `1`), a Total Goals market (id `18`) carrying 5 over/under lines, and a Double Chance market (id `10`):

```jsonc
{
  "desc": {
    "scheduled": 1778425200,                  // EPOCH SECONDS (UTC), int. Confirmed: 1778425200 → 2026-05-10T15:00:00+00:00.
    "type": "match",                           // "match" for fixtures we want; other values: "stage" (golf), "tournament" (outright). Filter to "match".
    "slug": "zaglebie-sosnowiec-ks-hutnik-krakow-ssa",
    "sport": "1",                              // string of int sport_id; cross-reference sports["1"].
    "category": "1669818868555714560",        // string id into categories{}.
    "tournament": "1670112313136517120",       // string id into tournaments{}.
    "competitors": [
      { "id": "7693",  "sport_id": "1", "name": "Zaglebie Sosnowiec",       "country_code": "", "abbreviation": "ZAG" },  // home
      { "id": "38719", "sport_id": "1", "name": "KS Hutnik Krakow SSA",     "country_code": "", "abbreviation": "HKR" }   // away
    ],
    "player_props": false,
    "bet_builder": true
  },
  "markets": {
    "1": {                                     // market_id "1" = 1x2 (see Section 5).
      "": {                                    // variant key "" = no specifiers.
        "1": { "k": "2.6"  },                  // outcome id "1" = home (template "{$competitor1}"). odds = "2.6" decimal as STRING.
        "2": { "k": "3.5"  },                  // outcome id "2" = draw.
        "3": { "k": "2.34" }                   // outcome id "3" = away ("{$competitor2}").
      }
    },
    "18": {                                    // market_id "18" = Total (over/under). Specifier: "total".
      "total=1.5": {                           // variant key encodes "total={line}".
        "12": { "k": "1.24" },                 // outcome "12" = "over {total}".
        "13": { "k": "3.45" }                  // outcome "13" = "under {total}".
      },
      "total=2.5": { "12": { "k": "1.69" }, "13": { "k": "1.99" } },
      "total=3":   { "13": { "k": "1.58" }, "12": { "k": "2.16" } },
      "total=3.5": { "13": { "k": "1.36" }, "12": { "k": "2.8"  } },
      "total=2":   { "12": { "k": "1.34" }, "13": { "k": "2.88" } }
    },
    "10": {                                    // market_id "10" = Double Chance — NOT in ALLOWED_MARKETS, parser drops.
      "": {
        "9":  { "k": "1.52" },
        "10": { "k": "1.27" },
        "11": { "k": "1.42" }
      }
    }
  },
  "state": {
    "provider": "48681207",                   // internal odds-provider hash, also used in delta updates.
    "status":       0,                         // 0 = "Not started" — keep. Anything else means live/in-progress/cancelled etc., skip.
    "match_status": 0
  }
}
```

A second example, basketball event `2664852545721212974`, showing the spread market:

```jsonc
{
  "desc": {
    "scheduled": 1778425200, "type": "match", "sport": "2",
    "competitors": [
      { "id": "6678",  "sport_id": "2", "name": "Besiktas JK",      "abbreviation": "BJK" },
      { "id": "25426", "sport_id": "2", "name": "Galatasaray SK",   "abbreviation": "GSN" }
    ],
    "player_props": false, "bet_builder": true
  },
  "markets": {
    "1": {                                     // 1x2 — NOTE: basketball published a 3-outcome 1x2 here ("1": 1.22, "2": 17.0, "3": 4.6). Treat as 1x2 only when sport supports draws (see Section 5); for basketball use market 219 instead.
      "": { "2": {"k":"17.0"}, "1": {"k":"1.22"}, "3": {"k":"4.6"} }
    },
    "223": {                                   // market_id "223" = Handicap (incl. overtime). Asian-style with single line.
      "hcp=-10.5": {                           // variant key encodes "hcp={signed_line}".
        "1715": { "k": "1.86" },               // outcome "1715" = away with "(-hcp)".
        "1714": { "k": "1.9"  }                // outcome "1714" = home with "(+hcp)".
      }
    },
    "219": {                                   // market_id "219" = Winner (incl. overtime) — moneyline (2-way).
      "": { "4": {"k":"1.18"}, "5": {"k":"4.7"} }   // outcome "4" = home, "5" = away.
    },
    "225": {                                   // market_id "225" = Total (incl. overtime).
      "total=167.5": { "13": {"k":"1.86"}, "12": {"k":"1.9"} }
    }
  },
  "state": { "provider": "77308e7c", "status": 0, "match_status": 0 }
}
```

### 4.2 Mapping to arnold's `StandardEvent`

- `id` ← top-level dict key in `events` (the string event_id, e.g. `"2664825045611843589"`).
- `name` ← `f"{home_team} vs {away_team}"` derived from `desc.competitors`. Betby has no pre-formatted name.
- `home_team` ← `desc.competitors[0].name` (first entry).
- `away_team` ← `desc.competitors[1].name` (second entry).
  - Edge case: events with `desc.type != "match"` (golf "stage", outright "tournament") have non-team competitors (e.g. `[{name:"The Masters 2027"}, {name:"Winner"}]`) — filter by `desc.type == "match"` to skip.
- `start_time` ← `desc.scheduled` — **epoch seconds, UTC**. Convert to `datetime` via `datetime.fromtimestamp(s, tz=UTC)`. NOT milliseconds, NOT ISO string.
- `markets[].type` ← derived from `desc.sport` + market-id key; see Section 5.
- `markets[].outcomes[].name` ← outcome key string. The descriptions catalogue gives the template (e.g. `"{$competitor1}"`, `"over {total}"`); the parser must materialise it: outcome `"1"`/`"4"`/`"1714"` → home_team, `"3"`/`"5"`/`"1715"` → away_team, `"2"` → `"draw"`, `"12"` → `"over {total}"`, `"13"` → `"under {total}"`.
- `markets[].outcomes[].odds` ← `markets[market_id][variant_key][outcome_id].k` cast to float. Format is **decimal odds as a string** (e.g. `"2.6"`, `"1.86"`). Confirmed via `/api/v2/auth/brand/.../settings` returning `"odds_format":"DECIMAL"`.

### 4.3 Spread (handicap) and total markets

**Variant-key encoding** is the same scheme Cloudbet uses, just URL-form-style:
- Single specifier: `"{spec}={value}"`, e.g. `"total=2.5"`, `"hcp=-1.5"`, `"hcp=-10.5"`, `"setnr=2"`, `"mapnr=3"`.
- Multiple specifiers joined with `|`, e.g. `"mapnr=1|hcp=-0.5"` (Dota market 555).
- No specifiers: empty string `""` (always exactly one variant).

The legitimate specifier names per market are documented in `markets[market_id].specifiers` of the descriptions catalogue (e.g. market 16 has `["hcp"]`, market 18 has `["total"]`, market 188 has `["hcp"]`, market 555 has `["mapnr","hcp"]`). The arnold parser only cares about `hcp` and `total`.

**Handicap units:** signed decimal in the home-team frame. `hcp=-10.5` means home is laying 10.5 points (favourite). The descriptions outcome template is:

```
outcome "1714" name = "{$competitor1} ({+hcp})"   → home with the line as written (e.g. "Besiktas JK (-10.5)")
outcome "1715" name = "{$competitor2} ({-hcp})"   → away with the SIGN-FLIPPED line (e.g. "Galatasaray SK (+10.5)")
```

Parser produces:
- `outcome[home].handicap = +hcp` (numeric, signed; matches what the home team gives/takes)
- `outcome[away].handicap = -hcp` (the opposite sign)
- (or: store both as a single line on the market and use Pinnacle's convention — implementer's choice in Task 8)

**Total units:** non-negative decimal, the over/under threshold. `total=2.5` → outcome `"12"` is "over 2.5", outcome `"13"` is "under 2.5".

### 4.4 Multi-line markets — picking the "main" line

The Betby descriptions catalogue for spread/total markets carries `main_order` — a sport-keyed dict of priorities for which sport-version of the market is the "main" one (e.g. for Total, `main_order["1"]=98` means soccer's primary total market is `id 18`, while basketball's primary is `id 225` with `main_order["2"]=85`). **`main_order` is NOT a per-line indicator**; it disambiguates between competing market_ids for the same sport (e.g. "Total" vs "Total (incl. overtime)").

For a single market_id that ships multiple variants (e.g. market 18 with five `total=*` lines on a soccer match, or market 16/223/225 with several `hcp=*` lines on a handicap-rich market), **Betby does NOT publish a per-line "main" flag in the prematch payload we captured.** The SPA renders all lines under an expandable picker; the "default" line is the one nearest to the bookmaker's expected outcome.

**Parser strategy (matches Cloudbet pattern in `backend/src/providers/cloudbet.py`):**
- Spread: pick the line with smallest `abs(hcp)` (reflects pick'em / pin-line logic). If tied, prefer the negative line (favourite laying points).
- Total: pick the line with the most balanced odds (smallest absolute difference between over and under prices). Falls through to median line if odds are missing.
- Discard all other lines for that market — arnold's `ALLOWED_MARKETS` model is one main line per market type.

This is a conservative default; if Task 12's integration tests show match-rate drops vs Pinnacle, the picker can be tightened later. Document the chosen rule in the parser implementation so it's reviewable.

---

## 5. Market-type ID map

Source: `/api/v3/descriptions/brand/2374656571012681728/markets/en` (full 577 KB body decoded from `capture.har`).

Filter to `ALLOWED_MARKETS = {1x2, moneyline, spread, total}`. Everything else is filtered out at the parser.

The Betby market dictionary is keyed by **string-encoded integers** (`"1"`, `"219"`, `"1000317"`, etc.). The same conceptual market (e.g. "winner") has DIFFERENT ids per sport family, because Betby distinguishes "winner including overtime", "winner including extra innings", "winner including overtime and penalties", etc. The parser must therefore key on `(sport_id, market_id)` not `market_id` alone.

**Minimal map for the sports arnold extracts:**

| betby market_id | arnold type | sport (Betby id) | how to identify | notes |
|---|---|---|---|---|
| `1` | `1x2` | `1` Soccer | descriptions: name `"1x2"`, market_type `Result`, no specifiers, 3 outcomes (`1` home / `2` draw / `3` away). | The canonical 1x2 — only soccer has draws as a meaningful outcome. (Other sports DO ship market 1 occasionally — e.g. basketball event 2664852545721212974 had a 3-outcome market 1 — but the draw outcome is statistically irrelevant for sports without ties; arnold treats those events as moneyline via market 219 instead. The parser SHOULD preferentially read market 219/251/406/186 over market 1 for non-soccer sports; if only market 1 is available it falls back to moneyline-from-1x2 by dropping the draw outcome.) |
| `1` | `1x2` | `4` Ice Hockey | (same descriptor) | NHL/IIHF games can end in regular-time draws with overtime/shootout deciding. Treat as 1x2 if both market 1 and market 406 are absent; otherwise use 406 (winner incl. overtime/penalties). |
| `219` | `moneyline` | `2` Basketball, `13` Australian Football, `16` American Football, `26` Water Polo, `59` Rugby League, `153` NBA 2K26, `155` Beach Soccer, `301` Snooker, `302` eBasketball | descriptions: name `"Winner (incl. overtime)"`, market_type `Result`, no specifiers, 2 outcomes (`4` home / `5` away). | This is the canonical moneyline for basketball-class sports (no possibility of post-OT draw). Outcome ids `4`/`5`, not `1`/`3`. |
| `186` | `moneyline` | `5` Tennis, `10` Boxing, `22` Darts, `33` Chess, `109` Counter-Strike, `110` League of Legends, `111` Dota 2, `117` MMA, `118` Call of Duty, `121` Hearthstone, `123` Crossfire, `125` Rainbow Six, `128` Rocket League, `134` King of Glory, `137` FC 26, `138` Kabaddi, `153` NBA 2K26, `158` Counter-Strike 2 (legacy), `194` Valorant, `196` Heroes of the Storm, `199` World of Warcraft, `200` Hearthstone, `201` Mobile Legends, `230` Smite, `242` Pickleball, `244` Footvolley, `303` eTennis, `404` Wrestling, others | descriptions: name `"Winner"`, market_type `Result`, no specifiers, 2 outcomes (`4` home / `5` away). | The "no overtime" winner — used for tennis/MMA/boxing/esports/etc. where draws don't apply. |
| `251` | `moneyline` | `3` Baseball, `306` MLB | descriptions: name `"Winner (incl. extra innings)"`, market_type `Result`, no specifiers, 2 outcomes (`4` home / `5` away). | Baseball-specific. Sample event 2665111117478633495 confirmed. |
| `406` | `moneyline` | `4` Ice Hockey | descriptions: name `"Winner (incl. overtime and penalties)"`, market_type `Result`, no specifiers, 2 outcomes (`4` home / `5` away). | Hockey-specific. Sample event 2665148250939592707 had market 406 alongside market 1 (3-way reg time) and 18/16. Arnold should prefer 406 (moneyline) over 1 (3-way) for hockey, since reg-time 1x2 is a different bet than match winner. |
| `16` | `spread` | `1` Soccer, `12` Rugby Union, `13` Australian Football, `15` Bandy, `155` Beach Soccer, `195` Floorball, etc. (sport list = market 16's `main_order` keys) | descriptions: name `"Handicap"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` home `(+hcp)` / `1715` away `(-hcp)`). | Asian Handicap for sports where draws on the spread aren't standard. **Soccer chunks rarely ship market 16 in our capture** — we observed only markets 1/10/18 for most soccer events. If 16 isn't published the parser simply has no spread for that event. |
| `223` | `spread` | `2` Basketball, `13` Aussie Rules, `16` American Football, `153` NBA 2K26, `155` Beach Soccer, `302` eBasketball | descriptions: name `"Handicap (incl. overtime)"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` / `1715`). | Basketball/AFL/NFL spread. Sample basketball event 2664852545721212974 had `223` with `hcp=-10.5`. NFL event 2665260883927773227 had `223` with `hcp=-1.5`. |
| `258` | `spread` | `3` Baseball | descriptions: name unknown in our excerpt — but the analogous `258` we observed in capture for baseball event 2665111117478633495 was a `total=13.5` market. **Correction:** baseball's run-line spread is `id 251` companion, but we did NOT see a baseball spread in capture. Treat this as not-yet-confirmed; Task 8 should re-discover via a baseball-heavy capture. | NEEDS_VERIFICATION: capture lacked any baseball event with a `spread`-type market. The descriptions catalogue lists market `258` as `Total (incl. extra innings)` (sport `3` and `306`) — confirmed in section above. So baseball spread market id is unknown from this capture; Task 8 must re-probe with an MLB matchday capture or explicitly query the catalogue for any `Handicap` market_type whose `main_order` has key `"3"`. |
| `188` | `spread` | `5` Tennis, `22` Darts, `32` Beach Tennis | descriptions: name `"Set handicap"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` / `1715`). | Tennis match-set spread (e.g. -1.5 sets). |
| `187` | `spread` | `5` Tennis (alt), `303` eTennis, `311` Padel | descriptions: name `"Game handicap"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` / `1715`). | Tennis games-spread. Tennis events typically ship BOTH 187 and 188; arnold should prefer the set-based 188 (closer to a "match spread" semantic) and ignore 187. |
| `327` | `spread` | `109` Counter-Strike, `110` League of Legends, `111` Dota 2, `112` StarCraft 2, `118` Call of Duty, `121` Hearthstone, `123` Crossfire, `124` Heroes of the Storm, `125` Rainbow Six, `128` Rocket League, `134` King of Glory, `158` Counter-Strike 2, `194` Valorant, `196` Heroes of the Storm, `199` WoW, `200` Hearthstone, `201` Mobile Legends, `230` Smite | descriptions: name `"Map handicap"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` / `1715`). | Esports map-spread (e.g. -1.5 maps in BO3). Sample LoL event 2663569846603751441 had `327` with `hcp=-2.5`. |
| `1000317` | `spread` | `109` Counter-Strike, `194` Valorant, `304`/`308` (other shooters) | descriptions: name `"Rounds handicap"`, market_type `Handicap`, specifiers `["hcp"]`, 2 outcomes (`1714` / `1715`). | CS/Valorant rounds-spread alternative — sample CS event 2665231067505635363 had `1000317` with `hcp=2.5`. Arnold should prefer market 327 (map handicap) when both are present — map-level spread is the cleaner sharp-vs-soft comparison; falls back to 1000317 only when 327 is absent. |
| `18` | `total` | `1` Soccer, `4` Ice Hockey, `10` Boxing, `12` Rugby Union, `13` Aussie Rules, `15` Bandy, `21` Cricket, `24` Snooker, etc. (`main_order` keys for market 18) | descriptions: name `"Total"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` over / `13` under). | Universal soccer-style over/under. Sample soccer event 2665095634083786760 had market 18 with 10 distinct `total=*` lines from 0.5 to 5.0. |
| `225` | `total` | `2` Basketball, `13` Aussie Rules, `16` American Football, `153` NBA 2K26, `155` Beach Soccer, `302` eBasketball | descriptions: name `"Total (incl. overtime)"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` over / `13` under). | Basketball total points incl. OT. NFL total points incl. OT. Sample basketball event had `225` with `total=167.5`; sample NFL event had `225` with `total=10.5`. |
| `258` | `total` | `3` Baseball, `306` MLB | descriptions: name `"Total (incl. extra innings)"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` over / `13` under). | Baseball total runs. Sample baseball event 2665111117478633495 had `258` with `total=13.5`. |
| `189` | `total` | `5` Tennis (main), `303` eTennis, `311` Padel | descriptions: name `"Total games"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` / `13`). | Tennis total games in match. Sample tennis event 2664749539319230505 had `189` with `total=23.5` and `314` with `total=2.5`. Arnold should prefer 189 (game-level total, larger sample, closer to Pinnacle's "Total Games"). |
| `314` | `total` | `5` Tennis (alt), `22` Darts, `303` eTennis | descriptions: name `"Total sets"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` / `13`). | Tennis total sets — only 2 or 3 lines (e.g. 2.5). Less interesting to arnold; skip if 189 is present. |
| `328` | `total` | `109` CS, `110` LoL, `111` Dota 2, `112` SC2, `118` CoD, `124` HotS, `125` R6, `128` RL, `134` KoG, `158` CS2, `199` WoW, `200` HS, `201` ML, `230` Smite, `333` (other) | descriptions: name `"Total maps"`, market_type `Total`, specifiers `["total"]`, 2 outcomes (`12` / `13`). | Esports total maps in series. Sample LoL event had `328` with `total=3.5`; sample Dota event had `328` with `total=2.5`. |

### 5.1 1x2 vs moneyline disambiguation

Arnold uses one of `{1x2, moneyline}` per event in `StandardEvent`. The deciding rule:

- If the sport supports a meaningful draw outcome (Soccer `1`, Ice Hockey `4` for regulation-time bets), **prefer market `1`** as 1x2.
- If the sport never has draws (Basketball, Tennis, Baseball, MMA, Boxing, NFL, esports), **prefer the 2-way "winner" market** (`219` / `186` / `251` / `406`) and emit it as `moneyline` with home/away outcomes only.
- Special case Ice Hockey: market `406` (winner incl. OT and penalties) is true match-winner = `moneyline`; market `1` (regulation-time only) is `1x2` with draw. Arnold should emit `moneyline` from `406` if both are present (Pinnacle's "Money Line" for hockey is incl. OT).

Outcome ID conventions (consistent within Betby):
- `1` / `2` / `3` — three-way result: home / draw / away.
- `4` / `5` — two-way result (no draw): home / away.
- `12` / `13` — over / under (paired with a `total` specifier).
- `1714` / `1715` — home(+hcp) / away(-hcp) (paired with a `hcp` specifier).
- `9` / `10` / `11` — Double Chance: home-or-draw / draw-or-away / home-or-away — IGNORE (not in `ALLOWED_MARKETS`).
- `74` / `76` — yes / no on Yes/No markets (e.g. market 911 "Will the fight go the distance") — IGNORE.

### 5.2 How to identify market category from the descriptions catalogue programmatically

The descriptions catalogue gives the parser everything it needs without hard-coding the table above:

```python
# Pseudocode — exact code lives in Task 7's market mapper.
def categorize(descriptor: dict) -> str | None:
    name = descriptor.get("name", "").lower()
    market_type = descriptor.get("market_type", "")
    specs = descriptor.get("specifiers") or []

    if name == "1x2":
        return "1x2"
    if name.startswith("winner"):                       # "Winner", "Winner (incl. overtime)", etc.
        return "moneyline"
    if market_type == "Handicap" and specs == ["hcp"]:  # primary spread markets
        return "spread"
    if market_type == "Total"    and specs == ["total"]: # primary total markets
        return "total"
    return None  # filtered
```

The hardcoded sport→market_id preference table is still required for **disambiguation when an event ships multiple eligible markets** (e.g. soccer with both market 1 and market 219 — favour 1; basketball with both 1 and 219 — favour 219). Build that table from the descriptions catalogue at startup by walking `descriptors[].main_order` and recording which market_id has the highest `main_order` priority per sport per arnold-type.

### 5.3 Anomalies / gaps from this capture

- **No baseball spread observed.** Market `251` is the moneyline (confirmed); market `258` is the total (confirmed). Baseball run-line spread is whichever market_id has `market_type=Handicap` and `main_order["3"]` set in the descriptions catalogue — Task 8 should look this up directly from the full catalogue (it's in the 577 KB descriptions response we have on disk). Likely candidates from the catalogue's market_id list: `259`, `260` (range adjacent to `258`).
- **No outright/futures coverage.** Events of `desc.type == "stage"` or `"tournament"` carry hundreds of `tt:outcometext:...` outcomes (e.g. golf "winner of the Masters"). Arnold doesn't extract outrights — filter `desc.type == "match"` only.
- **`state.status` filtering for live-vs-prematch:** observed `status: 0` on every prematch event. The status code table (`/api/v1/descriptions/statuses/en`) confirms `0 = "Not started"`. The parser MUST drop any event with `state.status != 0` (matches arnold's "skip live" policy).
- **Odds key is a STRING.** Every `k` field we sampled is a JSON string (e.g. `"2.6"`, `"1.97"`, `"3.45"`). Cast to float at parse time; do not assume number type.
- **Competitor order is NOT marked.** `desc.competitors[0]` is home, `[1]` is away by the rendering convention used throughout the descriptions templates (`{$competitor1}` always maps to outcome ids `1`, `4`, `1714`; `{$competitor2}` maps to `3`, `5`, `1715`). Confirmed across all 7 sport samples we inspected — no field flips this. The parser can rely on positional order.
- **WS frames are deltas, not snapshots.** The capture shows the WS open but no frames. For a polling REST extractor we don't need them, but if Task 12 ever wants live-odds delta tracking, the WS protocol is documented in third-party reverse-engineering guides for Betby; not needed for this provider.
- **Markets the SPA renders that are NOT in our snapshot:** `desc.player_props` and `desc.bet_builder` flags hint that additional markets are fetched on demand when the user opens the event detail page. For our prematch-snapshot use-case this is fine (props/builder aren't in `ALLOWED_MARKETS`).

---

## 6. Implementation cheat-sheet (for Tasks 6-10)

When in doubt, refer back to the artifacts at `c:/tmp/rainbet_discovery/` and the decoded files this analysis produced:
- `prematch_chunk_1.json` — full 528 KB decoded prematch chunk.
- `markets_descriptions.json` — full 577 KB market catalogue.

Concrete invariants the parser can assume:

1. **HTTP host:** `https://api-a-c7818b61-600.sptpub.com` (hardcoded in `App.2a4b4138.chunk.js`; revisit if the SPA bundle hash changes).
2. **Brand id:** `2374656571012681728`.
3. **Endpoint:** `GET /api/v4/prematch/brand/{brand_id}/en/0` returns manifest; loop over `top_events_versions ∪ rest_events_versions` and `GET /api/v4/prematch/brand/{brand_id}/en/{version}` to assemble the full snapshot.
4. **Headers:** capture used a normal browser User-Agent through a SOCKS5 proxy (Bahnhof). Sptpub does not appear to require auth or signed tokens for prematch data — the `/api/v2/auth/brand/.../settings` endpoint returns brand config without any Authorization header. (To confirm: try `requests.get` directly in Task 8's smoke test; if blocked, fall back to Playwright iframe interception like Altenar.)
5. **Response decoding:** server gzips/brotlis but standard libraries handle that transparently. JSON top-level keys (per chunk): `epoch`, `version`, `generated`, `snapshot_complete`, `fixtures_complete`, `status`, `strict_providers`, `sports`, `categories`, `tournaments`, `events`.
6. **Filter chain:**
   - `events[event_id].desc.type == "match"` (drop stage/tournament outrights).
   - `events[event_id].state.status == 0` (drop live/cancelled/postponed).
   - `events[event_id].desc.sport in ALLOWED_SPORTS` (the 17-row table in Section 1).
7. **Per-event extract:**
   - id = event_id (the dict key).
   - home/away = `desc.competitors[0].name` / `desc.competitors[1].name`.
   - start_time = `datetime.fromtimestamp(desc.scheduled, tz=UTC)`.
   - sport = lookup `desc.sport` in the sport map.
   - For each `(market_id, variant_key)` in `events[event_id].markets`:
     - Look up `descriptions[market_id]` to determine arnold type via `categorize()` (Section 5.2).
     - Filter to `(sport_id, market_id)` permitted by the preference table (Section 5).
     - Parse variant_key (split on `|`, then on `=`) to extract `hcp` and/or `total`.
     - For each outcome id, materialise outcome name from the descriptions template.
     - Cast `k` to float for odds.
     - For multi-line spread/total markets, apply the "main line" picker (Section 4.4).
8. **All odds are decimal** (per `/api/v2/auth/brand/.../settings`); no conversion needed before arnold's downstream pipeline.

Done.
