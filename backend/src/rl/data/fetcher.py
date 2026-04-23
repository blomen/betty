"""Databento historical tick data fetcher for NQ futures.

Downloads tick-by-tick trade data month by month and saves as Parquet files.
Also fetches macro data (VIX, DXY, US10Y, US2Y) from yfinance.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "rl"
TICKS_DIR = DATA_DIR / "ticks"
MACRO_DIR = DATA_DIR / "macro"

# Databento dataset / symbol constants
_DATASET = "GLBX.MDP3"
_SYMBOL = "NQ.v.0"  # continuous front-month (volume roll — matches TradingView NQ1!)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_ticks(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
    api_key: str | None = None,
) -> list[Path]:
    """Download NQ tick data from Databento historical API month by month.

    Each month is saved as ``NQ_YYYY-MM.parquet`` inside *output_dir* (defaults
    to ``TICKS_DIR``).  Files that already exist are skipped.

    Args:
        start: Inclusive start datetime (timezone-aware recommended).
        end: Exclusive end datetime.
        output_dir: Directory to write Parquet files into.  Created if absent.
        api_key: Databento API key.  Falls back to ``DATABENTO_API_KEY`` env var.

    Returns:
        List of Path objects for all files that were written (skipped files are
        not included).
    """
    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed — pip install databento")
        return []

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas package not installed — pip install pandas pyarrow")
        return []

    key = api_key or os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        raise ValueError("Databento API key not provided and DATABENTO_API_KEY env var not set")

    out_dir = output_dir or TICKS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    client = db.Historical(key=key)

    # Normalise to UTC-aware datetimes
    start_utc = _to_utc(start)
    end_utc = _to_utc(end)

    written: list[Path] = []

    for month_start, month_end in _month_ranges(start_utc, end_utc):
        month_label = month_start.strftime("%Y-%m")
        out_path = out_dir / f"NQ_{month_label}.parquet"

        if out_path.exists():
            logger.info("Skipping %s — already exists", out_path.name)
            continue

        logger.info(
            "Fetching ticks %s → %s …",
            month_start.date(),
            month_end.date(),
        )

        try:
            data = client.timeseries.get_range(
                dataset=_DATASET,
                symbols=[_SYMBOL],
                stype_in="continuous",
                schema="trades",
                start=month_start.isoformat(),
                end=month_end.isoformat(),
            )
        except Exception as exc:
            logger.error("Databento request failed for %s: %s", month_label, exc)
            continue

        rows: list[dict] = []
        tick_rule_count = 0
        prev_price: float = 0.0
        for rec in data:
            side_raw = getattr(rec, "side", "")
            side_char = side_raw.value if hasattr(side_raw, "value") else str(side_raw)

            ts_raw = rec.ts_event if hasattr(rec, "ts_event") else rec.hd.ts_event
            ts = datetime.fromtimestamp(int(ts_raw) / 1e9, tz=timezone.utc)
            price = rec.price / 1e9

            if side_char not in ("A", "B"):
                # Tick rule: infer side from price change
                # Uptick → buy aggressor (A), downtick → sell aggressor (B)
                # Same price → inherit from previous tick
                if price > prev_price:
                    side_char = "A"
                elif price < prev_price:
                    side_char = "B"
                else:
                    side_char = rows[-1]["side"] if rows else "A"
                tick_rule_count += 1

            prev_price = price

            rows.append(
                {
                    "timestamp": ts,
                    "price": price,
                    "size": int(rec.size),
                    "side": side_char,
                }
            )

        if tick_rule_count:
            logger.info(
                "%s: inferred side via tick rule for %d/%d ticks (%.0f%%)",
                month_label,
                tick_rule_count,
                len(rows),
                tick_rule_count / max(len(rows), 1) * 100,
            )

        if not rows:
            logger.warning("No valid ticks for %s — file not written", month_label)
            continue

        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.to_parquet(out_path, index=False)

        logger.info("Wrote %d ticks to %s", len(df), out_path.name)
        written.append(out_path)

    return written


def fetch_macro_history(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
) -> "Path | None":
    """Fetch VIX, DXY, US10Y, US2Y daily closes from yfinance.

    Saves a single ``macro_daily.parquet`` in *output_dir* (defaults to
    ``MACRO_DIR``).  Returns the path on success, ``None`` if yfinance is
    unavailable or the download fails.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — macro data unavailable.  pip install yfinance to enable.")
        return None

    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas not installed — cannot save macro data")
        return None

    out_dir = output_dir or MACRO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "macro_daily.parquet"

    tickers = {
        "VIX": "^VIX",
        "DXY": "DX-Y.NYB",
        "US10Y": "^TNX",
        "US2Y": "^IRX",
    }

    start_str = _to_utc(start).strftime("%Y-%m-%d")
    end_str = _to_utc(end).strftime("%Y-%m-%d")

    frames: list = []
    for col_name, ticker in tickers.items():
        try:
            raw = yf.download(ticker, start=start_str, end=end_str, progress=False)
            if raw.empty:
                logger.warning("yfinance returned no data for %s (%s)", col_name, ticker)
                continue
            close = raw["Close"]
            # yfinance >= 1.0 returns MultiIndex columns (Price, Ticker)
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            series = close.rename(col_name)
            frames.append(series)
            logger.info("Fetched %d rows for %s", len(series), col_name)
        except Exception as exc:
            logger.warning("Failed to fetch %s (%s): %s", col_name, ticker, exc)

    if not frames:
        logger.error("No macro data fetched — file not written")
        return None

    df = pd.concat(frames, axis=1)
    df.index.name = "date"
    df.sort_index(inplace=True)
    df.to_parquet(out_path)

    logger.info("Wrote macro data (%d rows, %d columns) to %s", len(df), len(df.columns), out_path.name)
    return out_path


