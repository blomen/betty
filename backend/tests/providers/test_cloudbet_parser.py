"""Tests for Cloudbet REST Feed API response parsing."""

import pytest

from src.providers.cloudbet import (
    parse_event,
    parse_selections_to_market,
)


class TestParseSelectionsToMarket:
    def test_moneyline_two_selections(self):
        selections = [
            {"outcome": "home", "params": "", "price": 1.80, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.00, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.moneyline")
        assert result is not None
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0] == {"name": "home", "odds": 1.80}
        assert result["outcomes"][1] == {"name": "away", "odds": 2.00}

    def test_1x2_three_selections_with_draw(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "draw", "params": "", "price": 3.40, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.match_odds")
        assert result is not None
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 2.80}
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.40}
        assert result["outcomes"][2] == {"name": "away", "odds": 2.50}

    def test_handicap_selections_main_line_only(self):
        selections = [
            {
                "outcome": "home",
                "params": "handicap=-1.5",
                "price": 2.10,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
            {
                "outcome": "away",
                "params": "handicap=-1.5",
                "price": 1.75,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
            {
                "outcome": "home",
                "params": "handicap=-2.5",
                "price": 3.00,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
            {
                "outcome": "away",
                "params": "handicap=-2.5",
                "price": 1.40,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
        ]
        result = parse_selections_to_market(selections, "soccer.asian_handicap")
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        # Main line = smallest absolute handicap = 1.5
        home = next(o for o in result["outcomes"] if o["name"] == "home")
        away = next(o for o in result["outcomes"] if o["name"] == "away")
        assert home["point"] == -1.5
        assert home["odds"] == 2.10
        assert away["point"] == 1.5
        assert away["odds"] == 1.75

    def test_totals_selections_main_line_only(self):
        selections = [
            {"outcome": "over", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "over", "params": "total=3.5", "price": 2.20, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=3.5", "price": 1.65, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.total_goals")
        assert result is not None
        assert result["type"] == "total"
        assert len(result["outcomes"]) == 2
        # Main line = smallest total = 2.5
        over = next(o for o in result["outcomes"] if o["name"] == "over")
        under = next(o for o in result["outcomes"] if o["name"] == "under")
        assert over["point"] == 2.5
        assert over["odds"] == 1.90
        assert under["point"] == 2.5
        assert under["odds"] == 1.90

    def test_disabled_selection_returns_none(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_DISABLED", "side": "BACK"},
            {"outcome": "draw", "params": "", "price": 3.40, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.match_odds")
        assert result is None

    def test_empty_selections_returns_none(self):
        result = parse_selections_to_market([], "soccer.match_odds")
        assert result is None

    def test_suspended_selection_returns_none(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_SUSPENDED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.moneyline")
        assert result is None

    def test_market_key_basketball_handicap(self):
        selections = [
            {
                "outcome": "home",
                "params": "handicap=-5.5",
                "price": 1.90,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
            {
                "outcome": "away",
                "params": "handicap=-5.5",
                "price": 1.90,
                "status": "SELECTION_ENABLED",
                "side": "BACK",
            },
        ]
        result = parse_selections_to_market(selections, "basketball.handicap")
        assert result is not None
        assert result["type"] == "spread"

    def test_market_key_basketball_totals(self):
        selections = [
            {"outcome": "over", "params": "total=220.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=220.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.totals")
        assert result is not None
        assert result["type"] == "total"
        over = next(o for o in result["outcomes"] if o["name"] == "over")
        assert over["point"] == 220.5

    def test_totals_single_line(self):
        """Single-line total — no filtering needed, just return it."""
        selections = [
            {"outcome": "over", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.total_goals")
        assert result is not None
        assert result["type"] == "total"
        assert len(result["outcomes"]) == 2


class TestParseEvent:
    def _make_football_event(self):
        return {
            "id": 12345,
            "name": "Manchester City V Liverpool",
            "status": "TRADING",
            "startTime": "2026-04-15T15:00:00Z",
            "home": {"name": "Manchester City", "key": "c1-manchester-city"},
            "away": {"name": "Liverpool", "key": "c2-liverpool"},
            "markets": {
                "soccer.match_odds": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {
                                    "outcome": "home",
                                    "params": "",
                                    "price": 2.80,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "draw",
                                    "params": "",
                                    "price": 3.40,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "away",
                                    "params": "",
                                    "price": 2.50,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                            ]
                        }
                    }
                },
                "soccer.asian_handicap": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {
                                    "outcome": "home",
                                    "params": "handicap=-1.5",
                                    "price": 2.10,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "away",
                                    "params": "handicap=-1.5",
                                    "price": 1.75,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "home",
                                    "params": "handicap=-2.5",
                                    "price": 3.00,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "away",
                                    "params": "handicap=-2.5",
                                    "price": 1.40,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                            ]
                        }
                    }
                },
                "soccer.total_goals": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {
                                    "outcome": "over",
                                    "params": "total=2.5",
                                    "price": 1.90,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "under",
                                    "params": "total=2.5",
                                    "price": 1.90,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                            ]
                        }
                    }
                },
            },
        }

    def test_football_event_all_markets(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        market_types = {m["type"] for m in event.markets}
        assert "1x2" in market_types
        assert "spread" in market_types
        assert "total" in market_types

    def test_football_event_basic_fields(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert event.id == "cloudbet_12345"
        assert event.sport == "football"
        assert event.provider == "cloudbet"
        assert event.start_time == "2026-04-15T15:00:00Z"

    def test_football_event_team_names_normalized(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert event.home_team == "manchester city"
        assert event.away_team == "liverpool"

    def test_live_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "TRADING_LIVE"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_resulted_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "RESULTED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_cancelled_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "CANCELLED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_suspended_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "SUSPENDED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_no_home_returns_none(self):
        event_data = self._make_football_event()
        event_data["home"] = None
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_no_away_returns_none(self):
        event_data = self._make_football_event()
        event_data["away"] = None
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_event_name_format(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert " vs " in event.name

    def test_no_markets_returns_none(self):
        event_data = self._make_football_event()
        event_data["markets"] = {}
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_basketball_event_moneyline(self):
        event_data = {
            "id": 99001,
            "name": "Lakers V Celtics",
            "status": "TRADING",
            "startTime": "2026-04-15T02:00:00Z",
            "home": {"name": "Los Angeles Lakers", "key": "c1-lakers"},
            "away": {"name": "Boston Celtics", "key": "c2-celtics"},
            "markets": {
                "basketball.moneyline": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {
                                    "outcome": "home",
                                    "params": "",
                                    "price": 1.80,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                                {
                                    "outcome": "away",
                                    "params": "",
                                    "price": 2.00,
                                    "status": "SELECTION_ENABLED",
                                    "side": "BACK",
                                },
                            ]
                        }
                    }
                }
            },
        }
        event = parse_event(event_data, "basketball", "cloudbet")
        assert event is not None
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "moneyline"


class TestThreeWaySkippedForNoDrawSports:
    """For sports where regulation can end tied but the canonical match-winner
    is 2-way (basketball/hockey/NFL/baseball/tennis/esports + combat), the
    3-way `*.1x2` / `*.match_odds` is a DIFFERENT proposition than the 2-way
    `*.moneyline` / `*.winner`. The draw outcome is real (regulation tie,
    decided in OT) and the market carries its own vig — de-drawing the 3-way
    produces inflated 2-way prices that manufacture fake +EV against
    Pinnacle's true moneyline. So we skip the 3-way entirely.

    Verified live for Cloudbet IBL basketball (2026-05-23): the affiliate API
    shipped `basketball.moneyline` at price=0 (suspended) and
    `basketball.1x2` at 5.86/18.02/1.08. The old de-drawing path stored
    5.86/1.08 as `moneyline`, vs Pinnacle's true moneyline near 4.83/1.03,
    printing a fictitious +16.3% edge.
    """

    def _basketball_3way_event(self):
        return {
            "id": "evt1",
            "status": "TRADING",
            "home": {"name": "Alba Berlin"},
            "away": {"name": "Rasta Vechta"},
            "markets": {
                "basketball.match_odds": {
                    "submarkets": {
                        "main": {
                            "selections": [
                                {"outcome": "home", "price": 1.34, "status": "SELECTION_ENABLED"},
                                {"outcome": "draw", "price": 15.83, "status": "SELECTION_ENABLED"},
                                {"outcome": "away", "price": 3.57, "status": "SELECTION_ENABLED"},
                            ]
                        }
                    }
                }
            },
        }

    def test_basketball_3way_never_becomes_moneyline(self):
        # With only the 3-way market available and no genuine 2-way, the event
        # has no moneyline at all — better than a counterfeit one.
        ev = parse_event(self._basketball_3way_event(), "basketball", "cloudbet")
        assert ev is None  # no extractable markets → event dropped

    @pytest.mark.parametrize("sport", ["ice_hockey", "american_football", "baseball", "tennis"])
    def test_other_no_draw_sports_skip_three_way(self, sport):
        ev = parse_event(self._basketball_3way_event(), sport, "cloudbet")
        assert ev is None

    def test_football_keeps_1x2_with_draw(self):
        ev = parse_event(self._basketball_3way_event(), "football", "cloudbet")
        assert ev is not None
        assert ev.markets[0]["type"] == "1x2"
        assert {o["name"] for o in ev.markets[0]["outcomes"]} == {"home", "draw", "away"}

    def test_basketball_2way_moneyline_kept_alongside_skipped_3way(self):
        """When BOTH `basketball.moneyline` (real 2-way) and `basketball.1x2`
        (3-way) ship, only the 2-way survives. This is the actual production
        shape — Cloudbet ships both, and we must take the real one."""
        ev_data = {
            "id": "evt2",
            "status": "TRADING",
            "home": {"name": "Lakers"},
            "away": {"name": "Celtics"},
            "markets": {
                "basketball.moneyline": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "", "price": 4.80, "status": "SELECTION_ENABLED"},
                                {"outcome": "away", "params": "", "price": 1.03, "status": "SELECTION_ENABLED"},
                            ]
                        }
                    }
                },
                "basketball.1x2": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "", "price": 5.86, "status": "SELECTION_ENABLED"},
                                {"outcome": "draw", "params": "", "price": 18.02, "status": "SELECTION_ENABLED"},
                                {"outcome": "away", "params": "", "price": 1.08, "status": "SELECTION_ENABLED"},
                            ]
                        }
                    }
                },
            },
        }
        ev = parse_event(ev_data, "basketball", "cloudbet")
        assert ev is not None
        ml = [m for m in ev.markets if m["type"] == "moneyline"]
        assert len(ml) == 1
        odds = {o["name"]: o["odds"] for o in ml[0]["outcomes"]}
        # Real 2-way prices from basketball.moneyline — NOT the de-drawed 1x2.
        assert odds == {"home": 4.80, "away": 1.03}
        # The 3-way must not leak into any other market type either.
        assert all(m["type"] != "1x2" for m in ev.markets)


