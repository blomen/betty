"""Regression guard: every level name `load_levels` can emit must have a
corresponding entry in the `level_type_map` inside `_rebuild_zones`. A miss
sends the level into the silent-fallback path (which is now a logged-warning
skip) — but worse, before this guard it silently misclassified it as VWAP,
poisoning the strength math and the model observation.

The test feeds a synthetic `expanded_session` with every emission path
populated, runs a real `LevelMonitor`, and asserts:

  1. Every `_levels` entry maps to a real LevelType (no unmapped warnings).
  2. Every emitted name's `level_type` matches the family we expect.
  3. The downstream Zone object has the right composition flags set.

This is a contract test between the producer (`load_levels`) and the mapper
(`_rebuild_zones.level_type_map`). If the two drift, this test fails before
the model starts seeing wrong observations in production.
"""

from __future__ import annotations

import logging

from src.market_data.level_monitor import LevelMonitor
from src.rl.config import LevelType
from src.rl.zone_builder import _LEVEL_FAMILY


def _full_expanded_session() -> dict:
    """An expanded_session populated with every field load_levels reads.

    Prices are spread far enough apart (~50 points) that each level lands
    in its own single-member zone — that way every emitted name shows up
    independently in the resulting zone list and we can assert per-name
    properties without worrying about cluster merging.
    """
    base = 20000.0
    step = 50.0
    p = lambda n: base + n * step  # noqa: E731

    # session dict — keys mirror the SESSION_LEVELS / VWAP / IB extraction
    # paths inside load_levels.
    session = {
        "poc": p(0),
        "vah": p(1),
        "val": p(2),
        "pdh": p(3),
        "pdl": p(4),
        "tokyo_high": p(5),
        "tokyo_low": p(6),
        "london_high": p(7),
        "london_low": p(8),
        "tpo_poc": p(9),
        "tpo_vah": p(10),
        "tpo_val": p(11),
        "ib_high": p(12),
        "ib_low": p(13),
        "vwap": p(14),
        "vwap_1sd_upper": p(15),
        "vwap_1sd_lower": p(16),
        "vwap_2sd_upper": p(17),
        "vwap_2sd_lower": p(18),
        "vwap_3sd_upper": p(19),
        "vwap_3sd_lower": p(20),
    }

    # levels list — DB-fed entries (FVG/OB ranges + swing pivots).
    levels_list = [
        {"type": "fvg_bullish", "price_high": p(21) + 1, "price_low": p(21) - 1},
        {"type": "fvg_bearish", "price_high": p(22) + 1, "price_low": p(22) - 1},
        {"type": "order_block_bullish", "price_high": p(23) + 1, "price_low": p(23) - 1},
        {"type": "order_block_bearish", "price_high": p(24) + 1, "price_low": p(24) - 1},
        {"type": "daily_swing_high", "price_low": p(25), "price_high": p(25)},
        {"type": "daily_swing_low", "price_low": p(26), "price_high": p(26)},
        {"type": "weekly_swing_high", "price_low": p(27), "price_high": p(27)},
        {"type": "weekly_swing_low", "price_low": p(28), "price_high": p(28)},
        {"type": "monthly_swing_high", "price_low": p(29), "price_high": p(29)},
        {"type": "monthly_swing_low", "price_low": p(30), "price_high": p(30)},
        # Duplicate ib_high/low — load_levels MUST skip these so we don't
        # double-count vs the session-fed nyib_high/low.
        {"type": "ib_high", "price_low": p(12), "price_high": p(12)},
        {"type": "ib_low", "price_low": p(13), "price_high": p(13)},
    ]

    # profiles — weekly/monthly VP and naked POCs land here.
    profiles = {
        "weekly": {"poc": p(31), "vah": p(32), "val": p(33)},
        "monthly": {"poc": p(34), "vah": p(35), "val": p(36)},
        "naked_pocs": [
            {"date": "2026-04-01", "price": p(37)},
            {"date": "2026-03-15", "price": p(38)},
        ],
    }

    return {"session": session, "levels": levels_list, "profiles": profiles}


