"""Tests for Smarkets signal-only parser."""
import pytest

from src.providers.smarkets import type_scope_to_sport


class TestTypeScopeToSport:
    @pytest.mark.parametrize("scope,expected", [
        ("football", "football"),
        ("basketball", "basketball"),
        ("tennis", "tennis"),
        ("ice-hockey", "ice_hockey"),
        ("american-football", "american_football"),
        ("baseball", "baseball"),
        ("mma", "mma"),
        ("boxing", "boxing"),
    ])
    def test_known_scopes(self, scope, expected):
        assert type_scope_to_sport(scope) == expected

    def test_politics_not_mapped(self):
        assert type_scope_to_sport("politics") is None
        assert type_scope_to_sport("entertainment") is None
