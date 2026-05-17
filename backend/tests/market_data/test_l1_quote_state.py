from src.market_data.l1_quote_state import L1QuoteState


def test_initial_state_returns_none_snapshot():
    state = L1QuoteState()
    assert state.snapshot() is None


def test_update_then_snapshot_returns_latest_quote():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=12, ask_size=8, ts=1.5)
    snap = state.snapshot()
    assert snap is not None
    assert snap.bid == 25000.0
    assert snap.ask == 25000.25
    assert snap.bid_size == 12
    assert snap.ask_size == 8
    assert snap.ts == 1.5
    assert snap.spread_ticks == 1.0  # (25000.25 - 25000.0) / 0.25


def test_zero_or_negative_sizes_clamp_to_zero():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=-3, ask_size=0, ts=1.0)
    snap = state.snapshot()
    assert snap.bid_size == 0
    assert snap.ask_size == 0


def test_crossed_book_keeps_last_valid():
    """If bid >= ask (e.g. data glitch), don't overwrite a valid state."""
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=25001.0, ask=25000.5, bid_size=10, ask_size=10, ts=2.0)  # crossed
    snap = state.snapshot()
    assert snap.bid == 25000.0  # unchanged
    assert snap.ts == 1.0


def test_equal_bid_ask_rejected():
    """bid == ask is a zero-spread crossed book — must be rejected."""
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=25000.5, ask=25000.5, bid_size=10, ask_size=10, ts=2.0)  # equal
    snap = state.snapshot()
    assert snap.ts == 1.0  # unchanged


def test_zero_bid_rejected():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=0.0, ask=25000.25, bid_size=10, ask_size=10, ts=2.0)
    snap = state.snapshot()
    assert snap.ts == 1.0  # unchanged


def test_zero_ask_rejected():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=25000.0, ask=0.0, bid_size=10, ask_size=10, ts=2.0)
    snap = state.snapshot()
    assert snap.ts == 1.0  # unchanged


def test_nan_bid_rejected():
    """NaN bid must be rejected — NaN comparisons always return False,
    so without an explicit isnan check NaN would slip past bid<=0 / bid>=ask."""
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=float("nan"), ask=25000.25, bid_size=10, ask_size=10, ts=2.0)
    snap = state.snapshot()
    assert snap.ts == 1.0  # unchanged


def test_nan_ask_rejected():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=25000.0, ask=float("nan"), bid_size=10, ask_size=10, ts=2.0)
    snap = state.snapshot()
    assert snap.ts == 1.0  # unchanged


def test_top_of_book_imbalance():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=30, ask_size=10, ts=1.0)
    snap = state.snapshot()
    # (30 - 10) / (30 + 10) = 0.5  (bid-side heavier)
    assert snap.top_imbalance == 0.5


def test_top_of_book_imbalance_ask_heavy():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=30, ts=1.0)
    snap = state.snapshot()
    # (10 - 30) / (10 + 30) = -0.5  (ask-side heavier)
    assert snap.top_imbalance == -0.5


def test_top_of_book_imbalance_zero_sizes():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=0, ask_size=0, ts=1.0)
    snap = state.snapshot()
    assert snap.top_imbalance == 0.0
