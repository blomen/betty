"""Macro data provider — fetches VIX, DXY, US yields from Yahoo Finance.

Uses yfinance for free, no-API-key access to macro indicators.
Falls back gracefully if data unavailable.
"""

import logging
from datetime import datetime, timedelta

from .amt import MacroSnapshot

logger = logging.getLogger(__name__)


# Yahoo Finance tickers for macro data
MACRO_TICKERS = {
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "us2y": "^IRX",  # 13-week T-bill as proxy; actual 2Y not on Yahoo
}


async def fetch_macro_snapshot() -> MacroSnapshot:
    """Fetch current macro data and classify regime.

    Uses yfinance (sync) wrapped in async. Returns MacroSnapshot
    with VIX, DXY, yields, and regime classification.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_macro_sync)


def _fetch_macro_sync() -> MacroSnapshot:
    """Synchronous macro data fetch via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — macro data unavailable")
        return MacroSnapshot(regime="unknown", fetched_at=datetime.now().isoformat())

    snapshot = MacroSnapshot(fetched_at=datetime.now().isoformat())

    try:
        # Fetch 5 days of data to compute day-over-day changes
        end = datetime.now()
        start = end - timedelta(days=7)

        def _scalar(df, col, idx):
            """Extract scalar from yfinance DataFrame (handles MultiIndex columns)."""
            val = df[col].iloc[idx]
            # yf.download with single ticker may return MultiIndex columns
            if hasattr(val, "item"):
                return val.item()
            return float(val)

        # VIX
        try:
            vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=True)
            if not vix.empty and len(vix) >= 2:
                snapshot.vix = _scalar(vix, "Close", -1)
                prev_vix = _scalar(vix, "Close", -2)
                if prev_vix > 0:
                    snapshot.vix_change_pct = round((snapshot.vix - prev_vix) / prev_vix * 100, 2)
        except Exception as e:
            logger.debug("VIX fetch failed: %s", e)

        # DXY (US Dollar Index)
        try:
            dxy = yf.download("DX-Y.NYB", start=start, end=end, progress=False, auto_adjust=True)
            if not dxy.empty and len(dxy) >= 2:
                snapshot.dxy = _scalar(dxy, "Close", -1)
                prev_dxy = _scalar(dxy, "Close", -2)
                if prev_dxy > 0:
                    snapshot.dxy_change_pct = round((snapshot.dxy - prev_dxy) / prev_dxy * 100, 2)
        except Exception as e:
            logger.debug("DXY fetch failed: %s", e)

        # US 10Y yield (^TNX gives yield × 10, e.g., 45.2 = 4.52%)
        try:
            tnx = yf.download("^TNX", start=start, end=end, progress=False, auto_adjust=True)
            if not tnx.empty and len(tnx) >= 2:
                snapshot.us10y = round(_scalar(tnx, "Close", -1) / 10, 3)
                prev_10y = _scalar(tnx, "Close", -2) / 10
                snapshot.us10y_change_bps = round((snapshot.us10y - prev_10y) * 100, 1)
        except Exception as e:
            logger.debug("US10Y fetch failed: %s", e)

        # US 2Y yield (2-year Treasury futures)
        try:
            two = yf.download("2YY=F", start=start, end=end, progress=False, auto_adjust=True)
            if not two.empty and len(two) >= 1:
                snapshot.us2y = round(_scalar(two, "Close", -1), 3)
                # Day-over-day change in basis points (matches us10y semantics)
                if len(two) >= 2:
                    prev_2y = _scalar(two, "Close", -2)
                    snapshot.us2y_change_bps = round((snapshot.us2y - prev_2y) * 100, 1)
        except Exception as e:
            logger.debug("US2Y fetch failed: %s", e)

        # Yield curve spread
        if snapshot.us10y is not None and snapshot.us2y is not None:
            snapshot.yield_curve_spread = round(snapshot.us10y - snapshot.us2y, 3)

    except Exception as e:
        logger.error("Macro fetch error: %s", e)

    # Classify regime
    snapshot.regime, snapshot.regime_score = classify_regime(snapshot)

    return snapshot


def classify_regime(macro: MacroSnapshot) -> tuple[str, float]:
    """Classify macro environment as risk-on, risk-off, or mixed.

    Scoring: each indicator contributes to a -1 to +1 scale.
    - VIX < 15 = risk-on, VIX > 25 = risk-off
    - VIX rising > 10% = risk-off signal
    - DXY rising = risk-off (flight to dollar)
    - Yields rising sharply = risk-off for growth/tech
    - Yield curve inversion = risk-off

    Returns (regime_label, score) where score: -1.0=max risk-off, +1.0=max risk-on.
    """
    signals: list[float] = []

    # VIX level
    if macro.vix is not None:
        if macro.vix < 15:
            signals.append(0.8)  # Low VIX = risk-on
        elif macro.vix < 20:
            signals.append(0.3)
        elif macro.vix < 25:
            signals.append(-0.3)
        elif macro.vix < 30:
            signals.append(-0.7)
        else:
            signals.append(-1.0)  # Panic

    # VIX change
    if macro.vix_change_pct is not None:
        if macro.vix_change_pct > 15:
            signals.append(-0.8)  # VIX spike = risk-off
        elif macro.vix_change_pct > 5:
            signals.append(-0.4)
        elif macro.vix_change_pct < -10:
            signals.append(0.6)  # VIX crush = risk-on
        elif macro.vix_change_pct < -3:
            signals.append(0.3)

    # DXY change (rising dollar = risk-off for equities)
    if macro.dxy_change_pct is not None:
        if macro.dxy_change_pct > 0.5:
            signals.append(-0.4)
        elif macro.dxy_change_pct < -0.5:
            signals.append(0.4)

    # Yield change (sharp rise = negative for growth/NQ)
    if macro.us10y_change_bps is not None:
        if macro.us10y_change_bps > 10:
            signals.append(-0.6)  # Sharp yield rise = risk-off for tech
        elif macro.us10y_change_bps > 5:
            signals.append(-0.3)
        elif macro.us10y_change_bps < -10:
            signals.append(0.5)  # Yields falling = risk-on for growth
        elif macro.us10y_change_bps < -5:
            signals.append(0.2)

    # Yield curve
    if macro.yield_curve_spread is not None:
        if macro.yield_curve_spread < -0.5:
            signals.append(-0.5)  # Deep inversion
        elif macro.yield_curve_spread < 0:
            signals.append(-0.2)  # Mild inversion
        elif macro.yield_curve_spread > 0.5:
            signals.append(0.3)  # Normal curve

    if not signals:
        return "unknown", 0.0

    score = sum(signals) / len(signals)
    score = max(-1.0, min(1.0, score))

    if score > 0.25:
        regime = "risk_on"
    elif score < -0.25:
        regime = "risk_off"
    else:
        regime = "mixed"

    return regime, round(score, 2)
