"""Verify the scheduler settlement tick invokes the opp-CLV backfill."""

from unittest.mock import patch

import pytest  # noqa: F401  # kept for consistency with sibling pipeline tests


def test_run_settlement_calls_opp_clv_backfill(db_session):
    """_run_settlement should call OppSnapshotService.compute_closing_clv
    after BetService.snapshot_closing_odds."""
    from src.pipeline.scheduler import ExtractionScheduler

    sched = ExtractionScheduler.__new__(ExtractionScheduler)  # bypass __init__; we only need the method

    with (
        patch("src.db.models.get_session", return_value=db_session),
        patch(
            "src.services.bet_service.BetService.snapshot_closing_odds", return_value={"processed": 0, "updated": 0}
        ) as bet_mock,
        patch(
            "src.services.opp_snapshot_service.OppSnapshotService.compute_closing_clv",
            return_value={"processed": 0, "updated": 0},
        ) as opp_mock,
    ):
        result = sched._run_settlement()

    assert bet_mock.called, "bet CLV must still be invoked"
    assert opp_mock.called, "opp CLV backfill must be invoked"
    # Both calls are commutative on the current data model (neither mutates Odds),
    # so we only assert each fires exactly once per tick.
    assert bet_mock.call_count == 1
    assert opp_mock.call_count == 1
    assert "bet_clv" in result
    assert "opp_clv" in result
