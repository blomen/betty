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
        skipped = 0
        for rec in data:
            side_raw = getattr(rec, "side", "")
            # Databento SDK returns Side enum (Side.ASK, Side.BID, Side.NONE)
            side_char = side_raw.value if hasattr(side_raw, "value") else str(side_raw)
            if side_char not in ("A", "B"):
                if side_char != "":
                    logger.warning(
                        "Unknown side value %r on tick — skipping", side_char
                    )
                skipped += 1
                continue

            ts_raw = rec.ts_event if hasattr(rec, "ts_event") else rec.hd.ts_event
            ts = datetime.fromtimestamp(int(ts_raw) / 1e9, tz=timezone.utc)

            rows.append(
                {
                    "timestamp": ts,
                    "price": rec.price / 1e9,
                    "size": int(rec.size),
                    "side": side_char,  # "A" = ask/buy aggressor, "B" = bid/sell aggressor
                }
            )

        if skipped:
            logger.warning(
                "%s: skipped %d tick(s) with unknown/missing side", month_label, skipped
            )

        if not rows:
            logger.warning("No valid ticks for %s — file not written", month_label)
            continue

        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.to_parquet(out_path, index=False)

        logger.info(
            "Wrote %d ticks to %s", len(df), out_path.name
        )
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
        logger.warning(
            "yfinance not installed — macro data unavailable.  "
            "pip install yfinance to enable."
        )
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
            series = raw["Close"].rename(col_name)
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
        raise ValueError(
            f"date_or_month must be 'YYYY-MM' or 'YYYY-MM-DD', got {date_or_month!r}"
        )

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


def _month_ranges(
    start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """Return a list of (month_start, month_end) pairs covering [start, end).

    Each pair is clamped to the actual start/end boundaries.
    """
    from datetime import timedelta
    import calendar

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
