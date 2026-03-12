"""Market service - orchestrates data fetch, AMT analysis, and scanning."""

import json
import logging
import os
from datetime import datetime, date, timedelta, timezone

from sqlalchemy.orm import Session

from ..config.trading_loader import get_market_data_config, get_scanner_config, get_setups
from ..db.models import TradingSignal
from ..market_data.amt import build_session_analysis, SessionAnalysis
from ..market_data.base import MarketDataProvider
from ..market_data.levels import compute_session_levels, compute_volume_profile, compute_vwap_bands, VolumeProfile, VWAPBands, SessionLevels
from ..market_data.macro_provider import fetch_macro_snapshot
from ..market_data.metrics import compute_rotation_factor, compute_aspr, compute_aspr_percentile, detect_value_migration
from ..market_data.orderflow import build_candle_flow, compute_signals
from ..market_data.scanner import MarketScanner
from ..market_data.scoring import score_candidate, day_type_fits_setup, filter_by_rr
from ..market_data.setups.detector import DetectorContext, run_all_detectors
from ..market_data.tpo import compute_tpo_profile
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

    async def compute_session(
        self, target_date: str | None = None, symbol: str | None = None
    ) -> dict:
        """Fetch market data → run AMT analysis → persist to DB."""
        config = get_market_data_config()
        symbol = symbol or config.get("symbol", "NQ.FUT").split(".")[0]
        target_date = target_date or date.today().isoformat()

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

        # Fetch current day data
        bars = await provider.get_bars(
            config.get("symbol", "NQ.FUT"), "1m", globex_start, rth_close
        )
        ticks = await provider.get_ticks(
            config.get("symbol", "NQ.FUT"), globex_start, rth_close
        )

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
        prev_bars = await provider.get_bars(
            config.get("symbol", "NQ.FUT"), "1m", prev_globex, prev_close
        )

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
        bars_1m = [
            {"ts": b.timestamp, "high": b.high, "low": b.low}
            for b in bars
        ]
        session_date = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        session_levels = compute_session_levels(bars_1m, session_date)

        # --- New: Aggregate to 30-min bars for TPO and metrics ---
        bars_30m = self._aggregate_bars_30m(bars)

        # TPO profile from 30-min bars
        tpo = compute_tpo_profile(bars_30m)

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

        # Run scanner
        setups = get_setups()
        scanner = MarketScanner(setups, threshold)
        signals = scanner.scan(analysis)

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

        # Get context gates
        ctx_model = self.repo.get_context(symbol)
        direction = "long"
        if ctx_model and ctx_model.macro_bias == "bear":
            direction = "short"
        elif ctx_model and ctx_model.macro_bias == "bull":
            direction = "long"

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
            macro_bias=ctx_model.macro_bias if ctx_model else None,
            structure=ctx_model.structure if ctx_model else None,
            day_type=ctx_model.day_type if ctx_model else None,
        )

        # Run all detectors
        raw_candidates = run_all_detectors(detector_ctx)

        # Score and filter
        scored = []
        for c in raw_candidates:
            fits = day_type_fits_setup(detector_ctx.day_type, c.setup_type)
            macro_ok = (
                (c.direction == "long" and detector_ctx.macro_bias != "bear") or
                (c.direction == "short" and detector_ctx.macro_bias != "bull")
            )
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
                "direction": s.direction,
                "score": s.score,
                "conditions": s.conditions,
                "price_at_signal": s.price_at_signal,
                "suggested_entry": s.suggested_entry,
                "suggested_stop": s.suggested_stop,
                "suggested_target": s.suggested_target,
                "vwap": s.vwap,
                "poc": s.poc,
                "triggered_at": s.triggered_at.isoformat() if s.triggered_at else None,
                "trade_id": s.trade_id,
            }
            for s in signals
        ]

    def get_confirmations(self, symbol: str | None = None) -> dict:
        """Evaluate 4 confirmation gates from current session data."""
        session_data = self.get_current_session(symbol)
        if not session_data:
            return {
                "macro": {"checked": False, "regime": "unknown", "vix": None},
                "span": {"checked": False, "structure": "no_data"},
                "fair_value": {"checked": False, "deviation_sd": None, "price_vs_va": "unknown"},
                "orderflow": {"checked": False, "delta": None, "divergence": False},
            }

        # Macro: risk_on = checked
        macro = session_data.get("macro") or {}
        regime = macro.get("regime", "unknown")
        macro_checked = regime == "risk_on"

        # Span: check market_type for trending structure
        market_type = session_data.get("market_type", "unknown")
        if market_type in ("trending_up", "trending_down"):
            span_checked = True
            span_structure = "bullish" if market_type == "trending_up" else "bearish"
        else:
            span_checked = False
            span_structure = "no_clear_structure"

        # Fair Value: price beyond 1.5SD from VWAP or outside VA
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

        # Orderflow: delta confirms direction (nonzero + matches trend)
        total_delta = session_data.get("total_delta")
        divergence = session_data.get("delta_divergence", False)
        of_checked = False
        if total_delta is not None:
            if market_type == "trending_up" and total_delta > 0:
                of_checked = True
            elif market_type == "trending_down" and total_delta < 0:
                of_checked = True
            elif divergence:
                of_checked = True

        return {
            "macro": {"checked": macro_checked, "regime": regime, "vix": macro.get("vix")},
            "span": {"checked": span_checked, "structure": span_structure},
            "fair_value": {"checked": fv_checked, "deviation_sd": deviation_sd, "price_vs_va": price_vs_va},
            "orderflow": {"checked": of_checked, "delta": total_delta, "divergence": divergence},
        }

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

    # ---- Helper methods for compute_session ----

    @staticmethod
    def _aggregate_bars_30m(bars) -> list[dict]:
        """Aggregate 1-min BarData objects into 30-min OHLCV dicts."""
        if not bars:
            return []
        result = []
        chunk = []
        for b in bars:
            chunk.append(b)
            if len(chunk) == 30:
                result.append({
                    "high": max(c.high for c in chunk),
                    "low": min(c.low for c in chunk),
                    "open": chunk[0].open,
                    "close": chunk[-1].close,
                    "volume": sum(c.volume for c in chunk),
                })
                chunk = []
        # Handle remaining bars
        if chunk:
            result.append({
                "high": max(c.high for c in chunk),
                "low": min(c.low for c in chunk),
                "open": chunk[0].open,
                "close": chunk[-1].close,
                "volume": sum(c.volume for c in chunk),
            })
        return result

    @staticmethod
    def _session_levels_to_rows(levels: SessionLevels, session_data: dict) -> list[dict]:
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
