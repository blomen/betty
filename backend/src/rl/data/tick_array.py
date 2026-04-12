"""Memory-efficient tick storage using column arrays instead of list[dict].

A 848K-tick session uses ~80 MB as TickArray vs ~620 MB as list[dict] + norm_ticks.
TickView provides dict-like access (`tick["price"]`, `tick.get("side", "B")`)
so existing code (ReplayEngine, episode_builder, micro_features) works unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class TickView:
    """Zero-copy view into a single tick within a TickArray.

    Supports dict-like access: tick["price"], tick["ts"], tick.get("side", "B").
    """

    __slots__ = ("_arr", "_idx")

    def __init__(self, arr: TickArray, idx: int) -> None:
        self._arr = arr
        self._idx = idx

    def __getitem__(self, key: str):
        if key == "price":
            return float(self._arr.price[self._idx])
        if key == "ts":
            return self._arr.ts[self._idx]
        if key == "size":
            return int(self._arr.size[self._idx])
        if key == "side":
            return self._arr.side[self._idx]
        raise KeyError(key)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class TickArray:
    """Column-oriented tick storage with dict-like element access.

    Wraps four numpy arrays (ts, price, size, side) and supports:
    - Indexing: ``ticks[i]`` returns a ``TickView``
    - Slicing: ``ticks[10:20]`` returns a new ``TickArray``
    - Iteration: ``for tick in ticks`` yields ``TickView`` objects
    - ``len(ticks)``

    All downstream code that does ``tick["price"]`` or ``tick.get("side")``
    works without modification.
    """

    __slots__ = ("ts", "price", "size", "side", "_len")

    def __init__(
        self,
        ts: np.ndarray,
        price: np.ndarray,
        size: np.ndarray,
        side: np.ndarray,
    ) -> None:
        self.ts = ts  # object array of datetime (UTC-aware)
        self.price = price  # float64
        self.size = size  # int64
        self.side = side  # object array of str ("A"/"B")
        self._len = len(price)

    def __len__(self) -> int:
        return self._len

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            return TickArray(
                self.ts[idx],
                self.price[idx],
                self.size[idx],
                self.side[idx],
            )
        return TickView(self, idx)

    def __iter__(self):
        for i in range(self._len):
            yield TickView(self, i)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> TickArray:
        """Build from a DataFrame with columns: ts, price, size, side.

        Accepts either 'ts' or 'timestamp' as the time column name.
        Timestamps are converted to Python datetime objects for compatibility
        with existing code that calls ``tick["ts"].astimezone()``.
        """
        ts_col = "ts" if "ts" in df.columns else "timestamp"
        ts_series = pd.to_datetime(df[ts_col], utc=True)
        # Convert to Python datetime objects (needed for .astimezone() calls)
        ts_arr = ts_series.dt.to_pydatetime()

        return cls(
            ts=ts_arr,
            price=df["price"].to_numpy(dtype=np.float64),
            size=df["size"].to_numpy(dtype=np.int64),
            side=df["side"].to_numpy(copy=True),
        )
