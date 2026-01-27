# OddOpp Quick Start Guide

Step-by-step guide to get the full stack running.

## Prerequisites

- Python 3.10+
- Node.js 18+
- pip and npm installed

## Backend Setup

### 1. Install Dependencies

```bash
cd backend
pip install -e ".[dev]"
```

For DOM scraping support (optional):
```bash
pip install -e ".[scrape]"
```

### 2. Configure Environment

Create `backend/.env`:
```bash
# Optional: Anthropic API key for frontend chat
ANTHROPIC_API_KEY=your_key_here
```

### 3. Initialize Database

The database will auto-initialize on first run. Optionally, you can initialize it manually:

```python
from src.db.models import init_db
init_db()
```

### 4. Start Backend API

```bash
cd backend
python -m uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

Backend will be available at:
- **API:** http://localhost:8000
- **Docs:** http://localhost:8000/docs (Swagger UI)
- **Health:** http://localhost:8000/health

## Frontend Setup

### 1. Install Dependencies

```bash
cd frontend
npm install
```

### 2. Start Dev Server

```bash
npm run dev
```

Frontend will be available at:
- **App:** http://localhost:5173

The dev server automatically proxies:
- `/api/*` → `http://localhost:8000/api/*`
- `/ws/*` → `ws://localhost:8000/ws/*`

## Running Extraction Pipeline

### Via CLI (Recommended for testing)

```bash
cd backend
python main.py --providers unibet leovegas casumo
```

Options:
- `--providers` - Space-separated list of providers
- `--no-poly` - Skip Polymarket extraction
- `--sport` - Filter by sport (football, basketball, etc.)
- `--max-groups` - Limit number of event groups per provider

### Via API

Using the frontend terminal or curl:

```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=unibet,leovegas&sport=football&max_groups=5"
```

Check status:
```bash
curl http://localhost:8000/api/extraction/status
```

## Verify Everything Works

### 1. Backend Health Check

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "time": "2026-01-27T..."
}
```

### 2. Check Providers

```bash
curl http://localhost:8000/api/providers
```

Expected response:
```json
{
  "providers": [...],
  "total_balance": 0.0
}
```

### 3. Frontend Access

Open http://localhost:5173 in browser.

You should see:
- OddOpp Terminal interface
- Welcome message with stats (all zeros initially)
- Chat input at bottom

### 4. Run First Extraction

In the terminal, type:
```
Run extraction for unibet
```

Or use the API directly:
```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=unibet&max_groups=3"
```

Wait 30-60 seconds, then refresh the frontend to see extracted data.

## Common Issues

### Backend: Port 8000 already in use

```bash
# Find and kill the process
lsof -ti:8000 | xargs kill -9

# Or use a different port
python -m uvicorn src.api:app --port 8001
```

If using different port, update `frontend/vite.config.ts` proxy target.

### Frontend: Port 5173 already in use

```bash
# Kill process
lsof -ti:5173 | xargs kill -9

# Or use different port
npm run dev -- --port 3000
```

### No data after extraction

1. Check backend logs for errors
2. Verify providers are enabled:
   ```bash
   curl http://localhost:8000/api/providers
   ```
3. Check circuit breaker status:
   ```bash
   curl http://localhost:8000/api/circuit-breaker/status
   ```
4. View metrics:
   ```bash
   curl http://localhost:8000/api/metrics/current
   ```

### WebSocket not connecting

1. Ensure backend is running on port 8000
2. Check browser console for WebSocket errors
3. Verify proxy configuration in `frontend/vite.config.ts`

### Chat not working

1. Check if ANTHROPIC_API_KEY is set in `backend/.env`
2. Verify API key is valid
3. Check backend logs for API errors
4. Frontend will fall back to simulation mode if backend chat fails

## Development Workflow

### 1. Start Backend
```bash
cd backend
python -m uvicorn src.api:app --reload --port 8000
```

### 2. Start Frontend (separate terminal)
```bash
cd frontend
npm run dev
```

### 3. Run Extraction (separate terminal)
```bash
cd backend
python main.py --providers unibet
```

### 4. Monitor Logs
- Backend: stdout from uvicorn
- Frontend: browser console + terminal
- Extraction: stdout from main.py

## Recommended First Test

1. **Start backend** (terminal 1)
   ```bash
   cd backend && python -m uvicorn src.api:app --reload --port 8000
   ```

2. **Start frontend** (terminal 2)
   ```bash
   cd frontend && npm run dev
   ```

3. **Run extraction** (terminal 3)
   ```bash
   cd backend && python main.py --providers unibet --max-groups 2
   ```

4. **Open frontend** (browser)
   - Go to http://localhost:5173
   - Wait for extraction to complete (~30s)
   - Refresh page or wait for auto-refresh
   - Should see events/odds in stats

5. **Test chat** (frontend terminal)
   - Type: "Show me all events"
   - Type: "Find arbitrage opportunities"
   - Type: "What providers are connected?"

## Production Build

### Backend
```bash
cd backend
pip install -e .
python -m uvicorn src.api:app --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm run build
npm run preview  # Test production build
```

Serve `frontend/dist` with any static file server (nginx, Apache, etc.)

## Environment Variables

### Backend (`backend/.env`)
```bash
# Optional - for frontend chat
ANTHROPIC_API_KEY=sk-ant-...

# Optional - custom database path
DATABASE_URL=sqlite:///path/to/custom.db
```

### Frontend
No environment variables required. Configuration is in `vite.config.ts`.

## Database Location

Default: `backend/data/oddopp.db`

To reset database:
```bash
rm backend/data/oddopp.db
# Will be recreated on next API start
```

## Useful Commands

### Backend
```bash
# Run tests
pytest tests/

# Run specific test
pytest tests/test_pipeline.py -v

# Format code
black src/

# Type check
mypy src/
```

### Frontend
```bash
# Build production
npm run build

# Preview production build
npm run preview

# Lint
npm run lint

# Type check
npx tsc --noEmit
```

## Next Steps

After getting everything running:

1. **Explore API docs**: http://localhost:8000/docs
2. **Check provider health**: `curl http://localhost:8000/api/monitor/providers`
3. **View opportunities**: `curl http://localhost:8000/api/opportunities`
4. **Read frontend docs**: `frontend/README.md`
5. **Read integration docs**: `docs/FRONTEND_INTEGRATION.md`

## Getting Help

- Check logs (backend stdout, browser console)
- Verify all services are running (backend:8000, frontend:5173)
- Review documentation in `docs/` and `backend/docs/`
- Check CLAUDE.md for project overview

## Status Checklist

- [ ] Backend installed (`pip install -e ".[dev]"`)
- [ ] Frontend installed (`npm install`)
- [ ] Backend running (http://localhost:8000/health)
- [ ] Frontend running (http://localhost:5173)
- [ ] Extraction completed (some events in database)
- [ ] Frontend shows data (non-zero stats)
- [ ] Chat works (type a message)
- [ ] API docs accessible (http://localhost:8000/docs)

Once all checked, you're ready to explore the full platform!
