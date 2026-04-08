"""Tests for GenericWorkflow intel loading and method dispatch."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def intel_dir(tmp_path):
    d = tmp_path / "mirror_intel"
    d.mkdir()
    return d


@pytest.fixture
def sample_intel():
    return {
        "provider_id": "testprovider",
        "platform": "custom",
        "discovered_at": "2026-04-08T14:30:00Z",
        "updated_at": "2026-04-08T14:30:00Z",
        "capabilities": {
            "login": "discovered",
            "balance": "discovered",
            "history": "none",
            "placement": "none",
        },
        "login": {
            "method": "dom",
            "indicator": {"selector": ".user-balance", "regex": "[\\d.,]+"},
        },
        "balance": {
            "method": "api",
            "api": {"url": "/api/wallet/balance", "path": "data.balance", "currency": "SEK"},
            "dom": None,
        },
        "history": None,
        "betslip": None,
        "navigation": None,
        "api_endpoints": {},
        "notes": "",
    }


def test_load_intel_returns_dict(intel_dir, sample_intel):
    from src.mirror.workflows.generic import load_intel
    (intel_dir / "testprovider.json").write_text(json.dumps(sample_intel))
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["capabilities"]["balance"] == "discovered"


def test_load_intel_missing_returns_none(intel_dir):
    from src.mirror.workflows.generic import load_intel
    result = load_intel("nonexistent", intel_dir)
    assert result is None


def test_save_intel_roundtrip(intel_dir, sample_intel):
    from src.mirror.workflows.generic import save_intel, load_intel
    save_intel("testprovider", sample_intel, intel_dir)
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["balance"]["api"]["path"] == "data.balance"
