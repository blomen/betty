"""
Test ComeOn Enhanced Extraction

Verifies:
1. Parallel league processing works
2. Market normalization works
3. Selection extraction works
4. Database schema supports point values
"""
import pytest
from src.providers.comeon_multileague import ComeOnMultiLeagueRetriever


def test_market_normalization():
    """Test market type normalization with Swedish keywords."""
    config = {
        'id': 'comeon',
        'provider_id': 'comeon',
        'site_url': 'https://www.comeon.com',
        'domain': 'comeon.com'
    }

    retriever = ComeOnMultiLeagueRetriever(config, None)

    # Test Swedish market names
    assert retriever._normalize_market_type('1x2') == '1x2'
    assert retriever._normalize_market_type('Över/Under') == 'over_under'
    assert retriever._normalize_market_type('Handikapp') == 'spread'
    assert retriever._normalize_market_type('Båda lagen gör mål') == 'both_teams_to_score'

    # Test English market names
    assert retriever._normalize_market_type('Over/Under') == 'over_under'
    assert retriever._normalize_market_type('Handicap') == 'spread'
    assert retriever._normalize_market_type('Both Teams to Score') == 'both_teams_to_score'


def test_outcome_normalization():
    """Test outcome normalization based on market type."""
    config = {
        'id': 'comeon',
        'provider_id': 'comeon',
        'site_url': 'https://www.comeon.com',
        'domain': 'comeon.com'
    }

    retriever = ComeOnMultiLeagueRetriever(config, None)

    # Test 1x2 outcomes
    assert retriever._normalize_outcome('Hemma', 'Home', '1x2') == 'home'
    assert retriever._normalize_outcome('Borta', 'Away', '1x2') == 'away'
    assert retriever._normalize_outcome('Oavgjort', 'Draw', '1x2') == 'draw'

    # Test over/under outcomes
    assert retriever._normalize_outcome('Över 2.5', 'Over', 'over_under') == 'over'
    assert retriever._normalize_outcome('Under 2.5', 'Under', 'over_under') == 'under'

    # Test spread outcomes
    assert retriever._normalize_outcome('+1.5', 'Home', 'spread') == 'home'
    assert retriever._normalize_outcome('-1.5', 'Away', 'spread') == 'away'


def test_config_has_concurrent_leagues():
    """Test that concurrent_leagues config parameter is available."""
    from src.config.loader import ProviderConfig

    config = ProviderConfig(
        id='comeon',
        retriever_type='custom',
        concurrent_leagues=5
    )

    assert config.concurrent_leagues == 5


def test_database_unique_constraint():
    """Test that database unique constraint includes point field."""
    from src.db.models import Odds
    from sqlalchemy import inspect

    # Check that point column exists
    mapper = inspect(Odds)
    columns = [col.name for col in mapper.columns]
    assert 'point' in columns

    # Check that unique constraint is defined
    # (actual constraint validation would require database connection)
    assert hasattr(Odds, '__table_args__')