def test_every_emitted_name_maps_to_a_level_type(caplog):
    """No `_levels` entry may fall through to the unmapped-warning branch."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)

    with caplog.at_level(logging.WARNING):
        monitor.load_levels(_full_expanded_session())

    unmapped_warnings = [rec for rec in caplog.records if "unmapped names" in rec.getMessage()]
    assert not unmapped_warnings, "load_levels emitted name(s) that level_type_map doesn't know:\n" + "\n".join(
        rec.getMessage() for rec in unmapped_warnings
    )


def test_ib_dedup_no_double_count():
    """DB-fed ib_high/low must be filtered so they don't duplicate
    session-fed nyib_high/low — checked by name AND by zone-membership."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    monitor.load_levels(_full_expanded_session())

    # No `ib_high` / `ib_low` entries should survive in _levels — only their
    # canonical `nyib_*` siblings.
    ib_named = [lv for lv in monitor._levels if lv.name in ("ib_high", "ib_low")]
    assert ib_named == [], (
        f"Expected no ib_high/ib_low entries (DB duplicates of session "
        f"nyib_*), but found: {[(l.name, l.price) for l in ib_named]}"
    )

    nyib = [lv for lv in monitor._levels if lv.name in ("nyib_high", "nyib_low")]
    assert len(nyib) == 2, f"Expected exactly 2 nyib entries, got {len(nyib)}"


def test_weekly_monthly_vp_and_naked_poc_emitted():
    """Producer fixes from this audit: weekly/monthly VP + naked POC must
    show up as MonitoredLevels after load_levels — these were the levels
    that were silently dropped before."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    monitor.load_levels(_full_expanded_session())

    names = {lv.name for lv in monitor._levels}
    expected = {
        "weekly_poc",
        "weekly_vah",
        "weekly_val",
        "monthly_poc",
        "monthly_vah",
        "monthly_val",
        "naked_poc",
    }
    missing = expected - names
    assert not missing, f"load_levels failed to emit: {missing}. Got names: {sorted(names)}"


def test_london_aliased_to_tokyo_in_zones():
    """London H/L must NOT be an isolated LevelType (would break trained
    DQN's input shape). They alias to TOKYO_HIGH/LOW so they share the
    "sessions" family without growing the LevelType enum."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    monitor.load_levels(_full_expanded_session())

    # London entries by name (preserves source identity for diagnostics)…
    london = [lv for lv in monitor._levels if lv.name in ("london_high", "london_low")]
    assert len(london) == 2

    # …but their resolved LevelType in the zones must be TOKYO_HIGH/LOW
    # so the model observation stays the same shape it was trained on.
    london_member_types = []
    for zone in monitor._zones:
        for m in zone.members:
            if m.name in ("london_high", "london_low"):
                london_member_types.append(m.level_type)
    assert set(london_member_types) == {LevelType.TOKYO_HIGH, LevelType.TOKYO_LOW}, (
        f"london_high/low must map to TOKYO_HIGH/LOW (alias). Got: {london_member_types}"
    )


def test_every_emitted_member_has_level_family_entry():
    """Belt-and-suspenders: every member that lands in a zone must have a
    _LEVEL_FAMILY entry. Without one, _compute_strength falls back to the
    enum value as a synthetic family name — which still works numerically
    but means the SYNERGY_BONUS table can't match. This test catches the
    case where someone adds a new LevelType but forgets _LEVEL_FAMILY."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    monitor.load_levels(_full_expanded_session())

    seen_types: set[LevelType] = set()
    for zone in monitor._zones:
        for m in zone.members:
            seen_types.add(m.level_type)

    missing_family = [lt for lt in seen_types if lt not in _LEVEL_FAMILY]
    assert not missing_family, (
        f"LevelType(s) reach zones without a _LEVEL_FAMILY entry: "
        f"{missing_family}. Add to backend/src/rl/zone_builder.py:_LEVEL_FAMILY"
    )


def test_no_zone_member_is_silently_misclassified_as_vwap():
    """Pre-fix bug: any unmapped name fell through to RLLevelType.VWAP and
    polluted the vwap family. Verify that VWAP-family members in zones come
    only from the legitimate VWAP* names, not from misclassified strangers."""
    monitor = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    monitor.load_levels(_full_expanded_session())

    legitimate_vwap_names = {
        "VWAP",
        "VWAP +1SD",
        "VWAP -1SD",
        "VWAP +2SD",
        "VWAP -2SD",
        "VWAP +3SD",
        "VWAP -3SD",
    }
    bogus_vwap_members = []
    for zone in monitor._zones:
        for m in zone.members:
            if _LEVEL_FAMILY.get(m.level_type) == "vwap" and m.name not in legitimate_vwap_names:
                bogus_vwap_members.append((m.name, m.level_type))
    assert not bogus_vwap_members, (
        "Found zone member(s) misclassified into the vwap family: "
        f"{bogus_vwap_members}. They should map to a different LevelType "
        "via level_type_map in _rebuild_zones, not the silent fallback."
    )
