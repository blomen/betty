"""Market service - orchestrates data fetch, AMT analysis, and scanning."""

import logging
import os
from datetime import datetime, date, timedelta, timezone

from sqlalchemy.orm import Session

from ..config.trading_loader import get_market_data_config, get_scanner_config, get_setups
from ..market_data.amt import build_session_analysis, SessionAnalysis
from ..market_data.base import MarketDataProvider
from ..market_data.macro_provider import fetch_macro_snapshot
from ..market_data.scanner import MarketScanner
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
        )
        self.db.commit()

        logger.info("Computed session for %s %s: POC=%.2f, VA=%.2f-%.2f",
                     symbol, target_date,
                     session_data.get("poc", 0),
                     session_data.get("val", 0),
                     session_data.get("vah", 0))

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

        self.db.commit()
        logger.info("Scan produced %d signals above threshold %.0f", len(results), threshold)
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
