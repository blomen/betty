"""Options flow and macro data fetcher.

Fetches daily macro regime data: VIX, DXY, US Treasury yields.
Computes derived features like yield curve spread.
Stores results to the options_flow table via SQLAlchemy session.
"""

import logging
from datetime import date as date_type

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "NQ"


def build_options_flow_row(
    date: str,
    vix_level: float | None = None,
    vix_1d_change: float | None = None,
    dxy_level: float | None = None,
    dxy_1d_change: float | None = None,
    us10y_level: float | None = None,
    us10y_1d_change: float | None = None,
    us02y_level: float | None = None,
    gex: float | None = None,
    gex_flip_level: float | None = None,
    net_options_delta: float | None = None,
    put_call_ratio: float | None = None,
    total_options_volume: float | None = None,
    vix_term_structure: str | None = None,
    es_nq_ratio: float | None = None,
    symbol: str = DEFAULT_SYMBOL,
) -> dict:
    """Build a dict suitable for inserting into the options_flow table.

    Computes yield_curve_spread = us10y_level - us02y_level when both are provided.
    """
    yield_curve_spread = None
    if us10y_level is not None and us02y_level is not None:
        yield_curve_spread = round(us10y_level - us02y_level, 6)

    return {
        "date": date,
        "symbol": symbol,
        "vix_level": vix_level,
        "vix_1d_change": vix_1d_change,
        "dxy_level": dxy_level,
        "dxy_1d_change": dxy_1d_change,
        "us10y_level": us10y_level,
        "us10y_1d_change": us10y_1d_change,
        "us02y_level": us02y_level,
        "yield_curve_spread": yield_curve_spread,
        "gex": gex,
        "gex_flip_level": gex_flip_level,
        "net_options_delta": net_options_delta,
        "put_call_ratio": put_call_ratio,
        "total_options_volume": total_options_volume,
        "vix_term_structure": vix_term_structure,
        "es_nq_ratio": es_nq_ratio,
    }


async def fetch_and_store_daily(session) -> dict | None:
    """Fetch today's macro data via yfinance and store to options_flow table.

    Returns the row dict if successful, None on error.
    """
    try:
        import yfinance as yf

        from src.db.models import OptionsFlow

        today = str(date_type.today())

        # Fetch tickers: VIX, DXY, 10Y yield, 2Y yield
        tickers = yf.download(
            ["^VIX", "DX-Y.NYB", "^TNX", "^IRX"],
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )

        def _latest(ticker: str) -> float | None:
            try:
                closes = tickers["Close"][ticker].dropna()
                return float(closes.iloc[-1]) if len(closes) > 0 else None
            except Exception:
                return None

        def _1d_change(ticker: str) -> float | None:
            try:
                closes = tickers["Close"][ticker].dropna()
                if len(closes) >= 2:
                    return round(float(closes.iloc[-1]) - float(closes.iloc[-2]), 4)
                return None
            except Exception:
                return None

        vix_level = _latest("^VIX")
        vix_1d_change = _1d_change("^VIX")
        dxy_level = _latest("DX-Y.NYB")
        dxy_1d_change = _1d_change("DX-Y.NYB")
        # TNX is quoted as yield × 10 in yfinance (e.g. 42.5 = 4.25%)
        us10y_raw = _latest("^TNX")
        us10y_level = round(us10y_raw / 10, 4) if us10y_raw else None
        us10y_1d_raw = _1d_change("^TNX")
        us10y_1d_change = round(us10y_1d_raw / 10, 4) if us10y_1d_raw else None
        # IRX is 13-week T-bill (closest to 2Y for simplicity)
        us02y_raw = _latest("^IRX")
        us02y_level = round(us02y_raw / 10, 4) if us02y_raw else None

        row_dict = build_options_flow_row(
            date=today,
            vix_level=vix_level,
            vix_1d_change=vix_1d_change,
            dxy_level=dxy_level,
            dxy_1d_change=dxy_1d_change,
            us10y_level=us10y_level,
            us10y_1d_change=us10y_1d_change,
            us02y_level=us02y_level,
        )

        # Upsert: skip if row already exists for today
        existing = session.query(OptionsFlow).filter_by(date=today, symbol=DEFAULT_SYMBOL).first()
        if existing is None:
            row = OptionsFlow(**row_dict)
            session.add(row)
            session.flush()
            logger.info("Stored options_flow row for %s", today)
        else:
            logger.debug("options_flow row already exists for %s, skipping", today)

        return row_dict

    except Exception as e:
        logger.error("fetch_and_store_daily failed: %s", e)
        return None