def fetch_cot_history(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
    cftc_code: str = "209742",
) -> "Path | None":
    """Fetch weekly CFTC COT data for NQ futures.

    Saves ``cot_weekly.parquet`` with net_position and open_interest columns.
    Returns the path on success, None on failure.
    """
    import httpx

    out_dir = output_dir or MACRO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cot_weekly.parquet"

    start_str = _to_utc(start).strftime("%Y-%m-%dT00:00:00")
    end_str = _to_utc(end).strftime("%Y-%m-%dT23:59:59")

    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    params = {
        "$where": (
            f"cftc_contract_market_code='{cftc_code}' "
            f"AND report_date_as_yyyy_mm_dd >= '{start_str}' "
            f"AND report_date_as_yyyy_mm_dd <= '{end_str}'"
        ),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "5000",
    }

    try:
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        logger.error("COT history fetch failed: %s", exc)
        return None

    if not rows:
        logger.warning("No COT data returned for %s – %s", start_str[:10], end_str[:10])
        return None

    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas not installed — cannot save COT data")
        return None

    records = []
    for row in rows:
        date_str = row.get("report_date_as_yyyy_mm_dd", "")[:10]
        net_nc = int(row.get("noncomm_positions_long_all", 0)) - int(row.get("noncomm_positions_short_all", 0))
        oi = int(row.get("open_interest_all", 0))
        records.append({"date": date_str, "cot_net_position": net_nc, "cot_open_interest": oi})

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)

    # Compute week-over-week change
    df["cot_net_change"] = df["cot_net_position"].diff()

    df.to_parquet(out_path)
    logger.info("Wrote COT history (%d weeks) to %s", len(df), out_path.name)
    return out_path