class TestCombatSportMoneyline:
    """Combat sports: moneyline comes ONLY from the 2-way `*.winner` market.

    Cloudbet exposes both a genuine 2-way `boxing.winner` ("To Win Fight")
    and a 3-way `boxing.1x2`. De-drawing the 3-way produces a mispriced
    moneyline (its draw is real + it carries its own vig), so it must be
    skipped — never folded into moneyline.
    """

    @staticmethod
    def _boxing_event(with_winner: bool):
        markets = {
            # 3-way — must be skipped for combat sports.
            "boxing.1x2": {
                "submarkets": {
                    "period=default": {
                        "selections": [
                            {"outcome": "home", "params": "", "price": 1.45, "status": "SELECTION_ENABLED"},
                            {"outcome": "draw", "params": "", "price": 17.12, "status": "SELECTION_ENABLED"},
                            {"outcome": "away", "params": "", "price": 3.0, "status": "SELECTION_ENABLED"},
                        ]
                    }
                }
            },
            "boxing.totals": {
                "submarkets": {
                    "period=default": {
                        "selections": [
                            {"outcome": "over", "params": "total=10.5", "price": 1.16, "status": "SELECTION_ENABLED"},
                            {"outcome": "under", "params": "total=10.5", "price": 5.07, "status": "SELECTION_ENABLED"},
                        ]
                    }
                }
            },
        }
        if with_winner:
            # Genuine 2-way moneyline ("To Win Fight").
            markets["boxing.winner"] = {
                "submarkets": {
                    "period=default": {
                        "selections": [
                            {"outcome": "home", "params": "", "price": 1.59, "status": "SELECTION_ENABLED"},
                            {"outcome": "away", "params": "", "price": 2.30, "status": "SELECTION_ENABLED"},
                        ]
                    }
                }
            }
        return {
            "id": 34577436,
            "status": "TRADING",
            "home": {"name": "Jack Catterall"},
            "away": {"name": "Shakhram Giyasov"},
            "markets": markets,
        }

    def test_3way_never_becomes_moneyline(self):
        # boxing.winner absent (disabled upstream) — the 3-way boxing.1x2 must
        # NOT be de-drawn into a fake moneyline. Event still extracts via totals.
        ev = parse_event(self._boxing_event(with_winner=False), "boxing", "cloudbet", "boxing", "boxing-intl")
        assert ev is not None
        types = {m["type"] for m in ev.markets}
        assert types == {"total"}, f"3-way leaked into {types}"

    def test_moneyline_comes_from_winner_market(self):
        ev = parse_event(self._boxing_event(with_winner=True), "boxing", "cloudbet", "boxing", "boxing-intl")
        assert ev is not None
        ml = [m for m in ev.markets if m["type"] == "moneyline"]
        assert len(ml) == 1
        odds = {o["name"]: o["odds"] for o in ml[0]["outcomes"]}
        # the real 2-way prices from boxing.winner — NOT the 3-way 1x2 (1.45 / 3.0)
        assert odds == {"home": 1.59, "away": 2.30}

    def test_mma_3way_also_skipped(self):
        ev = parse_event(self._boxing_event(with_winner=False), "mma", "cloudbet", "mma", "mma-ufc")
        assert ev is not None
        assert all(m["type"] != "moneyline" for m in ev.markets)


