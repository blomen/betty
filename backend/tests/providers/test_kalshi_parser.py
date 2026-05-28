"""Tests for Kalshi market parser."""

import json
from pathlib import Path

import pytest

from src.providers.kalshi import (
    KalshiRetriever,
    _extract_teams_from_title,
    _no_side_odds,
    _parse_spread_rung,
    _parse_total_rung,
    _strip_market_label,
    parse_event,
    parse_spread_event,
    parse_total_event,
    series_to_sport,
    spread_series_to_sport,
    total_series_to_sport,
)


class TestExtractTeamsFromTitle:
    def test_us_at_convention_flips_order(self):
        # "Lakers at Warriors" → visitor at host → home=Warriors, away=Lakers.
        assert _extract_teams_from_title("Lakers at Warriors") == ("Warriors", "Lakers")

    def test_us_at_symbol_convention_flips_order(self):
        assert _extract_teams_from_title("Lakers @ Warriors") == ("Warriors", "Lakers")

    def test_vs_convention_preserves_order(self):
        # European: "Arsenal vs Chelsea" → home=Arsenal, away=Chelsea.
        assert _extract_teams_from_title("Arsenal vs Chelsea") == ("Arsenal", "Chelsea")

    def test_game_prefix_stripped(self):
        # Real Kalshi NBA titles include "Game N:" prefix.
        home, away = _extract_teams_from_title("Game 4: Los Angeles L at Houston")
        assert home == "Houston"
        assert away == "Los Angeles L"

    def test_no_separator_returns_empty_away(self):
        assert _extract_teams_from_title("Mystery") == ("Mystery", "")


class TestSeriesToSport:
    @pytest.mark.parametrize(
        "ticker,expected",
        [
            ("KXNBAGAME-26APR18LALGSW-LAL", "basketball"),
            ("KXNFLGAME-26WEEK5-KC", "american_football"),
            ("KXMLBGAME-26APR18NYY-NYY", "baseball"),
            ("KXNHLGAME-26APR18BOS-BOS", "ice_hockey"),
            ("KXNCAAFGAME-26WEEK3-ALA", "american_football"),
            ("KXNCAABGAME-26MAR21-DUKE", "basketball"),
            ("KXTENNISAUSOPEN-26-DJOKOVIC", "tennis"),
            ("KXUFC300-26-JONES", "mma"),
            ("KXBOXINGFURY-26-FURY", "boxing"),
            ("KXEPL-26MAY-ARS", "football"),
            ("KXUCL-26APR-RMA", "football"),
            ("KXWC26-26JUL-ARG", "football"),
        ],
    )
    def test_known_prefixes(self, ticker, expected):
        assert series_to_sport(ticker) == expected

    def test_unknown_prefix_returns_none(self):
        assert series_to_sport("KXWEATHERNYC-26-75F") is None
        assert series_to_sport("KXPREZ-26-DEM") is None


