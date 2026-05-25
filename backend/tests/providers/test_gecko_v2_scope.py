"""Gecko V2 extractor sets scope from market_template."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "template,sport,expected",
    [
        ("TGOUOT", "ice_hockey", "ft"),  # hockey total incl. OT
        ("TGOU", "ice_hockey", "reg"),  # hockey regulation total
        ("MHCPNOT", "ice_hockey", "reg"),  # hockey regulation handicap
        ("MTG2W", "football", "ft"),  # football total (90+stoppage)
        ("MW3W", "football", "ft"),  # football 1x2
        ("MW2W", "tennis", "ft"),  # tennis 2-way moneyline
    ],
)
def test_template_scope_mapping(template, sport, expected):
    from src.providers.gecko_v2 import scope_for_template

    assert scope_for_template(template, sport) == expected
