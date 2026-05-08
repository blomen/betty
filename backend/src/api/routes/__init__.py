"""API route modules."""

from .bankroll import router as bankroll_router
from .bets import router as bets_router
from .chat import router as chat_router
from .events import router as events_router
from .extraction import router as extraction_router
from .fire_window import router as fire_window_router
from .limits import router as limits_router
from .market import router as market_router
from .metrics import router as metrics_router
from .mirror import router as mirror_router
from .mirror_state import router as mirror_state_router
from .mirror_stream import router as mirror_stream_router
from .monitoring import router as monitoring_router
from .opportunities import router as opportunities_router
from .polymarket import router as polymarket_router
from .postmortem import router as postmortem_router
from .profiles import router as profiles_router
from .providers import router as providers_router
from .risk import router as risk_router
from .settings import router as settings_router
from .signals_ws import router as signals_ws_router
from .slip_odds import router as slip_odds_router
from .specials import router as specials_router
from .stocks import router as stocks_router
from .trading import router as trading_router

__all__ = [
    "providers_router",
    "bankroll_router",
    "events_router",
    "opportunities_router",
    "bets_router",
    "profiles_router",
    "extraction_router",
    "metrics_router",
    "monitoring_router",
    "chat_router",
    "polymarket_router",
    "risk_router",
    "specials_router",
    "trading_router",
    "market_router",
    "settings_router",
    "limits_router",
    "postmortem_router",
    "mirror_router",
    "mirror_state_router",
    "mirror_stream_router",
    "fire_window_router",
    "signals_ws_router",
    "slip_odds_router",
    "stocks_router",
]
