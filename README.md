# Firev

Betting analytics and trading platform.

## What it does

- **Odds scanning** — Compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets
- **Market data** — Streams NQ futures ticks and candles via Databento for real-time trading signals
- **RL trading agent** — Reinforcement learning agent for futures trading with live inference
- **Opportunity detection** — Automated scanning for positive expected value opportunities
- **Bankroll management** — Kelly criterion stake sizing and risk management

## Tech Stack

- **Backend:** Python 3.10+ / FastAPI / PostgreSQL / Playwright / SQLAlchemy
- **Frontend:** React 19 / TypeScript / Vite / Tailwind
- **Infrastructure:** Docker / Nginx / Hetzner VPS
- **Data:** Databento (market data) / Pinnacle (sharp odds)

## Architecture

```
backend/src/
├── providers/       # 16 sportsbook extractors
├── pipeline/        # Orchestrator, storage, scheduler
├── analysis/        # Value scanner, EV enrichment, devigging
├── matching/        # Event normalization + fuzzy matching
├── bankroll/        # Kelly criterion + stake sizing
├── market_data/     # Futures data, TPO profiles, trade setups
├── rl/              # RL trading agent, features, replay engine
├── mirror/          # Browser automation for bet placement
├── api/             # FastAPI routes
└── db/              # SQLAlchemy models

frontend/src/
├── components/      # React terminal UI
├── hooks/           # Data fetching + state
└── services/        # API client
```

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture and development workflow.
