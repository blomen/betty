"""Altenar extractor sets scope based on typeId."""
from __future__ import annotations

import pytest


# Sanity: the typeId map is the source of truth for which scope each market emits.
# This test catches the case where someone adds a new typeId but forgets to
# tag it with a scope.
def test_altenar_typeid_scope_map_is_complete():
    """Every typeId in MARKET_TYPE_MAPPING must have a known scope."""
    # The MARKET_TYPE_MAPPING is in the provider source.
    from src.providers.altenar import AltenarRetriever
    p = AltenarRetriever({"id": "betinia"})
    mapping = getattr(p, "MARKET_TYPE_MAPPING", None) or getattr(p.__class__, "MARKET_TYPE_MAPPING", None)
    assert mapping is not None, "MARKET_TYPE_MAPPING must be discoverable"
    # Every entry should have a corresponding scope mapping
    from src.providers.altenar import TYPEID_SCOPE
    for type_id in mapping:
        assert type_id in TYPEID_SCOPE, f"typeId {type_id} ({mapping[type_id]}) has no scope mapping"


@pytest.mark.parametrize("type_id,sport,expected_scope", [
    (412, "ice_hockey", "ft"),       # hockey total incl. OT+pens
    (18, "ice_hockey", "reg"),       # hockey regulation total
    (18, "football", "ft"),          # football typeId 18 = Full Time (no OT)
    (225, "basketball", "ft"),       # basketball total incl. OT
    (258, "baseball", "ft"),         # baseball total incl. extras
    (406, "ice_hockey", "ft"),       # hockey moneyline incl. OT
    (1, "football", "ft"),           # football 1x2
])
def test_typeid_scope_mapping(type_id, sport, expected_scope):
    from src.providers.altenar import scope_for
    assert scope_for(type_id, sport) == expected_scope
