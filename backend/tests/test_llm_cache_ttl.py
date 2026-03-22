"""Test LLM cache TTL expiry."""
from datetime import datetime, timezone, timedelta
from src.db.models import LlmBoostCache


def _add_cache_entry(db_session, key="abc123", hours_ago=0):
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    db_session.add(LlmBoostCache(
        cache_key=key,
        title="Arsenal to win",
        boosted_odds=2.50,
        llm_title="Arsenal wins",
        llm_probability=0.45,
        llm_fair_odds=2.222,
        llm_confidence="medium",
        llm_reasoning="Strong home form",
        created_at=created.isoformat(),
        last_used_at=created.isoformat(),
    ))
    db_session.flush()


def test_fresh_cache_is_carried_forward(db_session):
    """Cache entries < 48h old should be carried forward."""
    from src.analysis.llm_enrichment import _cache_key, _load_cache_from_db, _carry_forward_from_cache
    key = _cache_key("Arsenal to win", 2.50, "")
    _add_cache_entry(db_session, key=key, hours_ago=10)
    cache = _load_cache_from_db(db_session)
    specials = [{"title": "Arsenal to win", "boosted_odds": 2.50, "event": ""}]
    count, used_keys = _carry_forward_from_cache(specials, cache)
    assert count == 1
    assert specials[0]["llm_probability"] == 0.45


def test_stale_cache_is_not_carried_forward(db_session):
    """Cache entries > 48h old should NOT be carried forward."""
    from src.analysis.llm_enrichment import _cache_key, _load_cache_from_db, _carry_forward_from_cache
    key = _cache_key("Arsenal to win", 2.50, "")
    _add_cache_entry(db_session, key=key, hours_ago=72)
    cache = _load_cache_from_db(db_session)
    specials = [{"title": "Arsenal to win", "boosted_odds": 2.50, "event": ""}]
    count, used_keys = _carry_forward_from_cache(specials, cache)
    assert count == 0
    assert specials[0].get("llm_probability") is None
