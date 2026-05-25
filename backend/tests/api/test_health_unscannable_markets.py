"""/health/extraction surfaces an unscannable_markets count for visibility."""

from __future__ import annotations


def test_unscannable_markets_in_health_payload():
    """The unscannable_markets metric counts (event_id, market, point) triples
    where Pinnacle has a non-canonical scope row and no soft book has the
    canonical-scope row for that market."""
    from src.api import _compute_unscannable_markets

    # Call with empty DB-equivalent input; expect 0.
    assert _compute_unscannable_markets(odds_rows=[]) == 0

    # Two rows: Pinnacle reg hockey total + no soft canonical → unscannable.
    rows = [
        {
            "event_id": "e1",
            "provider_id": "pinnacle",
            "sport": "ice_hockey",
            "market": "total",
            "point": 4.5,
            "scope": "reg",
        },
    ]
    assert _compute_unscannable_markets(odds_rows=rows) == 1

    # Add a soft 'ft' row for the same market — now scannable.
    rows.append(
        {
            "event_id": "e1",
            "provider_id": "betinia",
            "sport": "ice_hockey",
            "market": "total",
            "point": 4.5,
            "scope": "ft",
        },
    )
    assert _compute_unscannable_markets(odds_rows=rows) == 0