class TestParseEvent:
    def _event(self, ticker: str, markets: list[dict], title: str = "LAL vs GSW") -> dict:
        return {
            "event_ticker": ticker,
            "title": title,
            "markets": markets,
        }

    def _market(
        self,
        ticker: str,
        yes_sub_title: str,
        yes_ask: float,
        volume: float = 5000.0,
        status: str = "active",
    ) -> dict:
        # Match live Kalshi schema: yes_ask_dollars (float 0–1), volume_fp (USD notional).
        return {
            "ticker": ticker,
            "status": status,
            "yes_sub_title": yes_sub_title,
            "yes_ask_dollars": yes_ask,
            "no_ask_dollars": round(1.0 - yes_ask, 4),
            "volume_fp": volume,
        }

    def test_nba_moneyline_two_contracts(self):
        # "Lakers at Warriors" → US convention: Lakers is the visitor, Warriors
        # is home. Kalshi's yes_sub_title identifies each contract's team, so
        # we must NOT rely on volume ordering.
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                # Lakers has HIGHER volume but must still be "away".
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "Lakers", 0.60, volume=9000),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "Warriors", 0.42, volume=3000),
            ],
            title="Lakers at Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "basketball"
        assert result.provider == "kalshi"
        assert result.home_team == "Warriors"
        assert result.away_team == "Lakers"
        assert len(result.markets) == 1
        mkt = result.markets[0]
        assert mkt["type"] == "moneyline"
        assert len(mkt["outcomes"]) == 2
        # Outcomes must be labelled by team-match, not volume order.
        by_name = {o["name"]: o for o in mkt["outcomes"]}
        assert set(by_name) == {"home", "away"}
        # Lakers @ 0.60 → effective=0.6048 → odds ~1.653 is the AWAY outcome.
        assert 1.6 < by_name["away"]["odds"] < 1.7
        # Warriors @ 0.42 → effective=0.4249 → odds ~2.35 is the HOME outcome.
        assert 2.3 < by_name["home"]["odds"] < 2.4

    def test_below_volume_threshold_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.60, volume=50),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.42, volume=50),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_all_50_50_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.50, volume=5000),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.50, volume=5000),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_unknown_sport_skipped(self):
        event = self._event(
            "KXWEATHERNYC-26-75F",
            [self._market("KXWEATHERNYC-26-75F-YES", "75F", 0.30, volume=5000)],
        )
        assert parse_event(event, min_volume_usd=100.0, fee_rate=0.02) is None

    def test_soccer_3way_1x2(self):
        # "Arsenal vs Chelsea" → European convention: Arsenal is home.
        event = self._event(
            "KXEPL-26MAY-ARSCHE",
            [
                self._market("KXEPL-26MAY-ARSCHE-ARS", "Arsenal", 0.55),
                self._market("KXEPL-26MAY-ARSCHE-DRAW", "Draw", 0.25),
                self._market("KXEPL-26MAY-ARSCHE-CHE", "Chelsea", 0.22),
            ],
            title="Arsenal vs Chelsea",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "football"
        assert result.home_team == "Arsenal"
        assert result.away_team == "Chelsea"
        mkt = result.markets[0]
        assert mkt["type"] == "1x2"
        assert len(mkt["outcomes"]) == 3
        names = {o["name"] for o in mkt["outcomes"]}
        assert names == {"home", "draw", "away"}
        # The team tagged as HOME must indeed be the one matching Arsenal's
        # yes_sub_title, not a volume-dependent assignment.
        by_name = {o["name"]: o for o in mkt["outcomes"]}
        assert by_name["home"]["provider_meta"]["ticker"] == "KXEPL-26MAY-ARSCHE-ARS"
        assert by_name["away"]["provider_meta"]["ticker"] == "KXEPL-26MAY-ARSCHE-CHE"

    def test_unresolved_title_skipped(self):
        """If yes_sub_title and ticker suffix both fail to map to home/away,
        the event is dropped rather than emitting bad data."""
        event = self._event(
            "KXNBAGAME-26APR18AAABBB",
            [
                # yes_sub_title and ticker suffix don't match either title side.
                self._market("KXNBAGAME-26APR18AAABBB-FOO", "Unknown Contract A", 0.55),
                self._market("KXNBAGAME-26APR18AAABBB-BAR", "Unknown Contract B", 0.45),
            ],
            title="Lakers at Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_unresolved_title_no_separator_skipped(self):
        """Title with no ' at ' / ' vs ' separator cannot yield home/away."""
        event = self._event(
            "KXNBAGAME-26APR18X",
            [
                self._market("KXNBAGAME-26APR18X-A", "Team A", 0.55),
                self._market("KXNBAGAME-26APR18X-B", "Team B", 0.45),
            ],
            title="Mystery Showdown",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None


class TestKalshiRetriever:
    def test_parse_fixture_produces_events(self):
        fixture_path = Path(__file__).parent / "fixtures" / "kalshi" / "events_sports.json"
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))

        config = {"id": "kalshi", "params": {"min_volume_usd": 100}}
        retriever = KalshiRetriever(config)
        events = retriever.parse(raw, sport="basketball")

        # Fixture is captured with &series_ticker=KXNBAGAME, so at least one
        # NBA event should parse. If this is zero, the parser or fixture is wrong.
        assert len(events) > 0, "Expected at least one basketball event from NBA fixture"
        for e in events:
            assert e.provider == "kalshi"
            assert e.sport == "basketball"
            assert e.markets and e.markets[0]["type"] in ("moneyline", "1x2")
            for outcome in e.markets[0]["outcomes"]:
                assert outcome["odds"] > 1.0
                assert "provider_meta" in outcome
                assert "ticker" in outcome["provider_meta"]


class TestStripMarketLabel:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Game 1: Cleveland at New York: Spread", "Game 1: Cleveland at New York"),
            ("Game 1: Cleveland at New York: Total Points", "Game 1: Cleveland at New York"),
            ("Cleveland at New York: Total Goals", "Cleveland at New York"),
            ("Cleveland at New York: Total Runs", "Cleveland at New York"),
            ("Diaz Acosta vs O'Connell: Total Games", "Diaz Acosta vs O'Connell"),
            ("Game 1: Cleveland at New York: Total", "Game 1: Cleveland at New York"),
            ("Lakers at Celtics Spread", "Lakers at Celtics"),
            ("Lakers at Celtics", "Lakers at Celtics"),  # no label: unchanged
        ],
    )
    def test_strip(self, raw, expected):
        assert _strip_market_label(raw) == expected


