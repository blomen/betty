"""Regression test for per-provider staleness gating.

The old global 2 h gate let a soft provider that DROPPED an event keep its
stale row in the DB for up to two hours, where the scanner happily paired
it against fresh Pinnacle and surfaced a ghost arb. The fix is per-provider
staleness windows tied to each provider's extraction cadence
(``constants.staleness_minutes_for``).

These tests pin the contract:
- canonical providers map to their declared cadence
- non-canonical cluster members resolve via PROVIDER_CANONICAL
- unknown providers fall back to the legacy 120 min cap
- the floor prevents 1-min-cadence providers from getting a 6-min gate
"""

from src.constants import (
    PROVIDER_EXTRACTION_INTERVAL_MINUTES,
    staleness_minutes_for,
)


def test_sharp_provider_floor():
    # Pinnacle interval is 1 min — without the floor we'd get 6 min, which
    # would flap on any single missed cycle. The floor keeps it at 15 min.
    assert staleness_minutes_for("pinnacle") == 15


def test_api_soft_catches_dropped_event():
    # The user's reported bug: Lodur (Altenar cluster) dropped a tennis event
    # ~1.5 h before its scheduled start; the betinia row stayed in the DB and
    # got paired against fresh Pinnacle as a phantom +2.75 % arb. With a
    # 3-min cadence × 6 cycles = 18 min staleness, a 1.5 h row is now dropped
    # well before it can ghost-arb.
    assert staleness_minutes_for("betinia") == 18
    # lodur fans out from betinia's extraction, so it shares the cadence.
    assert staleness_minutes_for("lodur") == 18
    # quickcasino, dbet, swiper, campobet — same Altenar canonical.
    assert staleness_minutes_for("quickcasino") == 18


def test_kambi_cluster_resolves_to_unibet_cadence():
    # All Kambi members fan out from unibet's 3-min extraction.
    assert staleness_minutes_for("unibet") == 18
    assert staleness_minutes_for("leovegas") == 18
    assert staleness_minutes_for("expekt") == 18


def test_gecko_cluster_resolves_to_spelklubben():
    # spelklubben canonical, OBG members fan out.
    assert staleness_minutes_for("spelklubben") == 18
    assert staleness_minutes_for("bethard") == 18
    assert staleness_minutes_for("betsson") == 18
    assert staleness_minutes_for("nordicbet") == 18


def test_browser_providers_get_generous_windows():
    # browser_soft (45-min cadence) — a row up to 4.5 h old is still in-cycle.
    assert staleness_minutes_for("888sport") == 270
    assert staleness_minutes_for("tipwin") == 270
    # browser_slow (60-min) — demoted providers.
    assert staleness_minutes_for("10bet") == 360
    assert staleness_minutes_for("coolbet") == 360
    # browser_antibot (25-min) — comeon + rainbet.
    assert staleness_minutes_for("comeon") == 150
    assert staleness_minutes_for("rainbet") == 150


def test_unknown_provider_falls_back_to_legacy_cap():
    # Anything not in PROVIDER_EXTRACTION_INTERVAL_MINUTES gets the old 120-min
    # global cap. Preserves prior behaviour for forgotten/future providers.
    assert staleness_minutes_for("interwetten") == 120
    assert staleness_minutes_for("totally_made_up") == 120


def test_signal_providers_match_their_tier():
    # signal_international: 5-min cadence × 6 = 30 min.
    assert staleness_minutes_for("marathon") == 30
    assert staleness_minutes_for("smarkets") == 30
    assert staleness_minutes_for("stake") == 30


def test_polymarket_and_kalshi_match_their_intervals():
    # polymarket is 10-min cadence × 6 = 60 min.
    assert staleness_minutes_for("polymarket") == 60
    # kalshi is 5-min × 6 = 30 min.
    assert staleness_minutes_for("kalshi") == 30
    # cloudbet is 5-min × 6 = 30 min.
    assert staleness_minutes_for("cloudbet") == 30


def test_every_listed_provider_returns_at_least_the_floor():
    # Sanity: no entry should produce a value below the floor.
    for pid in PROVIDER_EXTRACTION_INTERVAL_MINUTES:
        assert staleness_minutes_for(pid) >= 15, pid
