"""Unit tests for soft-consensus lean.

Pure-function tests on `compute_consensus_lean` — verifies the lean
labels are assigned correctly across the four scenarios (sharp_value,
stale_outlier, market_lag, None when insufficient data), that sharp
providers are excluded from the consensus, and that the betting
provider's own odds don't anchor the verdict.
"""

import pytest

from src.analysis.consensus_lean import (
    NEUTRAL_BAND_PP,
    TYPICAL_SOFT_VIG_PP,
    compute_consensus_lean,
)


def _odds(provider: str, decimal: float) -> dict:
    return {"provider": provider, "odds": decimal}


class TestInsufficientData:
    def test_empty_snapshot_returns_none(self):
        assert compute_consensus_lean(None, 0.5) is None
        assert compute_consensus_lean([], 0.5) is None

    def test_invalid_sharp_prob_returns_none(self):
        snap = [_odds("betsson", 2.0)] * 5
        assert compute_consensus_lean(snap, 0.0) is None
        assert compute_consensus_lean(snap, 1.0) is None
        assert compute_consensus_lean(snap, -0.1) is None

    def test_too_few_soft_books_returns_none(self):
        # 2 soft books → below MIN_SOFT_BOOKS (3) → no verdict.
        snap = [_odds("betsson", 2.0), _odds("unibet", 2.0)]
        assert compute_consensus_lean(snap, 0.5) is None

    def test_below_min_after_excluding_sharps(self):
        # 4 entries total but 2 are sharp → 2 soft → not enough.
        snap = [
            _odds("pinnacle", 2.0),
            _odds("cloudbet", 2.0),
            _odds("betsson", 2.0),
            _odds("unibet", 2.0),
        ]
        assert compute_consensus_lean(snap, 0.5) is None


class TestSharpExclusion:
    @pytest.mark.parametrize("sharp", ["pinnacle", "polymarket", "kalshi", "cloudbet"])
    def test_sharp_provider_excluded_from_consensus(self, sharp):
        # If pinnacle weren't excluded, its odds would skew the median.
        # Sharps don't shade for public, so they don't belong in the
        # "what does the public-priced consensus think?" calculation.
        soft_odds = [_odds(f"soft_{i}", 1.90) for i in range(3)]
        snap = soft_odds + [_odds(sharp, 5.0)]
        result = compute_consensus_lean(snap, 0.5)
        assert result is not None
        # Median over only the 3 soft books at 1.90 → 1/1.90 ≈ 52.6pp.
        # If sharp were included the median would shift dramatically.
        assert result.soft_consensus_pp == pytest.approx(100 / 1.90, rel=0.01)
        assert result.n_soft_books == 3


class TestBetProviderExclusion:
    def test_bet_provider_excluded_so_value_book_doesnt_anchor(self):
        # The book we're betting at has a deliberately generous outlier
        # price; the consensus should reflect the OTHER soft books only.
        snap = [
            _odds("betsson", 2.40),  # generous outlier (the value bet)
            _odds("unibet", 1.95),
            _odds("tipwin", 1.95),
            _odds("vbet", 1.95),
        ]
        result = compute_consensus_lean(snap, sharp_fair_probability=0.5, bet_provider="betsson")
        assert result is not None
        # Consensus should be median of (unibet, tipwin, vbet) = 1/1.95.
        assert result.soft_consensus_pp == pytest.approx(100 / 1.95, rel=0.01)
        assert result.n_soft_books == 3

    def test_bet_provider_none_includes_all_softs(self):
        snap = [_odds(f"soft_{i}", 2.00) for i in range(4)]
        result = compute_consensus_lean(snap, sharp_fair_probability=0.5)
        assert result is not None
        assert result.n_soft_books == 4


