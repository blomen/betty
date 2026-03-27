"""Market service - orchestrates data fetch, AMT analysis, and scanning."""

import asyncio
import json
import logging
import os
from datetime import datetime, date, timedelta, timezone

from sqlalchemy.orm import Session

from ..config.trading_loader import get_market_data_config, get_scanner_config, get_setups
from ..db.models import TradingSignal
from ..market_data.amt import build_session_analysis, SessionAnalysis
from ..market_data.base import MarketDataProvider
from ..market_data.levels import (
    compute_session_levels, compute_volume_profile, compute_vwap_bands,
    bars_to_trades, compute_volume_profile_from_bars, VolumeProfile, VWAPBands, SessionLevels,
    _TOKYO_START, _TOKYO_END, _LONDON_START, _LONDON_END,
    _NY_START, _NY_END, _IB_END,
)
from ..market_data.macro_provider import fetch_macro_snapshot
from ..market_data.metrics import compute_rotation_factor, compute_aspr, compute_aspr_percentile, detect_value_migration
from ..market_data.orderflow import build_candle_flow, compute_signals
from ..market_data.scanner import MarketScanner
from ..market_data.scoring import score_candidate, day_type_fits_setup, filter_by_rr
from ..market_data.setups.detector import DetectorContext, run_all_detectors
from ..market_data.tpo import build_full_tpo_profile, aggregate_bars_30m, compute_session_tpos
from ..repositories.market_repo import MarketRepo

logger = logging.getLogger(__name__)

# Singleton provider instance
_provider: MarketDataProvider | None = None


def _get_provider() -> MarketDataProvider:
    """Lazy-init the market data provider."""
    global _provider
    if _provider is not None:
        return _provider

    config = get_market_data_config()
    provider_type = config.get("provider", "databento")

    if provider_type == "databento":
        from ..market_data.databento_provider import DabentoProvider
        from ..market_data.cache import CachedMarketDataProvider
        from ..paths import get_app_data_dir

        inner = DabentoProvider(config)
        cache_dir = get_app_data_dir() / "data" / config.get("cache_dir", "market_cache")
        _provider = CachedMarketDataProvider(inner, cache_dir)
    else:
        raise ValueError(f"Unknown market data provider: {provider_type}")

    return _provider


