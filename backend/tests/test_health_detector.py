"""Tests for extraction health detector."""

from unittest.mock import mock_open, patch

import yaml

from src.pipeline.health import get_provider_intervals

SAMPLE_YAML = yaml.dump(
    {
        "extraction_scheduling": {
            "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
            "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
            "browser_soft": {"providers": ["888sport"], "interval_minutes": 10},
        },
        "active": ["pinnacle", "unibet", "betinia", "888sport", "cloudbet"],
    }
)


def test_get_provider_intervals_maps_active_providers():
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=SAMPLE_YAML)):
            result = get_provider_intervals()

    assert result["pinnacle"] == 1
    assert result["unibet"] == 2
    assert result["betinia"] == 2
    assert result["888sport"] == 10
    # cloudbet is active but not in any tier — should not appear
    assert "cloudbet" not in result


def test_get_provider_intervals_excludes_inactive():
    cfg = yaml.dump(
        {
            "extraction_scheduling": {
                "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
                "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
            },
            "active": ["pinnacle"],  # only pinnacle active
        }
    )
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=cfg)):
            result = get_provider_intervals()

    assert result == {"pinnacle": 1}