class TestLeanLabels:
    """Verify the four-state classification."""

    def _build_softs(self, soft_implied_pp: float, count: int = 4) -> list[dict]:
        odds = 100.0 / soft_implied_pp
        return [_odds(f"soft_{i}", odds) for i in range(count)]

    def test_market_lag_when_consensus_matches_sharp(self):
        # Soft consensus pp ≈ sharp pp + TYPICAL_SOFT_VIG_PP — adjusted
        # divergence near zero → market_lag (just one slow book).
        sharp_pp = 50.0
        soft_pp = sharp_pp + TYPICAL_SOFT_VIG_PP  # exactly compensated
        snap = self._build_softs(soft_pp)
        result = compute_consensus_lean(snap, sharp_fair_probability=sharp_pp / 100)
        assert result is not None
        assert abs(result.divergence_pp) <= NEUTRAL_BAND_PP
        assert result.lean == "market_lag"

    def test_sharp_value_when_softs_say_less_likely(self):
        # Adjusted divergence well below -NEUTRAL_BAND_PP → public not
        # on this side, we're with the sharps → sharp_value.
        sharp_pp = 50.0
        soft_pp = sharp_pp - 5.0  # softs price MUCH less likely than fair
        snap = self._build_softs(soft_pp)
        result = compute_consensus_lean(snap, sharp_fair_probability=sharp_pp / 100)
        assert result is not None
        # After vig adjustment: (45 - 50) - 2 = -7pp → sharp_value
        assert result.divergence_pp < -NEUTRAL_BAND_PP
        assert result.lean == "sharp_value"

    def test_stale_outlier_when_softs_say_more_likely(self):
        # Softs say outcome is MORE likely than sharp fair → public
        # loaded this side → my book is a stale outlier likely to move.
        sharp_pp = 50.0
        soft_pp = sharp_pp + 8.0  # softs cluster well above fair
        snap = self._build_softs(soft_pp)
        result = compute_consensus_lean(snap, sharp_fair_probability=sharp_pp / 100)
        assert result is not None
        # After vig adjustment: (58 - 50) - 2 = +6pp → stale_outlier
        assert result.divergence_pp > NEUTRAL_BAND_PP
        assert result.lean == "stale_outlier"

    def test_neutral_band_boundaries(self):
        # Exactly NEUTRAL_BAND_PP after adjustment → still market_lag.
        sharp_pp = 50.0
        # Want adjusted divergence = +NEUTRAL_BAND_PP exactly.
        soft_pp = sharp_pp + TYPICAL_SOFT_VIG_PP + NEUTRAL_BAND_PP
        snap = self._build_softs(soft_pp)
        result = compute_consensus_lean(snap, sharp_fair_probability=sharp_pp / 100)
        assert result is not None
        assert result.lean == "market_lag"


class TestRobustness:
    def test_invalid_odds_filtered(self):
        # odds <= 1.0 are nonsense (no positive return). Filter them out
        # of the consensus rather than blowing up on division.
        snap = [
            _odds("betsson", 1.95),
            _odds("unibet", 1.95),
            _odds("tipwin", 1.95),
            _odds("broken1", 0.0),
            _odds("broken2", 1.0),
            {"provider": "no_odds_key"},  # malformed entry
            "not a dict",
        ]
        result = compute_consensus_lean(snap, 0.5)
        assert result is not None
        assert result.n_soft_books == 3

    def test_to_dict_shape(self):
        snap = [_odds(f"soft_{i}", 2.0) for i in range(4)]
        result = compute_consensus_lean(snap, 0.5)
        assert result is not None
        d = result.to_dict()
        assert set(d.keys()) == {
            "soft_consensus_pp",
            "sharp_pp",
            "divergence_pp",
            "lean",
            "n_soft_books",
        }
        assert d["lean"] in ("sharp_value", "stale_outlier", "market_lag")
        assert isinstance(d["n_soft_books"], int)

    def test_min_books_param_override(self):
        # Caller can override the default minimum (e.g. for tests/dev).
        snap = [_odds("betsson", 2.0), _odds("unibet", 2.0)]
        # Default MIN_SOFT_BOOKS=3 → None.
        assert compute_consensus_lean(snap, 0.5) is None
        # Override to 2 → returns a result.
        result = compute_consensus_lean(snap, 0.5, min_books=2)
        assert result is not None
        assert result.n_soft_books == 2


class TestValueBetIntegration:
    def test_value_bet_accepts_consensus_lean_field(self):
        from src.analysis.value import ValueBet

        vb = ValueBet(
            event_id="evt-1",
            market="moneyline",
            outcome="home",
            provider="betsson",
            provider_odds=2.10,
            fair_odds=1.95,
            fair_probability=0.51,
            edge_pct=7.7,
            consensus_lean={
                "lean": "sharp_value",
                "divergence_pp": -3.5,
                "n_soft_books": 5,
                "soft_consensus_pp": 47.5,
                "sharp_pp": 51.0,
            },
        )
        assert vb.consensus_lean is not None
        assert vb.consensus_lean["lean"] == "sharp_value"
