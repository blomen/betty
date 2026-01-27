"""
OddOpp Database Models

SQLite schema for:
- Canonical events (provider-agnostic)
- Odds per provider
- Provider balances
- Manual bet tracking
- User profile settings
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, 
    DateTime, Boolean, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Database file location
DB_PATH = Path(__file__).parent.parent.parent / "data" / "oddopp.db"

Base = declarative_base()


# ============ Core Models ============

class Event(Base):
    """
    A canonical sporting event.
    
    Events are provider-agnostic - the same match has ONE event row,
    with odds from multiple providers stored in the Odds table.
    """
    __tablename__ = "events"
    
    # Canonical ID: "{sport}:{home_normalized}:{away_normalized}:{date}"
    id = Column(String, primary_key=True)
    
    sport = Column(String, nullable=False)
    league = Column(String)
    home_team = Column(String, nullable=False)  # Normalized name
    away_team = Column(String, nullable=False)  # Normalized name
    start_time = Column(DateTime)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    odds = relationship("Odds", back_populates="event", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="event")


class Provider(Base):
    """
    A betting provider (bookmaker).
    
    Stores runtime state only - extraction logic lives in code.
    """
    __tablename__ = "providers"
    
    id = Column(String, primary_key=True)       # "unibet"
    name = Column(String, nullable=False)       # "Unibet"
    url = Column(String)                        # "unibet.se"
    
    is_enabled = Column(Boolean, default=True)  # Can toggle off
    balance = Column(Float, default=0.0)        # Your current balance
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    odds = relationship("Odds", back_populates="provider")
    bets = relationship("Bet", back_populates="provider")


class Odds(Base):
    """
    Odds for an event outcome from a specific provider.
    
    Multiple providers can have odds for the same event.
    """
    __tablename__ = "odds"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    
    market = Column(String, nullable=False)     # "1x2", "over_under_2.5"
    outcome = Column(String, nullable=False)    # "home", "away", "draw", "over", "under"
    odds = Column(Float, nullable=False)        # Decimal odds (e.g., 2.10)
    point = Column(Float, nullable=True)        # Line/Point (e.g. 2.5, -6.5) for Spread/Total
    
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Unique constraint: one odds per event/provider/market/outcome/point combo
    # Includes point to allow multiple lines per market (e.g., over 2.5 vs over 3.0)
    __table_args__ = (
        UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point', name='uq_odds_with_point'),
    )
    
    # Relationships
    event = relationship("Event", back_populates="odds")
    provider = relationship("Provider", back_populates="odds")


# ============ Bet Tracking ============

class Bet(Base):
    """
    A placed bet (manual entry).
    
    User enters bets manually, system auto-calculates profit/ROI.
    """
    __tablename__ = "bets"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # What you bet on
    event_id = Column(String, ForeignKey("events.id"))
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    market = Column(String)                     # "1x2"
    outcome = Column(String)                    # "home"
    odds = Column(Float, nullable=False)        # 2.10
    
    # Stake
    stake = Column(Float, nullable=False)       # 100.00
    
    # Bonus tracking
    is_bonus = Column(Boolean, default=False)
    bonus_type = Column(String)                 # "free_bet", "deposit_match", "risk_free"
    
    # Result (updated when settled)
    result = Column(String, default="pending")  # "pending", "won", "lost", "void"
    payout = Column(Float, default=0.0)         # What you got back
    
    # Timestamps
    placed_at = Column(DateTime, default=datetime.utcnow)
    settled_at = Column(DateTime)
    
    # Relationships
    event = relationship("Event", back_populates="bets")
    provider = relationship("Provider", back_populates="bets")
    
    @property
    def profit(self) -> float:
        """Net profit/loss from this bet."""
        if self.result == "won":
            return self.payout - self.stake
        elif self.result == "lost":
            # Free bets don't lose stake
            return 0.0 if self.is_bonus else -self.stake
        return 0.0
    
    @property
    def roi_pct(self) -> float:
        """Return on investment percentage."""
        if self.stake == 0:
            return 0.0
        return (self.profit / self.stake) * 100


# ============ User Settings ============

class Profile(Base):
    """User settings for stake calculation and filtering."""
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, default="default", unique=True)

    # Kelly criterion
    kelly_fraction = Column(Float, default=0.25)    # Quarter Kelly

    # Opportunity thresholds
    min_edge_pct = Column(Float, default=2.0)       # Min edge for value bets
    min_arb_pct = Column(Float, default=0.5)        # Min profit for arbs

    # Risk limits
    max_stake_pct = Column(Float, default=5.0)      # Max % of bankroll per bet

    # Bonus settings
    min_retention_pct = Column(Float, default=80.0)  # Min % for free bet value
    preferred_counterparts = Column(String)          # JSON list: ["bet365", "betsson"]
    bonus_enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ============ Opportunities ============

class Opportunity(Base):
    """
    Detected opportunities (arbitrage, value bets, bonus matches).

    Stores snapshots of opportunities at time of detection.
    Can be marked inactive when odds change.
    """
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # "arbitrage", "value", "bonus"

    # Event reference
    event_id = Column(String, ForeignKey("events.id"))
    market = Column(String)

    # Provider details
    provider1_id = Column(String, ForeignKey("providers.id"))
    provider2_id = Column(String, ForeignKey("providers.id"), nullable=True)

    # Odds at detection
    odds1 = Column(Float)
    odds2 = Column(Float, nullable=True)

    # Outcomes
    outcome1 = Column(String)
    outcome2 = Column(String, nullable=True)

    # Calculated metrics
    profit_pct = Column(Float, nullable=True)  # For arbitrage
    edge_pct = Column(Float, nullable=True)     # For value bets

    # Status
    is_active = Column(Boolean, default=True)
    detected_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    # Relationships
    event = relationship("Event")
    provider1 = relationship("Provider", foreign_keys=[provider1_id])
    provider2 = relationship("Provider", foreign_keys=[provider2_id])


# ============ Database Functions ============

def init_db() -> None:
    """Initialize database and create tables."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Base.metadata.create_all(engine)
    return engine


def get_session():
    """Get a database session."""
    engine = init_db()
    Session = sessionmaker(bind=engine)
    return Session()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
