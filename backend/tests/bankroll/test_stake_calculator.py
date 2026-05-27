"""Regression tests for provider_min_stake_sek.

The function must honor PROVIDER_STAKE_PROFILES[provider].currency, NOT the
wallet's exchange_rate. The cloudbet floor bug landed because the function
multiplied a SEK-denominated minimum by the wallet's USDC rate (10.5),
producing a floor that equalled the entire cloudbet bankroll.
"""

from src.bankroll.stake_calculator import provider_min_stake_sek


class TestProviderMinStakeSek:
    def test_sek_profile_pinnacle_returns_native_unchanged(self):
        # Pinnacle: profile SEK 20, wallet SEK rate 1.0 → 20 SEK.
        assert provider_min_stake_sek("pinnacle", exchange_rate=1.0, fallback=5.0) == 20.0

    def test_sek_profile_ignores_non_sek_wallet_rate(self):
        # Cloudbet regression: profile says SEK 20 ("20 kr click overhead").
        # Wallet is USDC so caller passes rate 10.5. Without the SEK branch
        # this returned 210, which == the entire cloudbet bankroll → every
        # Kelly stake floored to full balance. Must stay 20.
        assert provider_min_stake_sek("cloudbet", exchange_rate=10.5, fallback=5.0) == 20.0

    def test_usdc_profile_polymarket_multiplies_by_rate(self):
        # Polymarket: profile USDC 1.0 → 1.0 × 10.5 = 10.5 SEK.
        assert provider_min_stake_sek("polymarket", exchange_rate=10.5, fallback=5.0) == 10.5

    def test_usd_profile_kalshi_multiplies_by_rate(self):
        # Kalshi: profile USD 1.0 → 1.0 × 10.5 = 10.5 SEK.
        assert provider_min_stake_sek("kalshi", exchange_rate=10.5, fallback=5.0) == 10.5

    def test_unknown_provider_returns_fallback(self):
        assert provider_min_stake_sek("nonexistent", exchange_rate=10.5, fallback=42.0) == 42.0

    def test_zero_exchange_rate_treated_as_one(self):
        # Guard: a zero rate degenerates to 1.0 so we never floor to 0.
        # (Applies only to non-SEK profiles; SEK profiles ignore rate entirely.)
        assert provider_min_stake_sek("polymarket", exchange_rate=0.0, fallback=5.0) == 1.0
