# Betty

Betting analytics platform.

## What it does

- **Odds scanning** — Compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets
- **Market data** — Streams NQ futures ticks and candles via Databento for real-time trading signals
- **RL trading agent** — Reinforcement learning agent for futures trading with live inference
- **Opportunity detection** — Automated scanning for positive expected value opportunities
- **Bankroll management** — Kelly criterion stake sizing and risk management

## Tech Stack

- **Server engine:** Python 3.12+ / FastAPI / PostgreSQL / Playwright / SQLAlchemy
- **Local clients (arnoldsports / arnoldstocks):** React 19 / TypeScript / Vite / Tailwind
- **Infrastructure:** Docker / Nginx / Hetzner VPS
- **Data:** Databento (market data) / Pinnacle (sharp odds)

## Architecture

```
backend/src/             # Server-side engine (Hetzner, 24/7). No visual UI.
├── providers/           # 16 sportsbook extractors
├── pipeline/            # Orchestrator, storage, scheduler
├── analysis/            # Value + arb scanner, EV enrichment, devigging
├── matching/            # Event normalization + fuzzy matching
├── bankroll/            # Kelly criterion + stake sizing
├── market_data/         # Futures data, TPO profiles, trade setups
├── rl/                  # RL trading agent, features, replay engine
├── mirror/              # Browser automation for bet placement
├── api/                 # FastAPI routes
└── db/                  # SQLAlchemy models

arnoldsports/             # Local betting client (React + Playwright). Runs on your PC.
arnoldstocks/             # Local trading client (React + TopstepX). Runs on your PC.
```

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture and development workflow.