class MarketService:
    """Orchestrates market data, AMT analysis, and scanning."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = MarketRepo(db)

    @staticmethod
    def _is_globex_closed(dt: datetime | None = None) -> bool:
        """Check if CME Globex is closed (weekend gap).

        Globex schedule: Sun 18:00 ET → Fri 17:00 ET (with daily 17:00-18:00 halt).
        Weekend close: Fri 17:00 ET → Sun 18:00 ET.
        """
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = dt or datetime.now(et)
        if now.tzinfo is None:
            now = now.replace(tzinfo=et)
        else:
            now = now.astimezone(et)
        wd = now.weekday()  # Mon=0 … Sun=6
        hour = now.hour
        # Saturday: always closed
        if wd == 5:
            return True
        # Friday after 17:00: closed
        if wd == 4 and hour >= 17:
            return True
        # Sunday before 18:00: closed
        if wd == 6 and hour < 18:
            return True
        return False

    @staticmethod
    def _filter_halt(rows: list) -> list:
        """Remove rows during the daily CME halt (17:00-18:00 ET / 22:00-23:00 CET)."""
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("US/Eastern")
        filtered = []
        for r in rows:
            ts = r.ts if hasattr(r, 'ts') else None
            if ts is None:
                filtered.append(r)
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.astimezone(_ET).hour == 17:
                continue
            filtered.append(r)
        return filtered

    async def _get_session_bars(self, symbol: str) -> list[dict]:
        """Get today's complete 1m bars for VP. DB first, backfill from Databento if gaps.

        Anchored from 00:00 CET (midnight Stockholm time). A full session
        (Tokyo + London + NY ≈ 22h) should have ~1300 1m bars. If DB has < 70%
        coverage, fetches from Databento and persists to DB so it only happens once.
        """
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        # Start from 00:00 CET today
        today_cet = now.astimezone(_CET).date()
        d_start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)
        d_end = now

        # Expected bars: minutes from session start to now minus ~60 min halt
        elapsed_minutes = max(1, int((d_end - d_start).total_seconds() / 60))
        expected_bars = max(1, elapsed_minutes - 60)

        # Try DB first (filter out 17:00-18:00 ET halt)
        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", d_start, d_end))
        db_bars = [{"high": r.h, "low": r.l, "close": r.c, "volume": r.v} for r in rows]
        coverage = len(db_bars) / expected_bars if expected_bars > 0 else 0

        if coverage >= 0.70:
            logger.info("VP bars: %d from DB (%.0f%% coverage)", len(db_bars), coverage * 100)
            return db_bars

        # DB has gaps — try Databento backfill with its own timeout
        logger.info("VP bars: DB has %d/~%d (%.0f%%) — backfilling from Databento",
                     len(db_bars), expected_bars, coverage * 100)
        try:
            provider = _get_provider()
            # Use inner (uncached) provider to avoid parquet cache date mismatch
            raw_provider = getattr(provider, "inner", provider)
            config = get_market_data_config()
            full_symbol = config.get("symbol", "NQ.FUT")
            # Databento historical has ~15 min delay; clamp end to avoid 422
            fetch_end = min(d_end, datetime.now(timezone.utc) - timedelta(minutes=15))
            if fetch_end <= d_start:
                logger.info("VP bars: too early for Databento backfill, using DB bars")
            else:
                fetched = await asyncio.wait_for(
                    raw_provider.get_bars(full_symbol, "1m", d_start, fetch_end),
                    timeout=25.0,
                )
                if fetched:
                    try:
                        inserted = self.repo.bulk_insert_candles(symbol, "1m", fetched)
                        logger.info("VP bars: got %d from Databento, inserted %d new into DB", len(fetched), inserted)
                    except Exception as e:
                        self.db.rollback()
                        logger.warning("VP bars: DB insert failed (OK, using fetched data): %s", e)
                    return [{"high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                            for b in fetched]
        except asyncio.TimeoutError:
            logger.warning("Databento backfill timed out (25s)")
        except Exception as e:
            logger.warning("Databento backfill failed: %s", e)

        if db_bars:
            return db_bars

        # No bars for today — fall back to previous CET day so VP shows
        # recent data rather than stale DB session values.
        prev_cet = today_cet - timedelta(days=1)
        p_start = datetime(prev_cet.year, prev_cet.month, prev_cet.day, tzinfo=_CET).astimezone(timezone.utc)
        prev_rows = self._filter_halt(self.repo.get_candles(symbol, "1m", p_start, d_start))
        if prev_rows:
            logger.info("VP bars: using previous day's %d bars as fallback", len(prev_rows))
            return [{"high": r.h, "low": r.l, "close": r.c, "volume": r.v} for r in prev_rows]

        return db_bars

    async def compute_session(
        self, target_date: str | None = None, symbol: str | None = None
    ) -> dict:
        """Fetch market data → run AMT analysis → persist to DB."""
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        target_date = target_date or date.today().isoformat()

        # Skip Databento fetch during weekend close — serve cached session
        if not target_date or target_date == date.today().isoformat():
            if self._is_globex_closed():
                logger.info("Globex closed (weekend) — serving cached session for %s", symbol)
                cached = self.repo.get_previous_session(symbol)
                if cached:
                    return cached
                return {}

        sessions_cfg = config.get("sessions", {})
        rth_open = sessions_cfg.get("rth_open", "09:30")

        provider = _get_provider()

        # Parse target date and build datetime range
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        # Globex opens previous day 18:00 ET
        globex_start = datetime.combine(
            dt - timedelta(days=1),
            datetime.strptime(sessions_cfg.get("globex_open", "18:00"), "%H:%M").time()
        )
        rth_close = datetime.combine(
            dt,
            datetime.strptime(sessions_cfg.get("rth_close", "16:00"), "%H:%M").time()
        )

        # Fetch current day data (may fail on weekends / no data available)
        sym = config.get("symbol", "NQ.FUT")
        try:
            bars = await provider.get_bars(sym, "1m", globex_start, rth_close)
        except Exception as e:
            logger.debug("get_bars failed (likely weekend/no data): %s", e)
            bars = []
        try:
            ticks = await provider.get_ticks(sym, globex_start, rth_close)
        except Exception as e:
            logger.debug("get_ticks failed (likely weekend/no data): %s", e)
            ticks = []

        if not bars:
            logger.info("No bars available for %s on %s — returning cached/empty session", symbol, target_date)
            cached = self.repo.get_previous_session(symbol)
            if cached:
                return cached
            return {}

        # Fetch previous day for prev VA
        prev_dt = dt - timedelta(days=1)
        prev_globex = datetime.combine(
            prev_dt - timedelta(days=1),
            datetime.strptime(sessions_cfg.get("globex_open", "18:00"), "%H:%M").time()
        )
        prev_close = datetime.combine(
            prev_dt,
            datetime.strptime(sessions_cfg.get("rth_close", "16:00"), "%H:%M").time()
        )
        try:
            prev_bars = await provider.get_bars(sym, "1m", prev_globex, prev_close)
        except Exception as e:
            logger.debug("get_bars (prev day) failed: %s", e)
            prev_bars = []

        # Fetch macro data (VIX, DXY, yields)
        try:
            macro = await fetch_macro_snapshot()
            logger.info("Macro: VIX=%.1f (%s), regime=%s (%.2f)",
                        macro.vix or 0, macro.vix_change_pct, macro.regime, macro.regime_score)
        except Exception as e:
            logger.warning("Macro fetch failed: %s", e)
            macro = None

        # Build analysis
        tick_size = 0.25  # NQ tick size
        analysis = build_session_analysis(
            bars=bars,
            ticks=ticks,
            prev_bars=prev_bars,
            symbol=symbol,
            date_str=target_date,
            rth_open=rth_open,
            tick_size=tick_size,
        )
        analysis.macro = macro

        # Persist to DB
        session_data = analysis.to_dict()

        # --- New: Session levels from 1-min bars ---
        # Combine prev_bars (yesterday's RTH) + today's bars so compute_session_levels
        # can find PDH/PDL (yesterday's 09:30-16:00) and Tokyo (yesterday 20:00 - today 02:00)
        all_bars_for_levels = []
        if prev_bars:
            all_bars_for_levels.extend(
                {"ts": b.timestamp, "high": b.high, "low": b.low} for b in prev_bars
            )
        all_bars_for_levels.extend(
            {"ts": b.timestamp, "high": b.high, "low": b.low} for b in bars
        )
        # Use ET noon so .astimezone(ET).date() gives the correct calendar day
        # (UTC midnight would become previous day in ET due to UTC-4/5 offset)
        from zoneinfo import ZoneInfo as _ZI
        _et = _ZI("US/Eastern")
        _dt_parsed = datetime.strptime(target_date, "%Y-%m-%d")
        session_date = _dt_parsed.replace(hour=12, tzinfo=_et)
        session_levels = compute_session_levels(all_bars_for_levels, session_date)

        # --- New: Aggregate to 30-min bars for TPO and metrics ---
        bars_30m = self._aggregate_bars_30m(bars)

        # TPO profile from 30-min bars
        tpo = build_full_tpo_profile(bars_30m)

        # Per-session TPO profiles — need timestamped 30m bars
        # bars (BarData objects) have .timestamp; aggregate them with timestamps
        bars_30m_ts = []
        chunk = []
        for b in bars:
            chunk.append(b)
            if len(chunk) == 30:
                bars_30m_ts.append({
                    "ts": chunk[0].timestamp,
                    "high": max(c.high for c in chunk),
                    "low": min(c.low for c in chunk),
                    "open": chunk[0].open,
                    "close": chunk[-1].close,
                    "volume": sum(c.volume for c in chunk),
                })
                chunk = []
        session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=tick_size)

        from dataclasses import asdict as _asdict
        session_data["session_tpos"] = _asdict(session_tpo_set) if session_tpo_set else None

        try:
            self.store_tpo_session(tpo, symbol, target_date)
        except Exception:
            logger.warning("Failed to store TPO session for %s/%s", symbol, target_date, exc_info=True)

        # Session metrics: RF from 30-min highs/lows
        highs_30m = [b["high"] for b in bars_30m]
        lows_30m = [b["low"] for b in bars_30m]
        rf = compute_rotation_factor(highs_30m, lows_30m)
        ranges_30m = [b["high"] - b["low"] for b in bars_30m]
        aspr = compute_aspr(ranges_30m)
        historical = self.repo.get_historical_asprs(symbol)
        aspr_pct = compute_aspr_percentile(aspr, historical)

        # Value migration vs prior session
        value_migration = None
        prev_session = self.repo.get_previous_session(symbol, before_date=target_date)
        if (prev_session and prev_session.vah and prev_session.val
                and session_data.get("vah") and session_data.get("val")):
            value_migration = detect_value_migration(
                session_data["vah"], session_data["val"],
                prev_session.vah, prev_session.val,
            )

        self.repo.upsert_session(
            date=target_date,
            symbol=symbol,
            poc=session_data.get("poc"),
            vah=session_data.get("vah"),
            val=session_data.get("val"),
            vwap=session_data.get("vwap"),
            vwap_1sd_upper=session_data.get("vwap_1sd_upper"),
            vwap_1sd_lower=session_data.get("vwap_1sd_lower"),
            vwap_2sd_upper=session_data.get("vwap_2sd_upper"),
            vwap_2sd_lower=session_data.get("vwap_2sd_lower"),
            vwap_3sd_upper=session_data.get("vwap_3sd_upper"),
            vwap_3sd_lower=session_data.get("vwap_3sd_lower"),
            ib_high=session_data.get("ib_high"),
            ib_low=session_data.get("ib_low"),
            ib_range=session_data.get("ib_range"),
            overnight_high=session_data.get("overnight_high"),
            overnight_low=session_data.get("overnight_low"),
            total_delta=session_data.get("total_delta"),
            delta_divergence=session_data.get("delta_divergence"),
            market_type=session_data.get("market_type"),
            opening_type=session_data.get("opening_type"),
            poor_high=session_data.get("poor_high"),
            poor_low=session_data.get("poor_low"),
            session_json=session_data,
            # New fields
            pdh=session_levels.pdh,
            pdl=session_levels.pdl,
            tokyo_high=session_levels.tokyo_high,
            tokyo_low=session_levels.tokyo_low,
            london_high=session_levels.london_high,
            london_low=session_levels.london_low,
            rotation_factor=rf,
            aspr=aspr,
            aspr_percentile=aspr_pct,
            ib_tpo_count=tpo.ib_tpo_count,
            value_migration=value_migration,
        )

        # Persist session metric for baseline history
        self.repo.upsert_session_metric(symbol, target_date, rf, aspr)

        # Persist levels as MarketLevel rows for the /levels endpoint
        level_rows = self._session_levels_to_rows(session_levels, session_data)

        # Detect order blocks and FVGs from 30-min bars
        from ..market_data.levels import detect_order_blocks, detect_fvgs
        order_blocks = detect_order_blocks(bars_30m)
        fvgs = detect_fvgs(bars_30m)

        for ob in order_blocks:
            level_rows.append({
                "level_type": f"order_block_{ob.direction}",
                "price_low": ob.price_low,
                "price_high": ob.price_high,
                "direction": ob.direction,
                "session": "rth",
                "is_filled": False,
            })
        for fvg in fvgs:
            level_rows.append({
                "level_type": f"fvg_{fvg.direction}",
                "price_low": fvg.price_low,
                "price_high": fvg.price_high,
                "direction": fvg.direction,
                "session": "rth",
                "is_filled": False,
            })

        if level_rows:
            self.repo.upsert_levels(symbol, target_date, level_rows)

        self.db.commit()

        logger.info("Computed session for %s %s: POC=%.2f, VA=%.2f-%.2f, RF=%d, ASPR=%.2f",
                     symbol, target_date,
                     session_data.get("poc", 0),
                     session_data.get("val", 0),
                     session_data.get("vah", 0),
                     rf, aspr)

        return session_data

    async def build_expanded_session(self, symbol: str = "NQ") -> dict | None:
        """Build the expanded session response with all analytical layers."""
        from ..market_data.levels import detect_swing_points, detect_naked_pocs, compute_developing_poc

        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        today = date.today().isoformat()

        session_row = self.repo.get_session(today, symbol)
        if not session_row or not session_row.session_json:
            # Fallback: latest session (e.g. weekend/holiday — use last trading day)
            session_row = self.repo.get_previous_session(symbol)
            if not session_row or not session_row.session_json:
                return None

        sj = session_row.session_json
        if isinstance(sj, str):
            sj = json.loads(sj)

        # DB-only data (fast, never blocks)
        cot_data = self._get_cot_summary()
        levels = self.repo.get_levels(symbol, today)
        levels_list = [
            {
                "type": lv.level_type,
                "price_low": lv.price_low,
                "price_high": lv.price_high,
                "direction": lv.direction,
                "session": lv.session,
                "is_filled": lv.is_filled,
            }
            for lv in levels
        ]

        macro = sj.get("macro", {}) or {}
        if cot_data:
            macro["cot_net_position"] = cot_data.get("net_non_commercial")
            macro["cot_change_1w"] = cot_data.get("change_1w")

        vwap_dev_sd = None
        vwap = sj.get("vwap")
        last_price = sj.get("last_price")
        vwap_1sd_upper = sj.get("vwap_1sd_upper")
        if vwap and last_price and vwap_1sd_upper:
            sd_width = vwap_1sd_upper - vwap
            if sd_width > 0:
                vwap_dev_sd = round((last_price - vwap) / sd_width, 2)

        # Bar-dependent analytics — wrapped in timeout so session always returns
        structure = {}
        profiles = {
            "session": {"poc": session_row.poc, "vah": session_row.vah, "val": session_row.val},
            "developing_poc": None,
            "developing_poc_direction": None,
            "naked_pocs": [],
        }

        try:
            structure, profiles = await asyncio.wait_for(
                self._enrich_with_bars(symbol, today, session_row, sj),
                timeout=30.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Bar enrichment failed/timed out: %s", e)

        # Assemble nested response
        return {
            "session": {
                "date": session_row.date,
                "symbol": session_row.symbol,
                **{k: sj.get(k) for k in [
                    "poc", "vah", "val", "vwap",
                    "vwap_1sd_upper", "vwap_1sd_lower",
                    "vwap_2sd_upper", "vwap_2sd_lower",
                    "vwap_3sd_upper", "vwap_3sd_lower",
                    "ib_high", "ib_low", "ib_range",
                    "market_type", "opening_type",
                    "poor_high", "poor_low", "single_prints",
                    "value_migration", "overnight_high", "overnight_low",
                    "total_delta", "delta_divergence",
                    "last_price", "price_vs_va", "price_vs_vwap", "price_vs_ib",
                    "distribution_type",
                ]},
                "rotation_factor": session_row.rotation_factor,
                "aspr": session_row.aspr,
                "aspr_percentile": session_row.aspr_percentile,
                "tpo_poc": sj.get("tpo_poc"),
                "tpo_vah": sj.get("tpo_vah"),
                "tpo_val": sj.get("tpo_val"),
                # Session levels from DB columns (not in session_json)
                "pdh": session_row.pdh,
                "pdl": session_row.pdl,
                "tokyo_high": session_row.tokyo_high,
                "tokyo_low": session_row.tokyo_low,
                "london_high": session_row.london_high,
                "london_low": session_row.london_low,
            },
            "macro": macro,
            "structure": structure,
            "profiles": profiles,
            "levels": levels_list,
            "price_position": {
                "last_price": sj.get("last_price"),
                "vs_va": sj.get("price_vs_va"),
                "vs_vwap": sj.get("price_vs_vwap"),
                "vs_ib": sj.get("price_vs_ib"),
                "vwap_deviation_sd": vwap_dev_sd,
            },
            "ml_day_type": None,
            "ml_day_type_confidence": None,
        }

    async def _enrich_with_bars(self, symbol: str, today: str, session_row, sj: dict) -> tuple[dict, dict]:
        """Fetch bars and compute analytics. Returns (structure, profiles). May be slow/fail."""
        from ..market_data.levels import detect_swing_points, detect_naked_pocs, compute_developing_poc

        bars = await self._get_session_bars(symbol)

        bar_dicts = [{"high": b.get("high", 0), "low": b.get("low", 0), "close": b.get("close", 0)} for b in bars] if bars else []
        structure = detect_swing_points(bar_dicts, lookback=5)

        # Compute VP live — prefer tick data for accuracy, fall back to bars
        bar_dicts_with_vol = [{"high": b.get("high", 0), "low": b.get("low", 0), "close": b.get("close", 0), "volume": b.get("volume", 1)} for b in bars] if bars else []
        tick_vp = await self._compute_tick_vp(symbol)
        if tick_vp is not None:
            vp = tick_vp
        elif bar_dicts_with_vol:
            vp = compute_volume_profile_from_bars(bar_dicts_with_vol)
        else:
            vp = None

        if vp is not None:
            profiles = {
                "session": {"poc": vp.poc, "vah": vp.vah, "val": vp.val}
            }
        else:
            # Fallback to DB if no bars/ticks available
            profiles = {
                "session": {"poc": session_row.poc, "vah": session_row.vah, "val": session_row.val}
            }

        # Weekly and monthly VP
        for tf in ("weekly", "monthly"):
            try:
                tf_bars = await self._get_period_bars(symbol, tf)
                if tf_bars:
                    tf_vp = compute_volume_profile_from_bars(tf_bars)
                    profiles[tf] = {"poc": tf_vp.poc, "vah": tf_vp.vah, "val": tf_vp.val}
            except Exception as e:
                logger.warning("%s VP failed: %s", tf, e)

        # Developing POC
        dev_poc = compute_developing_poc(bar_dicts_with_vol)
        profiles["developing_poc"] = dev_poc["developing_poc"]
        profiles["developing_poc_direction"] = dev_poc["direction"]

        # Naked POCs
        prior_sessions = self.repo.list_sessions(symbol, limit=20)
        prior_pocs = [{"date": s.date, "poc": s.poc} for s in prior_sessions if s.poc and s.date != today]
        if prior_pocs:
            oldest_date = prior_pocs[0]["date"] if prior_pocs else today
            all_bars = await self._fetch_bars_range(symbol, oldest_date)
            all_bar_dicts = [{"high": b.get("high", 0), "low": b.get("low", 0)} for b in (all_bars or [])]
            profiles["naked_pocs"] = detect_naked_pocs(prior_pocs, all_bar_dicts)
        else:
            profiles["naked_pocs"] = []

        return structure, profiles

    async def run_scan(self, threshold: float | None = None) -> list[dict]:
        """Run scanner on current session → persist signals → return."""
        config = get_market_data_config()
        scanner_cfg = get_scanner_config()
        symbol = config.get("symbol", "NQ.FUT").split(".")[0]
        today = date.today().isoformat()

        threshold = threshold or scanner_cfg.get("score_threshold", 70)

        # Get or compute session
        session_row = self.repo.get_session(today, symbol)
        if not session_row or not session_row.session_json:
            # Need to compute first
            await self.compute_session()
            session_row = self.repo.get_session(today, symbol)

        if not session_row or not session_row.session_json:
            return []

        # Rebuild SessionAnalysis from stored JSON
        sj = session_row.session_json
        from ..market_data.amt import (
            VolumeProfile, VWAPBands, InitialBalance, DeltaAnalysis, SessionAnalysis
        )
        analysis = SessionAnalysis(
            date=sj.get("date", today),
            symbol=sj.get("symbol", symbol),
            last_price=sj.get("last_price"),
            market_type=sj.get("market_type", "unknown"),
            opening_type=sj.get("opening_type", "unknown"),
            poor_high=sj.get("poor_high", False),
            poor_low=sj.get("poor_low", False),
            single_prints=sj.get("single_prints", []),
            price_vs_va=sj.get("price_vs_va", "unknown"),
            price_vs_vwap=sj.get("price_vs_vwap", "unknown"),
            price_vs_ib=sj.get("price_vs_ib", "unknown"),
            overnight_high=sj.get("overnight_high"),
            overnight_low=sj.get("overnight_low"),
            prev_poc=sj.get("prev_poc"),
            prev_vah=sj.get("prev_vah"),
            prev_val=sj.get("prev_val"),
        )
        if sj.get("poc") is not None:
            analysis.volume_profile = VolumeProfile(
                poc=sj["poc"], vah=sj.get("vah", 0), val=sj.get("val", 0)
            )
        if sj.get("vwap") is not None:
            analysis.vwap_bands = VWAPBands(
                vwap=sj["vwap"],
                upper_1sd=sj.get("vwap_1sd_upper", 0),
                lower_1sd=sj.get("vwap_1sd_lower", 0),
                upper_2sd=sj.get("vwap_2sd_upper", 0),
                lower_2sd=sj.get("vwap_2sd_lower", 0),
                upper_3sd=sj.get("vwap_3sd_upper", 0),
                lower_3sd=sj.get("vwap_3sd_lower", 0),
            )
        if sj.get("ib_high") is not None:
            analysis.initial_balance = InitialBalance(
                ib_high=sj["ib_high"],
                ib_low=sj.get("ib_low", 0),
                ib_range=sj.get("ib_range", 0),
            )
        if sj.get("total_delta") is not None:
            analysis.delta = DeltaAnalysis(
                total_delta=sj["total_delta"],
                delta_divergence=sj.get("delta_divergence", False),
                cumulative_delta=[sj.get("cumulative_delta_last", 0)],
            )

        # Build live orderflow for ML models and auto-scoring
        of_signals = self._compute_live_orderflow(symbol, sj)

        # Run scanner with orderflow (enables M5/M6/M9 + auto-scoring)
        setups = get_setups()
        scanner = MarketScanner(setups, threshold, db_session=self.db)
        signals = scanner.scan(analysis, orderflow=of_signals)

        # Expire old signals
        expiry = scanner_cfg.get("signal_expiry_minutes", 60)
        self.repo.expire_old_signals(expiry)

        # Persist new signals
        results = []
        for sig in signals:
            conds_json = [
                {"name": c.name, "score": c.score, "weight": c.weight, "is_auto": c.is_auto, "detail": c.detail}
                for c in sig.conditions
            ]
            signal_row = self.repo.create_signal(
                session_id=session_row.id,
                setup_type=sig.setup_type,
                setup_name=sig.setup_name,
                category=sig.category,
                direction=sig.direction,
                score=sig.score,
                conditions=conds_json,
                price_at_signal=analysis.last_price,
                suggested_entry=sig.suggested_entry,
                suggested_stop=sig.suggested_stop,
                suggested_target=sig.suggested_target,
                vwap=sj.get("vwap"),
                poc=sj.get("poc"),
                vah=sj.get("vah"),
                val=sj.get("val"),
                ib_high=sj.get("ib_high"),
                ib_low=sj.get("ib_low"),
                cumulative_delta=sj.get("cumulative_delta_last"),
            )
            results.append({
                "id": signal_row.id,
                "setup_type": sig.setup_type,
                "setup_name": sig.setup_name,
                "category": sig.category,
                "direction": sig.direction,
                "score": sig.score,
                "conditions": conds_json,
                "price_at_signal": analysis.last_price,
                "suggested_entry": sig.suggested_entry,
                "suggested_stop": sig.suggested_stop,
                "suggested_target": sig.suggested_target,
            })

        # --- New: Setup detector-based signal generation ---
        try:
            detector_signals = self._run_setup_detectors(session_row, sj, symbol)
            results.extend(detector_signals)
        except Exception as e:
            logger.warning("Setup detectors failed (non-fatal): %s", e)

        self.db.commit()
        logger.info("Scan produced %d signals above threshold %.0f", len(results), threshold)
        return results

    def _run_setup_detectors(
        self, session_row, sj: dict, symbol: str
    ) -> list[dict]:
        """Run the level-based setup detectors and return scored signals."""
        config = get_market_data_config()
        sessions_cfg = config.get("sessions", {})

        # Determine session time window for recent ticks
        today = date.today()
        dt = datetime.combine(today, datetime.strptime(sessions_cfg.get("rth_open", "09:30"), "%H:%M").time())
        session_start = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        recent_ticks = self.repo.get_trades(symbol, start=session_start, end=now)
        tick_dicts = [
            {"ts": t.ts, "price": t.price, "size": t.size, "side": t.side}
            for t in recent_ticks
        ]

        # Build candle flow
        candles = build_candle_flow(tick_dicts, period_seconds=60)

        # Auto-detect direction from price action structure
        from ..market_data.levels import detect_swing_points
        bars_for_structure = sj.get("bars", [])
        if bars_for_structure:
            bar_dicts_for_struct = [{"high": b.get("high", 0) if isinstance(b, dict) else 0, "low": b.get("low", 0) if isinstance(b, dict) else 0, "close": b.get("close", 0) if isinstance(b, dict) else 0} for b in bars_for_structure]
        else:
            bar_dicts_for_struct = []
        structure_result = detect_swing_points(bar_dicts_for_struct, lookback=5)
        struct_class = structure_result.get("structure", "ranging")
        if struct_class == "uptrend":
            direction = "long"
        elif struct_class == "downtrend":
            direction = "short"
        else:
            direction = "long"  # Default to long when ranging

        ctx_model = self.repo.get_context(symbol)

        orderflow = compute_signals(candles, direction)

        # Build volume profile and VWAP from session data
        vp = VolumeProfile(
            poc=sj.get("poc", 0) or 0,
            vah=sj.get("vah", 0) or 0,
            val=sj.get("val", 0) or 0,
            levels=[],
            single_prints=[],
        )
        vwap_bands = compute_vwap_bands(tick_dicts) if tick_dicts else None

        # Build session levels from stored values
        sl = SessionLevels(
            pdh=session_row.pdh,
            pdl=session_row.pdl,
            tokyo_high=session_row.tokyo_high,
            tokyo_low=session_row.tokyo_low,
            london_high=session_row.london_high,
            london_low=session_row.london_low,
            ib_high=sj.get("ib_high"),
            ib_low=sj.get("ib_low"),
        )

        # Build TPO stub from session row data
        from ..market_data.tpo import TPOProfile
        tpo = TPOProfile(
            letters={}, poc=sj.get("poc", 0) or 0,
            vah=sj.get("vah", 0) or 0, val=sj.get("val", 0) or 0,
            single_prints=[], ledges=[],
            poor_high=sj.get("poor_high", False),
            poor_low=sj.get("poor_low", False),
            ib_tpo_count=session_row.ib_tpo_count or 0,
        )

        # Build detector context
        detector_ctx = DetectorContext(
            vp=vp,
            vwap=vwap_bands,
            session_levels=sl,
            tpo=tpo,
            orderflow=orderflow,
            last_price=tick_dicts[-1]["price"] if tick_dicts else 0,
            macro_bias=None,  # No longer using manual macro_bias
            structure=struct_class,  # Auto-detected from swing points
            day_type=ctx_model.day_type if ctx_model else None,
        )

        # Run all detectors
        raw_candidates = run_all_detectors(detector_ctx)

        # Score and filter
        scored = []
        for c in raw_candidates:
            fits = day_type_fits_setup(detector_ctx.day_type, c.setup_type)
            macro_ok = True  # No longer gating on manual macro bias
            final_score = score_candidate(
                c, orderflow, fits, macro_ok,
                session_row.rotation_factor, session_row.aspr_percentile,
            )
            if final_score >= 70:
                c.base_score = final_score
                scored.append(c)

        # R:R filter
        scored = filter_by_rr(scored, min_rr=1.5)

        # Store as TradingSignal rows
        results = []
        for c in scored:
            signal = TradingSignal(
                session_id=session_row.id,
                setup_type=c.setup_type,
                setup_name=c.setup_name,
                setup_category=c.setup_type,
                score=c.base_score,
                direction=c.direction,
                price_at_signal=detector_ctx.last_price,
                suggested_entry=c.entry_price,
                suggested_stop=c.stop_price,
                suggested_target=c.target_1,
                suggested_target_2=c.target_2,
                suggested_target_3=c.target_3,
                level_touched=c.level_touched,
                rr_tp1=c.rr_tp1,
                rr_tp2=c.rr_tp2,
                conditions=json.dumps({"orderflow": orderflow.__dict__}, default=str),
                vwap=sj.get("vwap"),
                poc=sj.get("poc"),
                vah=sj.get("vah"),
                val=sj.get("val"),
                ib_high=sj.get("ib_high"),
                ib_low=sj.get("ib_low"),
            )
            self.db.add(signal)
            self.db.flush()
            results.append({
                "id": signal.id,
                "setup_type": c.setup_type,
                "setup_name": c.setup_name,
                "category": c.setup_type,
                "direction": c.direction,
                "score": c.base_score,
                "price_at_signal": detector_ctx.last_price,
                "suggested_entry": c.entry_price,
                "suggested_stop": c.stop_price,
                "suggested_target": c.target_1,
                "suggested_target_2": c.target_2,
                "suggested_target_3": c.target_3,
                "level_touched": c.level_touched,
                "rr_tp1": c.rr_tp1,
                "rr_tp2": c.rr_tp2,
            })

        logger.info("Setup detectors produced %d signals (from %d candidates)",
                     len(results), len(raw_candidates))
        return results

    def get_current_session(self, symbol: str | None = None) -> dict | None:
        """Get today's session data from DB."""
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        today = date.today().isoformat()

        session = self.repo.get_session(today, symbol)
        if session and session.session_json:
            return session.session_json
        return None

    def get_active_signals(self, symbol: str | None = None) -> list[dict]:
        """Get currently active signals."""
        signals = self.repo.get_active_signals(symbol)
        return [
            {
                "id": s.id,
                "setup_type": s.setup_type,
                "setup_name": s.setup_name,
                "category": s.category,
                "setup_category": s.setup_category,
                "direction": s.direction,
                "score": s.score,
                "conditions": s.conditions,
                "price_at_signal": s.price_at_signal,
                "suggested_entry": s.suggested_entry,
                "suggested_stop": s.suggested_stop,
                "suggested_target": s.suggested_target,
                "suggested_target_2": s.suggested_target_2,
                "suggested_target_3": s.suggested_target_3,
                "level_touched": s.level_touched,
                "rr_tp1": s.rr_tp1,
                "vwap": s.vwap,
                "poc": s.poc,
                "triggered_at": s.triggered_at.isoformat() if s.triggered_at else None,
                "trade_id": s.trade_id,
            }
            for s in signals
        ]

    def get_confirmations(self, symbol: str | None = None) -> dict:
        """Evaluate 4 confirmation gates from current session + live orderflow.

        Returns rich data for each gate so the frontend can display full context.
        Layer B auto-gates: macro, span, fair_value, orderflow.
        """
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        session_data = self.get_current_session(symbol)

        empty_of = {
            "checked": False, "delta": None, "divergence": False,
            "delta_aligned": False, "delta_unwind": False,
            "cvd": None, "cvd_trend": "flat",
            "vsa_absorption": False, "tick_vol_accelerating": False,
            "trapped_traders": False, "passive_active_ratio": 0.0,
            "big_trades_count": 0, "big_trades_net_delta": 0,
            "stop_run_detected": False,
        }

        if not session_data:
            return {
                "macro": {"checked": False, "regime": "unknown", "vix": None},
                "span": {"checked": False, "structure": "no_data"},
                "fair_value": {"checked": False, "deviation_sd": None, "price_vs_va": "unknown"},
                "orderflow": empty_of,
            }

        # --- Compute live orderflow from recent ticks ---
        of_signals = self._compute_live_orderflow(symbol, session_data)

        # === Gate 1: Macro (risk regime) ===
        macro = session_data.get("macro") or {}
        regime = macro.get("regime", "unknown")
        macro_checked = regime in ("risk_on", "mixed")

        # === Gate 2: Span (trending structure) ===
        market_type = session_data.get("market_type", "unknown")
        if market_type in ("trending_up", "trending_down"):
            span_checked = True
            span_structure = "bullish" if market_type == "trending_up" else "bearish"
        else:
            span_checked = False
            span_structure = "no_clear_structure"

        # M7: Auto day-type prediction (overrides manual if model loaded)
        ml_day_type = None
        ml_day_type_confidence = None
        try:
            from ..ml.serving.predictor import get_predictor
            from ..ml.models.gate_classifier import DAY_TYPE_FEATURE_NAMES, DAY_TYPE_LABELS
            predictor = get_predictor()
            if predictor.is_loaded("gate_classifier"):
                gate_features = {
                    "rf_after_ib": session_data.get("rotation_factor", 0),
                    "ib_range": session_data.get("ib_range", 0),
                    "ib_range_vs_avg": session_data.get("ib_range_vs_avg", 1.0),
                    "opening_type_encoded": {"od": 0, "oi": 1, "or": 2, "otd": 3}.get(
                        session_data.get("opening_type", ""), 0),
                    "first_hour_delta_total": session_data.get("total_delta", 0),
                    "first_hour_volume_vs_avg": 1.0,
                    "overnight_range_pct": 0,
                    "gap_filled_pct": 0,
                    "yesterday_market_type_encoded": 0,
                    "poor_high_or_low_in_ib": 1 if session_data.get("poor_high") or session_data.get("poor_low") else 0,
                    "first_hour_big_trades_count": of_signals.big_trades_count if of_signals else 0,
                    "session_volume_first_hour": 0,
                    "vix_level": (session_data.get("macro") or {}).get("vix", 0) or 0,
                    "gex": 0,
                    "value_migration_encoded": {"up": 1, "down": -1, "neutral": 0}.get(
                        session_data.get("value_migration", "neutral"), 0),
                    "ib_tpo_count": session_data.get("ib_tpo_count", 0),
                }
                pred = predictor.predict("gate_classifier", gate_features)
                if pred and isinstance(pred, dict):
                    ml_day_type = DAY_TYPE_LABELS.get(pred["class"], "unknown")
                    probs = pred.get("probabilities", [])
                    ml_day_type_confidence = round(max(probs) * 100, 1) if probs else None
                    logger.info("M7 predicted day type: %s (%.1f%% confidence)",
                               ml_day_type, ml_day_type_confidence or 0)
        except Exception as e:
            logger.debug("M7 gate classifier skipped: %s", e)

        # === Gate 3: Fair Value (deviation from VWAP/VA) ===
        vwap = session_data.get("vwap")
        last_price = session_data.get("last_price")
        vwap_1sd_upper = session_data.get("vwap_1sd_upper")
        vwap_1sd_lower = session_data.get("vwap_1sd_lower")
        price_vs_va = session_data.get("price_vs_va", "unknown")

        deviation_sd = None
        fv_checked = False
        if vwap and last_price and vwap_1sd_upper and vwap_1sd_lower:
            sd_width = vwap_1sd_upper - vwap
            if sd_width > 0:
                deviation_sd = round((last_price - vwap) / sd_width, 2)
                fv_checked = abs(deviation_sd) >= 1.5 or price_vs_va in ("above", "below")

        # === Gate 4: Orderflow (rich signals from live ticks) ===
        # Checked if: delta aligned + at least 1 confirming signal
        confirming_count = sum([
            of_signals.delta_aligned,
            of_signals.vsa_absorption,
            of_signals.trapped_traders,
            of_signals.tick_vol_accelerating,
            of_signals.cvd_trend in ("rising", "falling"),
        ])
        of_checked = of_signals.delta_aligned and confirming_count >= 2

        return {
            "macro": {"checked": macro_checked, "regime": regime, "vix": macro.get("vix")},
            "span": {"checked": span_checked, "structure": span_structure},
            "fair_value": {"checked": fv_checked, "deviation_sd": deviation_sd, "price_vs_va": price_vs_va},
            "ml_day_type": ml_day_type,
            "ml_day_type_confidence": ml_day_type_confidence,
            "orderflow": {
                "checked": of_checked,
                "delta": of_signals.delta,
                "delta_aligned": of_signals.delta_aligned,
                "divergence": of_signals.delta_divergence,
                "delta_unwind": of_signals.delta_unwind,
                "cvd": of_signals.cvd,
                "cvd_trend": of_signals.cvd_trend,
                "vsa_absorption": of_signals.vsa_absorption,
                "tick_vol_accelerating": of_signals.tick_vol_accelerating,
                "trapped_traders": of_signals.trapped_traders,
                "passive_active_ratio": of_signals.passive_active_ratio,
                "big_trades_count": of_signals.big_trades_count,
                "big_trades_net_delta": of_signals.big_trades_net_delta,
                "stop_run_detected": of_signals.stop_run_detected,
                "imbalance_ratio_max": of_signals.imbalance_ratio_max,
                "stacked_imbalance_count": of_signals.stacked_imbalance_count,
                "stacked_imbalance_direction": of_signals.stacked_imbalance_direction,
            },
        }

    async def get_indicators(self, symbol: str | None = None) -> dict:
        """Return live indicator data (orderflow + ML predictions). No gate logic."""
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]

        session_row = self.repo.get_session(date.today().isoformat(), symbol)
        sj = {}
        if session_row and session_row.session_json:
            sj = session_row.session_json if isinstance(session_row.session_json, dict) else json.loads(session_row.session_json)

        # Bars not in session_json — fetch from cache (with timeout)
        try:
            bars = await asyncio.wait_for(
                self._fetch_bars_for_date(symbol, date.today().isoformat()),
                timeout=10.0,
            )
        except (asyncio.TimeoutError, Exception):
            bars = []
        bar_dicts = [{"high": b.get("high", 0), "low": b.get("low", 0), "close": b.get("close", 0)} for b in bars] if bars else []

        # Direction from structure, not manual gates
        from ..market_data.levels import detect_swing_points
        structure = detect_swing_points(bar_dicts, lookback=5)
        struct_class = structure.get("structure", "ranging")
        if struct_class == "uptrend":
            direction = "long"
        elif struct_class == "downtrend":
            direction = "short"
        else:
            direction = None

        # Compute orderflow
        of_signals = self._compute_live_orderflow(symbol, sj, direction=direction)

        # M7 day type prediction
        ml_day_type = None
        ml_day_type_confidence = None
        try:
            from ..ml.serving.predictor import get_predictor
            from ..ml.models.gate_classifier import DAY_TYPE_LABELS
            predictor = get_predictor()
            if predictor.is_loaded("gate_classifier"):
                gate_features = self._build_gate_features(sj, session_row)
                if gate_features:
                    pred = predictor.predict("gate_classifier", gate_features)
                    if pred and "class" in pred:
                        ml_day_type = DAY_TYPE_LABELS.get(pred["class"], "unknown")
                        probs = pred.get("probabilities", [])
                        ml_day_type_confidence = round(max(probs) * 100, 1) if probs else None
        except Exception as e:
            logger.debug("M7 prediction skipped: %s", e)

        return {
            "orderflow": of_signals.__dict__ if of_signals else {},
            "ml_day_type": ml_day_type,
            "ml_day_type_confidence": ml_day_type_confidence,
        }

    # VP curve cache: {(symbol, timeframe): (result, expiry_time)}
    _vp_cache: dict[tuple, tuple] = {}
    # Candle cache: {(symbol, interval, date, days): (result, expiry_time)}
    _candle_cache: dict[tuple, tuple] = {}
    # Session levels cache: {(symbol, days): (result, expiry_time)}
    _session_levels_cache: dict[tuple, tuple] = {}

    async def get_volume_profile_curve(self, symbol: str = "NQ", timeframe: str = "session") -> dict:
        """Return VP curve (price→volume) for charting. Cached for 60s.

        Session VP uses tick data from market_trades for accuracy (exact trade prices).
        Weekly/monthly use 1m bar approximation (good enough at scale).
        """
        import time as _time

        cache_key = (symbol, timeframe)
        cached = MarketService._vp_cache.get(cache_key)
        if cached and _time.time() < cached[1]:
            return cached[0]

        vp = None

        if timeframe == "session":
            # Try tick-based VP first (accurate — no bar-spread approximation)
            vp = await self._compute_tick_vp(symbol)

        if vp is None:
            # Fall back to bar-based VP
            if timeframe == "session":
                bars = await self._get_session_bars(symbol)
            else:
                bars = await self._get_period_bars(symbol, timeframe)

            if not bars:
                return {"timeframe": timeframe, "levels": [], "poc": 0, "vah": 0, "val": 0}

            vp = compute_volume_profile_from_bars(bars)

        result = {
            "timeframe": timeframe,
            "poc": vp.poc,
            "vah": vp.vah,
            "val": vp.val,
            "levels": [{"price": lv.price, "volume": lv.volume} for lv in vp.levels],
        }
        MarketService._vp_cache[cache_key] = (result, _time.time() + 300)
        return result

    async def _compute_tick_vp(self, symbol: str) -> VolumeProfile | None:
        """Compute VP from actual tick data in market_trades table.

        Returns VolumeProfile if sufficient ticks exist, else None (caller falls back to bars).
        """
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()
        d_start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)

        try:
            trades = self.repo.get_trades(symbol, d_start, now)
            if len(trades) < 100:
                logger.info("Tick VP: only %d ticks, falling back to bars", len(trades))
                return None

            # Build trade dicts for compute_volume_profile (exact prices, no spreading)
            trade_dicts = [{"price": t.price, "size": t.size} for t in trades]
            vp = compute_volume_profile(trade_dicts)
            logger.info("Tick VP: computed from %d ticks (POC=%.2f, VAH=%.2f, VAL=%.2f)",
                        len(trades), vp.poc, vp.vah, vp.val)
            return vp
        except Exception as e:
            logger.warning("Tick VP failed, will fall back to bars: %s", e)
            return None

    async def _get_period_bars(self, symbol: str, timeframe: str) -> list[dict]:
        """Get 1m bars for weekly or monthly VP from DB."""
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()

        if timeframe == "weekly":
            # Monday 00:00 CET of current week
            start_date = today_cet - timedelta(days=today_cet.weekday())
        else:  # monthly
            start_date = today_cet.replace(day=1)

        d_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=_CET).astimezone(timezone.utc)

        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", d_start, now))
        logger.info("VP %s bars: %d from DB (%s to now)", timeframe, len(rows), start_date)
        return [{"high": r.h, "low": r.l, "close": r.c, "volume": r.v} for r in rows]

    async def get_developing_vwap(self, symbol: str = "NQ", interval: str = "1m") -> dict:
        """Return developing VWAP time series from 1m candle data.

        Anchored from 00:00 CET (daily reset at midnight CET) — includes all
        sessions (Tokyo + London + New York).

        Uses stored 1m candles from DB for speed (3M ticks too slow to query).
        Computes VWAP from typical price (HLC/3) weighted by volume.
        """
        import math
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        # Start from 00:00 CET today
        today_cet = now.astimezone(_CET).date()
        start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET)

        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", start, now))
        logger.info("VWAP: got %d 1m candles from DB for %s (from 00:00 CET)", len(rows), symbol)

        cum_pv = 0.0
        cum_vol = 0
        cum_pv2 = 0.0
        series: list[dict] = []

        for r in rows:
            ts = r.ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            tp = (r.h + r.l + r.c) / 3
            vol = r.v or 1

            cum_pv += tp * vol
            cum_vol += vol
            cum_pv2 += tp * tp * vol

            if cum_vol == 0:
                continue

            vwap = cum_pv / cum_vol
            variance = max(0, (cum_pv2 / cum_vol) - vwap * vwap)
            sd = math.sqrt(variance)

            epoch = int(ts.timestamp())
            series.append({
                "t": epoch,
                "vwap": round(vwap, 2),
                "sd1_u": round(vwap + sd, 2),
                "sd1_l": round(vwap - sd, 2),
                "sd2_u": round(vwap + 2 * sd, 2),
                "sd2_l": round(vwap - 2 * sd, 2),
                "sd3_u": round(vwap + 3 * sd, 2),
                "sd3_l": round(vwap - 3 * sd, 2),
            })

        logger.info("VWAP: computed %d points from 1m candles (00:00 CET anchor)", len(series))
        return {"vwap": series, "symbol": symbol, "count": len(series)}

    async def get_session_levels(self, symbol: str = "NQ", days: int = 5) -> dict:
        """Compute session levels (PDH/PDL, IB, Tokyo, London) from 1m candles for multiple days.

        Returns per-day levels with CET epoch boundaries so the frontend can draw
        time-scoped horizontal lines. Computes on-the-fly from market_candles —
        same logic used by RL backtesting. Cached for 60s.
        """
        import time as _time
        from zoneinfo import ZoneInfo
        from collections import defaultdict

        cache_key = (symbol, days)
        cached = MarketService._session_levels_cache.get(cache_key)
        if cached and _time.time() < cached[1]:
            return cached[0]

        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()

        # Fetch enough 1m candles to cover `days` trading days + 1 extra for PDH/PDL
        pad_days = days + (days // 5) * 2 + 3  # pad for weekends
        start_dt = datetime(
            today_cet.year, today_cet.month, today_cet.day,
            tzinfo=_CET,
        ) - timedelta(days=pad_days)
        start_utc = start_dt.astimezone(timezone.utc)

        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", start_utc, now))
        if not rows:
            return {"days": [], "symbol": symbol}

        # Convert DB rows to bar dicts for compute_session_levels()
        bars = [
            {"ts": r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc), "high": r.h, "low": r.l}
            for r in rows
        ]

        # Group bars by CET date
        bars_by_date: dict[str, list[dict]] = defaultdict(list)
        for b in bars:
            cet_date = b["ts"].astimezone(_CET).date().isoformat()
            bars_by_date[cet_date].append(b)

        # Get sorted dates (most recent first), limit to requested days
        sorted_dates = sorted(bars_by_date.keys(), reverse=True)[:days]

        def _cet_epoch(d, h, m):
            return int(datetime(d.year, d.month, d.day, h, m, tzinfo=_CET).timestamp())

        all_dates_sorted = sorted(bars_by_date.keys())  # ascending

        result_days = []
        for date_str in sorted_dates:
            # Find the most recent prior trading day (skip weekends/holidays)
            prior_bars: list[dict] = []
            for d in reversed(all_dates_sorted):
                if d < date_str:
                    prior_bars = bars_by_date[d]
                    break
            all_bars = prior_bars + bars_by_date[date_str]

            dt_parsed = datetime.strptime(date_str, "%Y-%m-%d")
            session_date = dt_parsed.replace(hour=12, tzinfo=ZoneInfo("US/Eastern"))
            sl = compute_session_levels(all_bars, session_date)

            # CET epoch boundaries for frontend time-scoping
            d = datetime.strptime(date_str, "%Y-%m-%d").date()

            result_days.append({
                "date": date_str,
                "pdh": sl.pdh,
                "pdl": sl.pdl,
                "pdh_time": sl.pdh_time,
                "pdl_time": sl.pdl_time,
                "ib_high": sl.ib_high,
                "ib_low": sl.ib_low,
                "tokyo_high": sl.tokyo_high,
                "tokyo_low": sl.tokyo_low,
                "london_high": sl.london_high,
                "london_low": sl.london_low,
                "ny_high": sl.ny_high,
                "ny_low": sl.ny_low,
                # Time boundaries (CET epochs) from levels.py constants
                "tokyo_start": _cet_epoch(d, _TOKYO_START.hour, _TOKYO_START.minute),
                "tokyo_end": _cet_epoch(d, _TOKYO_END.hour, _TOKYO_END.minute),
                "london_start": _cet_epoch(d, _LONDON_START.hour, _LONDON_START.minute),
                "london_end": _cet_epoch(d, _LONDON_END.hour, _LONDON_END.minute),
                "ib_start": _cet_epoch(d, _NY_START.hour, _NY_START.minute),
                "ib_end": _cet_epoch(d, _IB_END.hour, _IB_END.minute),
                "ny_start": _cet_epoch(d, _NY_START.hour, _NY_START.minute),
                "ny_end": _cet_epoch(d, _NY_END.hour, _NY_END.minute),
                "day_start": _cet_epoch(d, 0, 0),
                "day_end": _cet_epoch(d, _NY_END.hour, _NY_END.minute),
            })

        result = {"days": result_days, "symbol": symbol}
        MarketService._session_levels_cache[cache_key] = (result, _time.time() + 60)
        return result

    async def get_candles(self, symbol: str = "NQ", interval: str = "5m", date_str: str | None = None, days: int = 5) -> dict:
        """Return OHLCV candle array for charting from market_candles DB.

        Stored intervals: 1m, 5m.  15m is resampled from 1m on the fly.
        Detects gaps and triggers async backfill from Databento historical.
        Cached for 30s (keyed on symbol/interval/date/days).
        """
        import time as _time

        end_date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_key = (symbol, interval, end_date, days)
        cached = MarketService._candle_cache.get(cache_key)
        if cached and _time.time() < cached[1]:
            return cached[0]

        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        start_dt = end_dt - timedelta(days=days + (days // 5) * 2 + 2)  # pad for weekends

        repo = MarketRepo(self.db)

        if interval == "15m":
            rows = repo.get_candles(symbol, "1m", start_dt, end_dt)
            candles = self._resample_candles(rows, 15)
        else:
            rows = repo.get_candles(symbol, interval, start_dt, end_dt)
            candles = [
                {"t": int(r.ts.replace(tzinfo=timezone.utc).timestamp() if not r.ts.tzinfo else r.ts.timestamp()),
                 "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v}
                for r in rows
            ]

        # Filter to Globex hours (skip daily 17:00-18:00 ET halt + weekends)
        candles = [c for c in candles if self._in_globex(c["t"])]

        # Clamp outlier wicks from settlement auctions / erroneous ticks
        candles = self._filter_outlier_candles(candles)

        # Detect gaps and trigger async backfill in a background thread
        # (avoids event loop starvation from Databento API calls blocking HTTP handlers)
        base_interval = "1m" if interval == "15m" else interval
        gaps = self._detect_gaps(candles, base_interval)
        if gaps:
            import threading
            def _run_backfill(sym, iv, gap_list):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._backfill_gaps(sym, iv, gap_list))
                except Exception as e:
                    logger.warning("Background gap backfill failed: %s", e)
                finally:
                    loop.close()
            threading.Thread(
                target=_run_backfill, args=(symbol, base_interval, gaps),
                daemon=True, name="candle-backfill",
            ).start()

        result = {"candles": candles, "symbol": symbol, "interval": interval, "date": end_date}
        MarketService._candle_cache[cache_key] = (result, _time.time() + 30)
        return result

    @staticmethod
    def _filter_outlier_candles(candles: list[dict], radius: int = 20, max_wick: float = 25.0) -> list[dict]:
        """Clamp wicks using a close-price corridor as truth anchor.

        During NQ contract rolls, the continuous symbol (NQ.v.0) includes trades
        from both the expiring and new front-month contracts, creating systematic
        ~75-80pt wicks on virtually every candle.  Median-wick-based filters fail
        because the majority of candles are corrupted.

        Instead, build a local price corridor from neighboring *close* prices
        (which always come from the actively-traded contract) and clamp H/L so
        wicks cannot extend beyond close_range + max_wick.

        max_wick=25 allows normal volatile wicks (NQ 1m can easily have 15-20pt
        wicks during CPI/FOMC) while removing the 75-80pt roll artifacts.
        """
        if len(candles) < 3:
            return candles
        n = len(candles)
        closes = [c["c"] for c in candles]

        result = []
        for i, c in enumerate(candles):
            # Build close-price corridor from neighbors
            lo = max(0, i - radius)
            hi = min(n, i + radius + 1)
            neighbor_closes = closes[lo:hi]

            corridor_high = max(neighbor_closes)
            corridor_low = min(neighbor_closes)

            # Ceiling / floor: corridor extremes + max_wick buffer
            ceiling = corridor_high + max_wick
            floor = corridor_low - max_wick

            clamped_h = min(c["h"], ceiling)
            clamped_l = max(c["l"], floor)
            # Ensure h >= body high, l <= body low (never invert the candle)
            clamped_h = max(clamped_h, max(c["o"], c["c"]))
            clamped_l = min(clamped_l, min(c["o"], c["c"]))

            if clamped_h != c["h"] or clamped_l != c["l"]:
                result.append({**c, "h": clamped_h, "l": clamped_l})
            else:
                result.append(c)
        return result

    @staticmethod
    def _detect_gaps(candles: list[dict], interval: str) -> list[tuple[int, int]]:
        """Find gaps in candle series that exceed 2x the expected interval.

        Returns list of (gap_start_epoch, gap_end_epoch) tuples.
        Only returns gaps older than 15 min (Databento historical delay).
        """
        if len(candles) < 2:
            return []
        bucket_s = 60 if interval == "1m" else 300
        max_gap = bucket_s * 3  # allow small gaps (2 missing bars ok)
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        min_age = 15 * 60  # Databento 15 min delay

        gaps = []
        for i in range(1, len(candles)):
            diff = candles[i]["t"] - candles[i - 1]["t"]
            gap_end = candles[i]["t"]
            if diff > max_gap and (now_epoch - gap_end) > min_age:
                gaps.append((candles[i - 1]["t"], gap_end))
        return gaps

    async def _backfill_gaps(self, symbol: str, interval: str, gaps: list[tuple[int, int]]):
        """Async backfill detected gaps from Databento historical.

        Always backfills both 1m and 5m to keep them in sync.
        """
        try:
            from ..market_data.databento_provider import DabentoProvider
            config = get_market_data_config()
            inner = DabentoProvider(config)
            db_symbol = config.get("symbol", "NQ.v.0")

            # Backfill both 1m and 5m for each gap to keep intervals in sync
            intervals = {"1m", "5m"}
            intervals.add(interval)

            from ..db.models import get_session as _get_db_session
            for gap_start, gap_end in gaps:
                start_dt = datetime.fromtimestamp(gap_start, tz=timezone.utc)
                end_dt = datetime.fromtimestamp(gap_end, tz=timezone.utc)

                for iv in intervals:
                    logger.info("Candle gap backfill %s: %s → %s", iv, start_dt, end_dt)
                    try:
                        bars = await asyncio.wait_for(
                            inner.get_bars(db_symbol, iv, start_dt, end_dt),
                            timeout=60.0,
                        )
                        if bars:
                            db = _get_db_session()
                            try:
                                repo = MarketRepo(db)
                                count = repo.bulk_insert_candles(symbol, iv, bars)
                                logger.info("Candle gap backfill %s: inserted %d bars", iv, count)
                            finally:
                                db.close()
                    except Exception as e:
                        logger.warning("Candle gap backfill %s failed: %s", iv, e)
        except Exception as e:
            logger.warning("Candle gap backfill failed (non-fatal): %s", e)

    # Pre-create timezone for Globex filter (avoid per-call import + construction)
    _ET = None

    @staticmethod
    def _in_globex(epoch: int) -> bool:
        """Check if timestamp falls within CME Globex hours.

        Globex: Sun 18:00 ET → Fri 17:00 ET, with daily 17:00-18:00 ET halt.
        """
        if MarketService._ET is None:
            from zoneinfo import ZoneInfo
            MarketService._ET = ZoneInfo("US/Eastern")
        dt = datetime.fromtimestamp(epoch, tz=MarketService._ET)
        wd = dt.weekday()  # Mon=0 … Sun=6
        hour = dt.hour
        # Saturday: always closed
        if wd == 5:
            return False
        # Friday after 17:00: closed
        if wd == 4 and hour >= 17:
            return False
        # Sunday before 18:00: closed
        if wd == 6 and hour < 18:
            return False
        # Daily halt: 17:00-18:00 ET (Mon-Thu)
        if hour == 17:
            return False
        return True

    @staticmethod
    def _resample_candles(rows: list, minutes: int) -> list[dict]:
        """Resample 1m DB rows into larger interval candles."""
        if not rows:
            return []
        buckets: dict[int, list] = {}
        for r in rows:
            ts = r.ts.replace(tzinfo=timezone.utc) if not r.ts.tzinfo else r.ts
            epoch = int(ts.timestamp())
            bucket = epoch - (epoch % (minutes * 60))
            buckets.setdefault(bucket, []).append(r)

        result = []
        for bucket_ts in sorted(buckets):
            bars = buckets[bucket_ts]
            result.append({
                "t": bucket_ts,
                "o": bars[0].o,
                "h": max(b.h for b in bars),
                "l": min(b.l for b in bars),
                "c": bars[-1].c,
                "v": sum(b.v for b in bars),
            })
        return result

    def get_session_history(self, symbol: str | None = None, limit: int = 30) -> list[dict]:
        """Get historical session data."""
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        sessions = self.repo.list_sessions(symbol, limit)
        return [
            {
                "date": s.date,
                "symbol": s.symbol,
                "poc": s.poc,
                "vah": s.vah,
                "val": s.val,
                "vwap": s.vwap,
                "ib_high": s.ib_high,
                "ib_low": s.ib_low,
                "market_type": s.market_type,
                "opening_type": s.opening_type,
                "total_delta": s.total_delta,
            }
            for s in sessions
        ]

    # ---- TPO session storage ----

    def store_tpo_session(self, profile, symbol: str, date_str: str):
        """Store a completed TPO session profile to the DB."""
        from ..db.models import MarketTPOSession
        import json as _json
        from dataclasses import asdict
        session_json = _json.dumps(asdict(profile), default=str)

        existing = self.db.query(MarketTPOSession).filter_by(symbol=symbol, date=date_str).first()
        if existing:
            for attr in ['poc', 'vah', 'val', 'ib_high', 'ib_low', 'rotation_factor',
                          'profile_shape', 'opening_type', 'opening_direction',
                          'upper_excess', 'lower_excess', 'session_high', 'session_low']:
                setattr(existing, attr, getattr(profile, attr))
            existing.session_json = session_json
        else:
            self.db.add(MarketTPOSession(
                symbol=symbol, date=date_str,
                poc=profile.poc, vah=profile.vah, val=profile.val,
                ib_high=profile.ib_high, ib_low=profile.ib_low,
                rotation_factor=profile.rotation_factor,
                profile_shape=profile.profile_shape,
                opening_type=profile.opening_type,
                opening_direction=profile.opening_direction,
                upper_excess=profile.upper_excess,
                lower_excess=profile.lower_excess,
                session_high=profile.session_high,
                session_low=profile.session_low,
                session_json=session_json,
            ))
        self.db.commit()

    def get_tpo_history(self, symbol: str = "NQ", days: int = 30) -> list[dict]:
        """Fetch historical TPO sessions for RL batch access."""
        from ..db.models import MarketTPOSession
        import json as _json
        rows = (
            self.db.query(MarketTPOSession)
            .filter_by(symbol=symbol)
            .order_by(MarketTPOSession.date.desc())
            .limit(days)
            .all()
        )
        result = []
        for row in reversed(rows):
            data = _json.loads(row.session_json)
            data["date"] = row.date
            result.append(data)
        return result

    _tpo_cache: dict[str, tuple[float, dict]] = {}

    def get_tpo_live(self, symbol: str = "NQ") -> dict:
        """Compute today's developing TPO profile. Cached 60s.

        After 22:00 CET (session end), auto-persists the completed profile
        to market_tpo_sessions so RL training has historical data.
        """
        import time as _time
        from dataclasses import asdict
        from zoneinfo import ZoneInfo
        cache_key = f"tpo_live_{symbol}"
        now = _time.time()

        cached = MarketService._tpo_cache.get(cache_key)
        if cached and now - cached[0] < 60:
            return cached[1]

        _CET = ZoneInfo("Europe/Stockholm")
        now_cet = datetime.now(timezone.utc).astimezone(_CET)
        # Compute TPO for the most recent trading day
        # Before 22:00 CET = developing (today), after 22:00 = completed (today)
        tpo_date = now_cet.date()
        day_start = datetime(tpo_date.year, tpo_date.month, tpo_date.day, tzinfo=_CET)
        day_end = day_start + timedelta(hours=22)

        start_utc = day_start.astimezone(timezone.utc)
        end_utc = min(day_end, datetime.now(timezone.utc).replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

        rows = self.repo.get_candles(symbol, "1m", start_utc, end_utc)

        class _Bar:
            __slots__ = ("open", "high", "low", "close", "volume")
            def __init__(self, r):
                self.open, self.high, self.low, self.close, self.volume = r.o, r.h, r.l, r.c, r.v
        bars_30m = aggregate_bars_30m([_Bar(r) for r in rows])

        profile = build_full_tpo_profile(bars_30m, tick_size=0.25)
        result = asdict(profile)
        date_str = tpo_date.isoformat()
        result["date"] = date_str

        # Auto-persist completed sessions (after 22:00 CET or weekend)
        session_complete = now_cet.hour >= 22 or now_cet.weekday() >= 5
        if session_complete and len(bars_30m) >= 10:
            persist_key = f"tpo_persisted_{symbol}_{date_str}"
            if not MarketService._tpo_cache.get(persist_key):
                try:
                    self.store_tpo_session(profile, symbol, date_str)
                    MarketService._tpo_cache[persist_key] = (now, True)
                    logger.info("TPO session persisted: %s %s (%d bars)", symbol, date_str, len(bars_30m))
                except Exception:
                    logger.warning("Failed to persist TPO session", exc_info=True)

        # Build timestamped 30m bars for per-session split
        chunk = []
        bars_30m_ts = []
        for r in rows:
            chunk.append(r)
            if len(chunk) == 30:
                bars_30m_ts.append({
                    "ts": chunk[0].ts,
                    "high": max(c.h for c in chunk),
                    "low": min(c.l for c in chunk),
                    "open": chunk[0].o,
                    "close": chunk[-1].c,
                    "volume": sum(c.v for c in chunk),
                })
                chunk = []
        session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=0.25)
        from dataclasses import asdict as _asdict
        result["session_tpos"] = _asdict(session_tpo_set) if session_tpo_set else None

        MarketService._tpo_cache[cache_key] = (now, result)
        return result

    def get_session_tpos(self, symbol: str = "NQ") -> dict:
        """Per-session TPO profiles — piggybacks on get_tpo_live() cache to avoid duplicate DB queries."""
        live = self.get_tpo_live(symbol=symbol)
        session_tpos = live.get("session_tpos")
        if not session_tpos:
            return {"date": live.get("date", ""), "sessions": {"tokyo": None, "london": None, "ny": None}, "poc_migration_tokyo_london": 0, "poc_migration_london_ny": 0}

        def _fix_keys(d):
            """Ensure float dict keys are strings for JSON serialization."""
            if d is None:
                return None
            if "letters" in d:
                d["letters"] = {str(k): v for k, v in d["letters"].items()}
            if "tpo_counts" in d:
                d["tpo_counts"] = {str(k): v for k, v in d["tpo_counts"].items()}
            return d

        return {
            "date": live.get("date", ""),
            "sessions": {
                "tokyo": _fix_keys(session_tpos.get("tokyo")),
                "london": _fix_keys(session_tpos.get("london")),
                "ny": _fix_keys(session_tpos.get("ny")),
            },
            "poc_migration_tokyo_london": session_tpos.get("poc_migration_tokyo_london", 0),
            "poc_migration_london_ny": session_tpos.get("poc_migration_london_ny", 0),
        }

    def backfill_tpo_sessions(self, symbol: str = "NQ", days: int = 30) -> int:
        """Backfill historical TPO sessions from existing 1m bar data."""
        from dataclasses import asdict
        from zoneinfo import ZoneInfo
        from ..db.models import MarketTPOSession

        _CET = ZoneInfo("Europe/Stockholm")
        now = datetime.now(timezone.utc)
        stored = 0

        for offset in range(1, days + 1):
            target = (now - timedelta(days=offset)).astimezone(_CET).date()
            # Skip weekends
            if target.weekday() >= 5:
                continue
            date_str = target.isoformat()

            # Skip if already stored
            existing = self.db.query(MarketTPOSession).filter_by(
                symbol=symbol, date=date_str
            ).first()
            if existing:
                continue

            # Fetch 1m bars for the full CET day (00:00-22:00)
            day_start = datetime(target.year, target.month, target.day, tzinfo=_CET)
            day_end = day_start + timedelta(hours=22)
            rows = self.repo.get_candles(
                symbol, "1m",
                day_start.astimezone(timezone.utc),
                day_end.astimezone(timezone.utc),
            )
            if not rows:
                continue

            class _Bar:
                __slots__ = ("open", "high", "low", "close", "volume")
                def __init__(self, r):
                    self.open, self.high, self.low, self.close, self.volume = r.o, r.h, r.l, r.c, r.v

            bars_30m = aggregate_bars_30m([_Bar(r) for r in rows])
            if len(bars_30m) < 10:
                continue

            # Timestamped 30m bars for per-session split
            chunk = []
            bars_30m_ts = []
            for r in rows:
                chunk.append(r)
                if len(chunk) == 30:
                    bars_30m_ts.append({
                        "ts": chunk[0].ts,
                        "high": max(c.h for c in chunk),
                        "low": min(c.l for c in chunk),
                        "open": chunk[0].o,
                        "close": chunk[-1].c,
                        "volume": sum(c.v for c in chunk),
                    })
                    chunk = []
            session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=0.25)

            profile = build_full_tpo_profile(bars_30m, tick_size=0.25)
            try:
                self.store_tpo_session(profile, symbol, date_str)
                stored += 1
                logger.info("TPO backfill: %s %s (%d bars)", symbol, date_str, len(bars_30m))

                # Append per-session TPO to stored session_json
                from ..db.models import MarketTPOSession
                row = self.db.query(MarketTPOSession).filter_by(
                    symbol=symbol, date=date_str
                ).first()
                if row and session_tpo_set:
                    import json as _json
                    from dataclasses import asdict as _asdict
                    sj = _json.loads(row.session_json) if isinstance(row.session_json, str) else row.session_json
                    sj["session_tpos"] = _asdict(session_tpo_set)
                    row.session_json = _json.dumps(sj, default=str)
                    self.db.commit()
            except Exception:
                logger.warning("TPO backfill failed for %s", date_str, exc_info=True)

        return stored

    # ---- Helper methods for compute_session ----

    @staticmethod
    def _aggregate_bars_30m(bars) -> list[dict]:
        return aggregate_bars_30m(bars)

    @staticmethod
    def _session_levels_to_rows(
        levels: SessionLevels,
        session_data: dict,
    ) -> list[dict]:
        """Convert SessionLevels + session data into MarketLevel row dicts."""
        rows = []

        def _add(level_type, price, direction=None, session_name=None):
            if price is not None:
                rows.append({
                    "level_type": level_type,
                    "price_low": price,
                    "price_high": price,
                    "direction": direction,
                    "session": session_name,
                    "is_filled": False,
                })

        _add("pdh", levels.pdh, "resistance", "prior_day")
        _add("pdl", levels.pdl, "support", "prior_day")
        _add("tokyo_high", levels.tokyo_high, "resistance", "tokyo")
        _add("tokyo_low", levels.tokyo_low, "support", "tokyo")
        _add("london_high", levels.london_high, "resistance", "london")
        _add("london_low", levels.london_low, "support", "london")
        _add("ib_high", levels.ib_high or session_data.get("ib_high"), "resistance", "ib")
        _add("ib_low", levels.ib_low or session_data.get("ib_low"), "support", "ib")
        _add("weekly_high", levels.weekly_high, "resistance", "weekly")
        _add("weekly_low", levels.weekly_low, "support", "weekly")
        _add("monthly_high", levels.monthly_high, "resistance", "monthly")
        _add("monthly_low", levels.monthly_low, "support", "monthly")
        _add("poc", session_data.get("poc"), None, "rth")
        _add("vah", session_data.get("vah"), "resistance", "rth")
        _add("val", session_data.get("val"), "support", "rth")
        _add("vwap", session_data.get("vwap"), None, "rth")

        return rows

    async def _fetch_bars_for_date(self, symbol: str, date_str: str | None) -> list[dict]:
        """Fetch 1-min bars for a specific date from cache/Databento."""
        if not date_str:
            return []
        try:
            provider = _get_provider()
            config = get_market_data_config()
            full_symbol = config.get("symbol", "NQ.FUT")
            sessions_cfg = config.get("sessions", {})
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            globex_start = datetime.combine(
                dt - timedelta(days=1),
                datetime.strptime(sessions_cfg.get("globex_open", "18:00"), "%H:%M").time()
            )
            rth_close = datetime.combine(
                dt,
                datetime.strptime(sessions_cfg.get("rth_close", "16:00"), "%H:%M").time()
            )
            bars = await provider.get_bars(full_symbol, "1m", globex_start, rth_close)
            if not bars:
                return []
            return [{"high": b.high, "low": b.low, "close": b.close, "open": b.open, "volume": b.volume, "timestamp": b.timestamp} for b in bars]
        except Exception as e:
            logger.warning("Failed to fetch bars for %s %s: %s", symbol, date_str, e)
            return []

    async def _fetch_bars_range(self, symbol: str, start_date: str, daily: bool = False) -> list[dict]:
        """Fetch bars from start_date to today. Use daily=True for long ranges."""
        today_str = date.today().isoformat()
        try:
            provider = _get_provider()
            config = get_market_data_config()
            full_symbol = config.get("symbol", "NQ.FUT")
            interval = "1d" if daily else "1m"
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(today_str, "%Y-%m-%d")
            bars = await provider.get_bars(full_symbol, interval, start_dt, end_dt)
            if not bars:
                return []
            return [{"high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
        except Exception as e:
            logger.warning("Failed to fetch bars range %s to %s: %s", start_date, today_str, e)
            return []

    def _get_cot_summary(self) -> dict | None:
        """Get latest COT data from DB."""
        try:
            from sqlalchemy import text
            rows = self.db.execute(
                text("SELECT * FROM cot_reports ORDER BY report_date DESC LIMIT 2")
            ).fetchall()
            if not rows:
                return None
            latest = dict(rows[0]._mapping)
            change_1w = None
            if len(rows) > 1:
                prev = dict(rows[1]._mapping)
                change_1w = (latest.get("net_non_commercial", 0) or 0) - (prev.get("net_non_commercial", 0) or 0)
            return {
                "net_non_commercial": latest.get("net_non_commercial"),
                "change_1w": change_1w,
            }
        except Exception:
            return None

    def _compute_live_orderflow(self, symbol: str, session_data: dict, direction: str | None = None):
        """Compute live orderflow signals. Returns OrderflowSignals or None."""
        try:
            from ..market_data.orderflow import build_candle_flow, compute_signals
            config = get_market_data_config()
            sessions_cfg = config.get("sessions", {})
            today = date.today()
            dt = datetime.combine(today, datetime.strptime(sessions_cfg.get("rth_open", "09:30"), "%H:%M").time())
            session_start = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            recent_ticks = self.repo.get_trades(symbol, start=session_start, end=now)
            if not recent_ticks:
                return None
            tick_dicts = [{"ts": t.ts, "price": t.price, "size": t.size, "side": t.side} for t in recent_ticks]
            candles = build_candle_flow(tick_dicts, period_seconds=60)
            return compute_signals(candles, direction or "long")
        except Exception as e:
            logger.debug("Live orderflow failed: %s", e)
            return None

    def _build_gate_features(self, session_data: dict, session_row) -> dict | None:
        """Build feature dict for ML gate classifier from session data."""
        try:
            if not session_data:
                return None
            return {
                "vix": (session_data.get("macro") or {}).get("vix"),
                "regime_score": (session_data.get("macro") or {}).get("regime_score"),
                "market_type": session_data.get("market_type"),
                "opening_type": session_data.get("opening_type"),
                "total_delta": session_data.get("total_delta"),
                "delta_divergence": session_data.get("delta_divergence"),
                "rotation_factor": getattr(session_row, "rotation_factor", None),
                "aspr_percentile": getattr(session_row, "aspr_percentile", None),
                "price_vs_va": session_data.get("price_vs_va"),
                "price_vs_vwap": session_data.get("price_vs_vwap"),
            }
        except Exception:
            return None
