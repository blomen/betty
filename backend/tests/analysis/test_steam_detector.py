"""Tests for the steam-move detector.

Mix of:
  - Pure-function tests on env flag parsing.
  - DB-backed tests using an in-memory SQLite (so the detector's
    grouping/threshold logic gets real coverage without a Postgres).
  - Safety tests on `lookup_signal_for_outcome` (fail-open contract).
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.analysis.steam_detector import (
    _DEFAULT_DELTA_PP_MIN,
    _DEFAULT_MIN_PROVIDERS,
    _DEFAULT_WINDOW_MIN,
    delta_pp_threshold,
    detect_steam_moves,
    is_enabled,
    lookup_signal_for_outcome,
    min_providers,
    window_minutes,
)

# ─── Env-flag plumbing ────────────────────────────────────────────────


class TestEnvFlags:
    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        for k in ("STEAM_DETECTOR_ENABLED", "STEAM_WINDOW_MIN", "STEAM_MIN_PROVIDERS", "STEAM_DELTA_PP_MIN"):
            monkeypatch.delenv(k, raising=False)

    def test_disabled_by_default(self):
        assert is_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "yes", "YES"])
    def test_truthy_enables(self, monkeypatch, val):
        monkeypatch.setenv("STEAM_DETECTOR_ENABLED", val)
        assert is_enabled() is True

    def test_window_default(self):
        assert window_minutes() == _DEFAULT_WINDOW_MIN

    def test_window_override(self, monkeypatch):
        monkeypatch.setenv("STEAM_WINDOW_MIN", "10")
        assert window_minutes() == 10

    def test_window_out_of_range_falls_back(self, monkeypatch):
        # 0, negative, or >120 → default. Guards against typos that
        # would silently disable steam (set to 0) or query a huge
        # window (set to 9999).
        for bad in ("0", "-5", "999", "not-a-number"):
            monkeypatch.setenv("STEAM_WINDOW_MIN", bad)
            assert window_minutes() == _DEFAULT_WINDOW_MIN

    def test_min_providers_default(self):
        assert min_providers() == _DEFAULT_MIN_PROVIDERS

    def test_min_providers_override(self, monkeypatch):
        monkeypatch.setenv("STEAM_MIN_PROVIDERS", "5")
        assert min_providers() == 5

    def test_min_providers_floor_is_two(self, monkeypatch):
        # 1 provider can't be "steam" (no consensus); must fall back.
        monkeypatch.setenv("STEAM_MIN_PROVIDERS", "1")
        assert min_providers() == _DEFAULT_MIN_PROVIDERS

    def test_delta_threshold_default(self):
        assert delta_pp_threshold() == _DEFAULT_DELTA_PP_MIN

    def test_delta_threshold_override(self, monkeypatch):
        monkeypatch.setenv("STEAM_DELTA_PP_MIN", "1.0")
        assert delta_pp_threshold() == 1.0

    def test_delta_threshold_invalid_falls_back(self, monkeypatch):
        for bad in ("0", "-1", "999", "abc"):
            monkeypatch.setenv("STEAM_DELTA_PP_MIN", bad)
            assert delta_pp_threshold() == _DEFAULT_DELTA_PP_MIN


# ─── Hot-path lookup safety ────────────────────────────────────────────


class TestLookupSafety:
    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("STEAM_DETECTOR_ENABLED", raising=False)

    def test_disabled_returns_none(self):
        # No env flag → detector inactive → None regardless of args.
        assert lookup_signal_for_outcome(None, "evt", "moneyline", "home") is None

    def test_missing_keys_returns_none(self, monkeypatch):
        monkeypatch.setenv("STEAM_DETECTOR_ENABLED", "1")
        assert lookup_signal_for_outcome(None, None, "moneyline", "home") is None
        assert lookup_signal_for_outcome(None, "evt", None, "home") is None
        assert lookup_signal_for_outcome(None, "evt", "moneyline", None) is None

    def test_db_error_returns_none(self, monkeypatch):
        # A guard bug must NEVER block the scanner. Any exception in
        # detection → None.
        monkeypatch.setenv("STEAM_DETECTOR_ENABLED", "1")

        class _Bomb:
            def query(self, *_a, **_kw):
                raise RuntimeError("simulated DB failure")

        assert lookup_signal_for_outcome(_Bomb(), "evt", "moneyline", "home") is None


# ─── DB-backed detection ──────────────────────────────────────────────


@pytest.fixture
def in_memory_db():
    """In-memory SQLite session with the OddsMovement table created."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.db.models import Base, OddsMovement  # noqa: F401 — needed for create_all

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[OddsMovement.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _insert_movement(session, **kw):
    from src.db.models import OddsMovement

    defaults = {
        "scope": "ft",
        "point": None,
        "recorded_at": datetime.now(UTC),
    }
    defaults.update(kw)
    if "direction" not in defaults:
        defaults["direction"] = "up" if (defaults.get("delta_implied_pp") or 0) > 0 else "down"
    session.add(OddsMovement(**defaults))
    session.commit()


class TestDetectorAgainstDb:
    @pytest.fixture(autouse=True)
    def enable(self, monkeypatch):
        monkeypatch.setenv("STEAM_DETECTOR_ENABLED", "1")
        # Default thresholds (5 min window, 3 providers, 0.5pp delta)
        monkeypatch.delenv("STEAM_WINDOW_MIN", raising=False)
        monkeypatch.delenv("STEAM_MIN_PROVIDERS", raising=False)

    def test_empty_db_returns_nothing(self, in_memory_db):
        assert detect_steam_moves(in_memory_db) == []

    def test_below_min_providers_no_signal(self, in_memory_db):
        # 2 providers on same outcome — under default threshold of 3.
        for prov in ("pinnacle", "betsson"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
            )
        assert detect_steam_moves(in_memory_db) == []

    def test_three_providers_same_direction_detected(self, in_memory_db):
        for prov in ("pinnacle", "betsson", "unibet"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
            )
        signals = detect_steam_moves(in_memory_db)
        assert len(signals) == 1
        s = signals[0]
        assert s.event_id == "evt-1"
        assert s.market == "moneyline"
        assert s.outcome == "home"
        assert s.direction == "up"
        assert s.provider_count == 3
        assert set(s.providers) == {"pinnacle", "betsson", "unibet"}
        assert s.total_delta_pp == pytest.approx(12.0)

    def test_opposite_direction_movements_dont_combine(self, in_memory_db):
        # 2 providers moved up, 2 moved down — neither side has 3 votes.
        for prov in ("pinnacle", "betsson"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.9,
                delta_implied_pp=2.6,
                direction="up",
            )
        for prov in ("unibet", "tipwin"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=1.9,
                new_odds=2.0,
                delta_implied_pp=-2.6,
                direction="down",
            )
        assert detect_steam_moves(in_memory_db) == []

    def test_different_outcomes_not_combined(self, in_memory_db):
        # Steam on "home" with 2 providers, steam on "away" with 1 — neither hits threshold.
        for prov in ("pinnacle", "betsson"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
            )
        _insert_movement(
            in_memory_db,
            event_id="evt-1",
            provider_id="unibet",
            market="moneyline",
            outcome="away",
            prev_odds=2.0,
            new_odds=1.85,
            delta_implied_pp=4.0,
        )
        assert detect_steam_moves(in_memory_db) == []

    def test_same_provider_multiple_movements_dedup(self, in_memory_db):
        # Pinnacle moves 3 times in 5 min — that's still only ONE distinct
        # provider; should not single-handedly trip a 3-provider threshold.
        for _ in range(3):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
            )
        assert detect_steam_moves(in_memory_db) == []

    def test_old_movements_outside_window_ignored(self, in_memory_db):
        old = datetime.now(UTC) - timedelta(minutes=60)
        for prov in ("pinnacle", "betsson", "unibet"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
                recorded_at=old,
            )
        assert detect_steam_moves(in_memory_db) == []

    def test_signals_sorted_by_strength(self, in_memory_db):
        # Weak steam on evt-1 (3 providers, 1pp each), strong steam on
        # evt-2 (4 providers, 5pp each) — strong should come first.
        for prov in ("pinnacle", "betsson", "unibet"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.96,
                delta_implied_pp=1.0,
            )
        for prov in ("pinnacle", "betsson", "unibet", "tipwin"):
            _insert_movement(
                in_memory_db,
                event_id="evt-2",
                provider_id=prov,
                market="moneyline",
                outcome="away",
                prev_odds=2.0,
                new_odds=1.80,
                delta_implied_pp=5.5,
            )
        signals = detect_steam_moves(in_memory_db)
        assert [s.event_id for s in signals] == ["evt-2", "evt-1"]

    def test_disabled_flag_returns_empty_even_with_data(self, in_memory_db, monkeypatch):
        for prov in ("pinnacle", "betsson", "unibet"):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id=prov,
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
            )
        # Now disable — should return nothing even though data exists.
        monkeypatch.delenv("STEAM_DETECTOR_ENABLED", raising=False)
        assert detect_steam_moves(in_memory_db) == []


class TestPurgeOldMovements:
    @pytest.fixture(autouse=True)
    def enable(self, monkeypatch):
        monkeypatch.setenv("STEAM_DETECTOR_ENABLED", "1")

    def test_purge_removes_old_only(self, in_memory_db):
        from src.analysis.steam_detector import purge_old_movements
        from src.db.models import OddsMovement

        old = datetime.now(UTC) - timedelta(hours=48)
        fresh = datetime.now(UTC) - timedelta(minutes=5)
        for ts in (old, fresh):
            _insert_movement(
                in_memory_db,
                event_id="evt-1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="home",
                prev_odds=2.0,
                new_odds=1.85,
                delta_implied_pp=4.0,
                recorded_at=ts,
            )

        deleted = purge_old_movements(in_memory_db, retention_hours=24)
        in_memory_db.commit()
        remaining = in_memory_db.query(OddsMovement).all()

        assert deleted == 1
        assert len(remaining) == 1
        # SQLite round-trips datetimes as naive — strip tz on the stored
        # value so the comparison isn't testing the dialect.
        recorded = remaining[0].recorded_at
        if recorded.tzinfo is not None:
            recorded = recorded.replace(tzinfo=None)
        assert recorded >= datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