def fetch_statistics_history(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
    api_key: str | None = None,
) -> "Path | None":
    """Fetch daily CME statistics (OI, settlement, cleared/block volume) from Databento.

    Saves ``statistics_daily.parquet`` with columns:
        date, open_interest, cleared_volume, block_volume, settlement_price, oi_change

    Uses the ``statistics`` schema on GLBX.MDP3, filtering for relevant StatTypes.
    Groups by trading date (from ts_ref).
    """
    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed — pip install databento")
        return None

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas package not installed — pip install pandas pyarrow")
        return None

    key = api_key or os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        raise ValueError("Databento API key not provided and DATABENTO_API_KEY env var not set")

    out_dir = output_dir or MACRO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "statistics_daily.parquet"

    client = db.Historical(key=key)

    try:
        data = client.timeseries.get_range(
            dataset=_DATASET,
            symbols=[_SYMBOL],
            stype_in="continuous",
            schema="statistics",
            start=start.isoformat(),
            end=end.isoformat(),
        )
    except Exception as exc:
        logger.error("Databento statistics fetch failed: %s", exc)
        return None

    from databento_dbn import StatType

    _QUANTITY_TYPES = {
        StatType.OPEN_INTEREST: "open_interest",
        StatType.CLEARED_VOLUME: "cleared_volume",
        StatType.BLOCK_VOLUME: "block_volume",
    }
    _PRICE_TYPES = {
        StatType.SETTLEMENT_PRICE: "settlement_price",
    }

    # Collect per-date stats
    daily: dict[str, dict] = {}  # date_str -> {col: value}
    for rec in data:
        st = rec.stat_type
        # Use ts_ref for the trading date this stat applies to
        ts_ref = rec.ts_ref if hasattr(rec, "ts_ref") else rec.hd.ts_event
        date_str = datetime.fromtimestamp(int(ts_ref) / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")

        if date_str not in daily:
            daily[date_str] = {}

        if st in _QUANTITY_TYPES:
            daily[date_str][_QUANTITY_TYPES[st]] = rec.quantity
        elif st in _PRICE_TYPES:
            daily[date_str][_PRICE_TYPES[st]] = rec.price / 1e9

    if not daily:
        logger.warning("No statistics data returned for %s – %s", start.date(), end.date())
        return None

    df = pd.DataFrame.from_dict(daily, orient="index")
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df.sort_index(inplace=True)

    # Fill missing columns with 0
    for col in ("open_interest", "cleared_volume", "block_volume", "settlement_price"):
        if col not in df.columns:
            df[col] = 0

    # Compute day-over-day OI change
    df["oi_change"] = df["open_interest"].diff()

    df.to_parquet(out_path)
    logger.info("Wrote statistics history (%d days) to %s", len(df), out_path.name)
    return out_path


def load_ticks(
    date_or_month: str,
    ticks_dir: Path | None = None,
) -> list[dict]:
    """Load tick records from Parquet for a given month or specific date.

    Args:
        date_or_month: Either ``"YYYY-MM"`` (full month) or ``"YYYY-MM-DD"``
            (single day).
        ticks_dir: Directory containing ``NQ_YYYY-MM.parquet`` files.  Defaults
            to ``TICKS_DIR``.

    Returns:
        List of tick dicts sorted by timestamp.  Each dict has keys:
        ``timestamp``, ``price``, ``size``, ``side``.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas not installed — cannot load ticks")
        return []

    src_dir = ticks_dir or TICKS_DIR

    # Determine which month file to open and whether to filter to a single date
    parts = date_or_month.strip().split("-")
    if len(parts) == 2:
        # "YYYY-MM"
        month_label = date_or_month
        filter_date = None
    elif len(parts) == 3:
        # "YYYY-MM-DD"
        month_label = f"{parts[0]}-{parts[1]}"
        filter_date = date_or_month
    else:
        raise ValueError(f"date_or_month must be 'YYYY-MM' or 'YYYY-MM-DD', got {date_or_month!r}")

    file_path = src_dir / f"NQ_{month_label}.parquet"
    if not file_path.exists():
        logger.warning("Tick file not found: %s", file_path)
        return []

    df = pd.read_parquet(file_path)

    if filter_date is not None:
        # Filter to rows whose timestamp date matches
        ts_col = df["timestamp"]
        if hasattr(ts_col, "dt"):
            date_series = ts_col.dt.date.astype(str)
        else:
            date_series = ts_col.apply(lambda t: str(t.date()) if hasattr(t, "date") else str(t)[:10])
        df = df[date_series == filter_date]

    df = df.sort_values("timestamp").reset_index(drop=True)
    return df.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _month_ranges(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Return a list of (month_start, month_end) pairs covering [start, end).

    Each pair is clamped to the actual start/end boundaries.
    """
    import calendar
    from datetime import timedelta

    ranges: list[tuple[datetime, datetime]] = []

    current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end:
        _, last_day = calendar.monthrange(current.year, current.month)
        next_month = current.replace(day=last_day) + timedelta(days=1)
        next_month = next_month.replace(hour=0, minute=0, second=0, microsecond=0)

        seg_start = max(current, start)
        seg_end = min(next_month, end)

        ranges.append((seg_start, seg_end))
        current = next_month

    return ranges