class TestSpreadTotalSeriesLookup:
    @pytest.mark.parametrize(
        "ticker,expected",
        [
            ("KXNBASPREAD-26MAY19CLENYK", "basketball"),
            ("KXNFLSPREAD-26WEEK5-KCDET", "american_football"),
            ("KXMLBSPREAD-26MAY19NYYBOS", "baseball"),
            ("KXNHLSPREAD-26MAY19BOSPIT", "ice_hockey"),
            ("KXATPGAMESPREAD-26MAY19", "tennis"),
            ("KXATPGSPREAD-26MAY19", "tennis"),
            ("KXEPLSPREAD-26MAY19", "football"),
            ("KXBOGUS-26MAY19", None),
        ],
    )
    def test_spread_lookup(self, ticker, expected):
        assert spread_series_to_sport(ticker) == expected

    @pytest.mark.parametrize(
        "ticker,expected",
        [
            ("KXNBATOTAL-26MAY19CLENYK", "basketball"),
            ("KXNFLTOTAL-26WEEK5", "american_football"),
            ("KXATPGAMETOTAL-26MAY19", "tennis"),
            ("KXEPLTOTAL-26MAY19", "football"),
            ("KXBOGUS-26MAY19", None),
        ],
    )
    def test_total_lookup(self, ticker, expected):
        assert total_series_to_sport(ticker) == expected


class TestRungParsers:
    def test_spread_rung_home_match(self):
        m = {"yes_sub_title": "New York wins by over 7.5 points"}
        assert _parse_spread_rung(m, "New York", "Cleveland") == ("home", 7.5)

    def test_spread_rung_away_match(self):
        m = {"yes_sub_title": "Cleveland wins by over 12.5 points"}
        assert _parse_spread_rung(m, "New York", "Cleveland") == ("away", 12.5)

    def test_spread_rung_ambiguous_returns_none(self):
        # Both home and away substring match → return None.
        m = {"yes_sub_title": "New York wins by over 7.5 points"}
        assert _parse_spread_rung(m, "New York Jets", "New York Giants") is None

    def test_spread_rung_unparseable_returns_none(self):
        assert _parse_spread_rung({"yes_sub_title": "exotic prop"}, "A", "B") is None

    @pytest.mark.parametrize(
        "sub,expected",
        [
            ("Over 16.5 games", 16.5),
            ("Over 210.5 points", 210.5),
            ("Over 8.5 goals", 8.5),
            ("Over 7.5 runs", 7.5),
            ("Total under", None),  # no number
        ],
    )
    def test_total_rung(self, sub, expected):
        assert _parse_total_rung({"yes_sub_title": sub}) == expected


class TestNoSideOdds:
    def test_prices_off_no_ask(self):
        # NO ask 0.55 (no fee) → _price_to_odds(0.55) = 1/0.55 = 1.818.
        odds = _no_side_odds({"no_ask_dollars": 0.55}, 0.0)
        assert abs(odds - 1.818) < 0.001

    def test_not_derived_from_yes_ask(self):
        # 5¢ spread: yes_ask 0.80, no_ask 0.25. The NO side MUST price off the
        # no_ask (1/0.25 = 4.0), never 1 - yes_ask = 0.20 (1/0.20 = 5.0) — the
        # latter is the NO bid and inflates every under/away bet.
        odds = _no_side_odds({"yes_ask_dollars": 0.80, "no_ask_dollars": 0.25}, 0.0)
        assert abs(odds - 4.0) < 0.001

    def test_degenerate_returns_zero(self):
        assert _no_side_odds({"no_ask_dollars": 1.0}, 0.02) == 0.0
        assert _no_side_odds({"no_ask_dollars": 0.0}, 0.02) == 0.0
        assert _no_side_odds({}, 0.02) == 0.0  # no NO quote at all


