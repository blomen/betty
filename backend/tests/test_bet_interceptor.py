"""Tests for BetInterceptor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.mirror.interceptor import BetInterceptor


def test_interceptor_initial_state():
    interceptor = BetInterceptor(provider_id="spelklubben")
    assert interceptor.provider_id == "spelklubben"
    assert interceptor.status == "stopped"
    assert interceptor.browser is None


def test_interceptor_user_data_dir():
    interceptor = BetInterceptor(provider_id="spelklubben")
    assert "mirror_profiles" in str(interceptor.user_data_dir)
    assert "spelklubben" in str(interceptor.user_data_dir)
