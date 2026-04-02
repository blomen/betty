"""API route modules."""

from .providers import router as providers_router
from .bankroll import router as bankroll_router
from .events import router as events_router
from .opportunities import router as opportunities_router
from .bets import router as bets_router
from .profiles import router as profiles_router
from .extraction import router as extraction_router
from .metrics import router as metrics_router
from .monitoring import router as monitoring_router
from .chat import router as chat_router
from .polymarket import router as polymarket_router
from .risk import router as risk_router
from .specials import router as specials_router
from .trading import router as trading_router
from .market import router as market_router
from .settings import router as settings_router
from .limits import router as limits_router
from .postmortem import router as postmortem_router
from .mirror import router as mirror_router
from .fire_window import router as fire_window_router

__all__ = [
    'providers_router',
    'bankroll_router',
    'events_router',
    'opportunities_router',
    'bets_router',
    'profiles_router',
    'extraction_router',
    'metrics_router',
    'monitoring_router',
    'chat_router',
    'polymarket_router',
    'risk_router',
    'specials_router',
    'trading_router',
    'market_router',
    'settings_router',
    'limits_router',
    'postmortem_router',
    'mirror_router',
    'fire_window_router',
]