class TestParseSpreadEvent:
    def _ladder(self):
        return [
            {
                "yes_sub_title": "New York wins by over 29.5 points",
                "ticker": "X-NYK29",
                "status": "active",
                "yes_ask_dollars": 0.08,
                "no_ask_dollars": 0.92,
                "volume_fp": 1000,
            },
            {
                "yes_sub_title": "New York wins by over 7.5 points",
                "ticker": "X-NYK7",
                "status": "active",
                "yes_ask_dollars": 0.55,
                "no_ask_dollars": 0.45,
                "volume_fp": 5000,
            },
            {
                "yes_sub_title": "New York wins by over 12.5 points",
                "ticker": "X-NYK12",
                "status": "active",
                "yes_ask_dollars": 0.30,
                "no_ask_dollars": 0.70,
                "volume_fp": 3000,
            },
        ]

    def test_emits_every_quoted_rung(self):
        raw = {
            "event_ticker": "KXNBASPREAD-26MAY19CLENYK",
            "title": "Game 1: Cleveland at New York: Spread",
            "markets": self._ladder(),
        }
        out = parse_spread_event(raw, home="New York", away="Cleveland", min_volume_usd=100)
        # All three ladder rungs (7.5, 12.5, 29.5) cleanly quoted → 3 markets emitted.
        assert len(out) == 3
        assert all(m["type"] == "spread" for m in out)
        points = sorted({abs(m["outcomes"][0]["point"]) for m in out})
        assert points == [7.5, 12.5, 29.5]
        # On every rung the YES contract is "home wins by over |point|", so home
        # always carries the negative point. The favored side flips with the
        # spread: at -7.5 home is favored (55% YES), at -29.5 home is the
        # underdog (8% YES) — both are valid sharp-middle / alternate-line rows.
        for m in out:
            home_o = next(o for o in m["outcomes"] if o["name"] == "home")
            away_o = next(o for o in m["outcomes"] if o["name"] == "away")
            assert home_o["point"] < 0
            assert away_o["point"] > 0
            assert home_o["point"] == -away_o["point"]
        m_low = next(m for m in out if m["outcomes"][0]["point"] == -7.5)
        m_high = next(m for m in out if m["outcomes"][0]["point"] == -29.5)
        low_home = next(o for o in m_low["outcomes"] if o["name"] == "home")["odds"]
        low_away = next(o for o in m_low["outcomes"] if o["name"] == "away")["odds"]
        high_home = next(o for o in m_high["outcomes"] if o["name"] == "home")["odds"]
        high_away = next(o for o in m_high["outcomes"] if o["name"] == "away")["odds"]
        assert low_home < low_away
        assert high_home > high_away

    def test_no_matching_submarkets_returns_empty(self):
        raw = {
            "event_ticker": "X",
            "title": "Game 1: Cleveland at New York: Spread",
            "markets": [
                {
                    "yes_sub_title": "Mystery prop",
                    "ticker": "x",
                    "status": "active",
                    "yes_ask_dollars": 0.5,
                    "volume_fp": 5000,
                },
            ],
        }
        assert parse_spread_event(raw, home="New York", away="Cleveland") == []

    def test_zero_yes_price_skips_rung(self):
        raw = {
            "event_ticker": "X",
            "title": "X",
            "markets": [
                {
                    "yes_sub_title": "New York wins by over 5.5 points",
                    "ticker": "x",
                    "status": "active",
                    "yes_ask_dollars": 0.0,
                    "volume_fp": 5000,
                },
            ],
        }
        assert parse_spread_event(raw, home="New York", away="Cleveland") == []


class TestParseTotalEvent:
    def test_emits_every_quoted_rung(self):
        raw = {
            "event_ticker": "KXNBATOTAL-26MAY19CLENYK",
            "title": "Game 1: Cleveland at New York: Total Points",
            "markets": [
                {
                    "yes_sub_title": "Over 195.5 points",
                    "ticker": "X-195",
                    "status": "active",
                    "yes_ask_dollars": 0.85,
                    "no_ask_dollars": 0.15,
                    "volume_fp": 2000,
                },
                {
                    "yes_sub_title": "Over 209.5 points",
                    "ticker": "X-209",
                    "status": "active",
                    "yes_ask_dollars": 0.50,
                    "no_ask_dollars": 0.50,
                    "volume_fp": 8000,
                },
                {
                    "yes_sub_title": "Over 225.5 points",
                    "ticker": "X-225",
                    "status": "active",
                    "yes_ask_dollars": 0.15,
                    "no_ask_dollars": 0.85,
                    "volume_fp": 2000,
                },
            ],
        }
        out = parse_total_event(raw, min_volume_usd=100)
        assert len(out) == 3
        points = sorted(m["outcomes"][0]["point"] for m in out)
        assert points == [195.5, 209.5, 225.5]
        # Middle rung at 0.50/0.50 → odds ~2.0 each side.
        mid = next(m for m in out if m["outcomes"][0]["point"] == 209.5)
        over_o = next(o for o in mid["outcomes"] if o["name"] == "over")
        under_o = next(o for o in mid["outcomes"] if o["name"] == "under")
        assert 1.9 < over_o["odds"] < 2.1
        assert 1.9 < under_o["odds"] < 2.1
