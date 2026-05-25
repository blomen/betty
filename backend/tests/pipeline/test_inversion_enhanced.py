"""Enhanced inversion detection catches near-coinflip inversions and drops
unresolvable mismatches."""

from __future__ import annotations


def test_inversion_caught_at_low_ratio():
    """Pinnacle home@2.0/away@1.75 (ratio 1.143, away favored) vs soft inverted
    home@1.75/away@2.0 (home favored) — old 1.5 threshold missed this, new 1.10
    threshold catches it."""
    from src.pipeline.storage import _is_inversion_detected

    assert _is_inversion_detected(2.0, 1.75, 1.75, 2.00), "inversion at ratio 1.14 must be detected"


def test_no_inversion_when_books_agree():
    """Both books favor the same side — no inversion."""
    from src.pipeline.storage import _is_inversion_detected

    assert not _is_inversion_detected(2.0, 1.85, 2.10, 1.80)


def test_devig_disagreement_triggers_inversion():
    """When raw ratio is near 1.0, devig probability disagreement of >25pp
    on home identifies an inversion."""
    from src.pipeline.storage import _is_inversion_detected

    # Sharp home 1.50, away 2.50 → P(home) ≈ 62.5%
    # Soft (inverted) home 2.50, away 1.50 → soft P(home) ≈ 37.5%
    # Difference 25pp → triggers
    assert _is_inversion_detected(1.50, 2.50, 2.50, 1.50)


def test_post_swap_verification_drops_if_still_off():
    """If swap doesn't reconcile (e.g. genuine event mismatch), the soft odds
    should not validate."""
    from src.pipeline.storage import _validate_post_swap

    # Even after swap, sharp 2.0/1.85 vs soft 4.50/1.20 still disagrees by >15pp
    assert not _validate_post_swap(2.0, 1.85, 4.50, 1.20)


def test_event_marked_validated_when_clean():
    """An event with no inversion + sharp/soft devig agreement should validate."""
    from src.pipeline.storage import _validate_post_swap

    assert _validate_post_swap(2.0, 1.85, 2.10, 1.80)