class TestEventUrl:
    """parse_event builds the playable web URL and stamps it on provider_meta.

    Cloudbet web event URL: /en/sports/{sportKey}/{competitionSlug}/{numericId}
    — the numeric `event.id`, Cloudbet's sport key (not our internal name),
    and the competition slug (affiliate key minus its "{sportKey}-" prefix).
    """

    @staticmethod
    def _event():
        return {
            "id": 34591796,
            "status": "TRADING",
            "name": "Jack Catterall v Shakhram Giyasov",
            "home": {"name": "Jack Catterall"},
            "away": {"name": "Shakhram Giyasov"},
            "startTime": "2026-05-23T19:00:00Z",
            "markets": {
                "boxing.winner": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "", "price": 1.40, "status": "SELECTION_ENABLED"},
                                {"outcome": "away", "params": "", "price": 3.00, "status": "SELECTION_ENABLED"},
                            ]
                        }
                    }
                }
            },
        }

    def test_url_built_from_sport_and_competition_keys(self):
        ev = parse_event(self._event(), "boxing", "cloudbet", "boxing", "boxing-international-matchups")
        assert ev is not None
        assert ev.url == "https://www.cloudbet.com/en/sports/boxing/international-matchups/34591796"

    def test_url_uses_cloudbet_sport_key_not_internal_name(self):
        # Internal sport "ice_hockey" → Cloudbet sport key "ice-hockey".
        ev = parse_event(self._event(), "ice_hockey", "cloudbet", "ice-hockey", "ice-hockey-nhl")
        assert ev is not None
        assert ev.url == "https://www.cloudbet.com/en/sports/ice-hockey/nhl/34591796"

    def test_event_url_stamped_on_every_outcome(self):
        ev = parse_event(self._event(), "boxing", "cloudbet", "boxing", "boxing-international-matchups")
        assert ev is not None
        expected = "https://www.cloudbet.com/en/sports/boxing/international-matchups/34591796"
        outcomes = [o for m in ev.markets for o in m["outcomes"]]
        assert outcomes
        for o in outcomes:
            assert o["provider_meta"]["cloudbet_event_url"] == expected

    def test_competition_key_without_sport_prefix_used_as_is(self):
        ev = parse_event(self._event(), "boxing", "cloudbet", "boxing", "international-matchups")
        assert ev is not None
        assert ev.url == "https://www.cloudbet.com/en/sports/boxing/international-matchups/34591796"

    def test_legacy_call_without_routing_keys_falls_back(self):
        # 3-arg call (unit tests / legacy) must not crash; URL degrades to the
        # sport landing page rather than a broken event link.
        ev = parse_event(self._event(), "boxing", "cloudbet")
        assert ev is not None
        assert ev.url == "https://www.cloudbet.com/en/sports/boxing"
