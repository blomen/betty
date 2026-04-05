"""
Firev FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path
load_dotenv(get_env_path(), override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..db.models import init_db
from .routes import (
    providers_router,
    bankroll_router,
    events_router,
    opportunities_router,
    bets_router,
    profiles_router,
    extraction_router,
    metrics_router,
    monitoring_router,
    chat_router,
    polymarket_router,
    risk_router,
    specials_router,
    trading_router,
    settings_router,
    market_router,
    limits_router,
    postmortem_router,
    mirror_router,
    fire_window_router,
)

logger = logging.getLogger(__name__)

# Track startup time and boot ID for restart detection
_startup_time: float = 0.0
_boot_id: str = uuid.uuid4().hex[:8]
_background_tasks: set = set()  # prevent GC of fire-and-forget tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _startup_time
    _startup_time = time.time()
    await asyncio.to_thread(init_db)

    # Clear any stale fire window from previous session
    from ..services.fire_window import close_window
    close_window()

    # Start auto-settlement background task
    from ..services.auto_settle import auto_settle_loop
    _settle_task = asyncio.create_task(auto_settle_loop())
    _settle_task.set_name("auto-settle")

    # Kill orphaned browser processes from previous mirror session
    import subprocess
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "firefox.exe", "/T"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    # Add extraction-specific log file (INFO level) alongside root handlers
    # IMPORTANT: DEBUG floods the log with Databento tick data (hundreds/sec)
    # which blocks the event loop with synchronous disk I/O.
    import logging.handlers
    from ..paths import get_logs_dir
    extraction_handler = logging.handlers.RotatingFileHandler(
        get_logs_dir() / "extraction.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    extraction_handler.setLevel(logging.INFO)
    extraction_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    root_logger = logging.getLogger()
    root_logger.addHandler(extraction_handler)
    # Set root to INFO — DEBUG causes Databento SDK to flood event loop with
    # sync disk writes (dispatching MBP1Msg, read N bytes) at tick rate
    if root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)
    # Silence noisy third-party loggers
    logging.getLogger("databento").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Warm up singletons / heavy imports in background — don't block API startup
    import threading

    def _warmup_imports():
        from ..config.loader import load_config
        load_config()
        try:
            import numpy  # noqa: F401
        except ImportError:
            pass
    threading.Thread(target=_warmup_imports, daemon=True, name="startup-imports").start()

    # Warm up opportunity cache in background thread so first page load is fast
    # Must not block the event loop — otherwise /api/version etc. hang during startup
    def _warmup_opportunities():
        try:
            from ..db.models import get_session as _warmup_session
            from ..services import OpportunityService
            from .routes.opportunities import _opp_cache, _OPP_CACHE_TTL
            _wdb = _warmup_session()
            try:
                _wsvc = OpportunityService(_wdb)
                result = _wsvc.list_opportunities(type='value', limit=500)
                # Also prime the route-level response cache
                import json
                from fastapi.encoders import jsonable_encoder
                cache_key = ('value', None, None, None, None, None, None, 500)
                serialized = json.dumps(jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"))
                _opp_cache[cache_key] = (serialized, time.time() + _OPP_CACHE_TTL)
                logger.info("[Startup] Opportunity cache warmed (%d opps)", result.get("count", 0))
            finally:
                _wdb.close()
        except Exception as e:
            logger.warning("[Startup] Opportunity warmup failed: %s", e)
    threading.Thread(target=_warmup_opportunities, daemon=True, name="startup-warmup").start()

    # Mirror-only mode: skip scheduler, trading features, RL collector
    _mirror_only = bool(os.environ.get("FIREV_MIRROR_ONLY"))
    if _mirror_only:
        logger.info("[Startup] Mirror-only mode — skipping scheduler, trading, RL")

    # Auto-start continuous extraction (server only — skip for local mirror)
    if not _mirror_only:
        from ..pipeline.scheduler import get_scheduler
        scheduler = get_scheduler()

        async def _start_scheduler():
            try:
                await scheduler.start_continuous(interval_seconds=300)
                logger.info("[Startup] Scheduler started successfully")
            except Exception:
                logger.exception("[Startup] Scheduler start_continuous failed")

        _scheduler_task = asyncio.create_task(_start_scheduler())
        _scheduler_task.set_name("scheduler-start")
        _background_tasks.add(_scheduler_task)
        _scheduler_task.add_done_callback(_background_tasks.discard)

        # Auto-start RL training daemon (server only, runs at nice 19)
        def _start_rl_daemon():
            import subprocess as _sp
            daemon_script = "/app/backend/scripts/rl_train_daemon.sh"
            pid_file = "/app/data/rl/daemon.pid"
            try:
                # Check if daemon already running
                try:
                    with open(pid_file) as f:
                        old_pid = int(f.read().strip())
                    os.kill(old_pid, 0)  # signal 0 = check if alive
                    logger.info("[Startup] RL daemon already running (PID %d)", old_pid)
                    return
                except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
                    pass  # not running
                _sp.Popen(["bash", daemon_script], start_new_session=True)
                logger.info("[Startup] RL training daemon started")
            except Exception as e:
                logger.warning("[Startup] RL daemon start failed: %s", e)
        threading.Thread(target=_start_rl_daemon, daemon=True, name="startup-rl-daemon").start()
    else:
        logger.info("[Startup] Scheduler disabled (FIREV_NO_SCHEDULER set)")

    # ── Trading features (Databento stream, level monitor, candle backfill) ──
    # Everything is gated on market hours: when Globex is closed (weekend),
    # nothing starts — zero threads, zero network, zero CPU.
    # A lightweight watcher sleeps until market opens, then boots everything.
    databento_key = os.environ.get("DATABENTO_API_KEY")
    _databento_stream = None
    if databento_key and not _mirror_only:
        from ..db.models import get_session as _get_db_session, get_market_session as _get_market_session
        from ..market_data.stream import DatabentoLiveStream, TickWriter
        from ..services.market_service import MarketService as _MS

        _databento_stream = DatabentoLiveStream(
            api_key=databento_key,
            db_session_factory=_get_market_session,
        )
        app.state.databento_stream = _databento_stream

        async def _start_trading_features():
            """Boot all trading features (network I/O heavy).

            Called once market is confirmed open — never during weekend close.
            """
            try:
                # Prune ticks from prior sessions (market.db)
                await TickWriter.prune_old_trades(_get_market_session, symbol="NQ")

                # Seed CandleFlow from last DB candle so live updates continue
                # rather than starting fresh (which causes fake wicks on chart).
                from ..repositories.market_repo import MarketRepo as _MR
                _seed_db = _get_market_session()
                try:
                    for _flow, _interval in [
                        (_databento_stream._candle_flow, "5m"),
                        (_databento_stream._candle_flow_1m, "1m"),
                    ]:
                        _last = _MR(_seed_db).get_latest_candle("NQ", _interval)
                        if _last:
                            _ts = _last.ts.replace(tzinfo=timezone.utc) if not _last.ts.tzinfo else _last.ts
                            _flow.seed(int(_ts.timestamp()), _last.o, _last.h, _last.l, _last.c, _last.v)
                            logger.info("Seeded %s CandleFlow from DB: bucket=%s", _interval, _ts)
                finally:
                    _seed_db.close()

                await _databento_stream.start()

                # Initialize Level Monitor for proximity-based level alerts
                from ..market_data.level_monitor import LevelMonitor
                from ..services.market_service import MarketService

                level_monitor = LevelMonitor(publish_fn=_databento_stream._publish)
                _databento_stream.set_level_monitor(level_monitor)
                app.state.level_monitor = level_monitor
                # Use the stream thread's event loop for level context fetching —
                # keeps ML/macro async work off the main event loop entirely.
                level_monitor.set_async_context(_databento_stream._stream_thread_loop, _get_db_session)

                logger.info("Trading features started: Databento stream + level monitor")

                # Load initial levels + COT in background thread (DB-heavy, would stall event loop)
                import threading
                def _load_initial_data():
                    loop = asyncio.new_event_loop()
                    async def _run():
                        try:
                            svc = MarketService(_get_db_session())
                            try:
                                expanded = await svc.build_expanded_session()
                                if expanded:
                                    level_monitor.load_levels(expanded)
                                    logger.info("Initial levels loaded")
                            finally:
                                svc.db.close()
                        except Exception as e:
                            logger.warning("Failed to load initial levels: %s", e)

                        try:
                            from ..market_data.cot import fetch_cot, store_cot_data
                            reports = await fetch_cot()
                            if reports:
                                db = _get_db_session()
                                try:
                                    store_cot_data(db, reports)
                                    db.commit()
                                finally:
                                    db.close()
                                logger.info("COT data refreshed: %d reports", len(reports))
                        except Exception as e:
                            logger.warning("COT refresh failed: %s", e)

                        # Refresh economic calendar from ForexFactory
                        try:
                            from ..data.economic_calendar import fetch_and_store_calendar
                            db = _get_db_session()
                            try:
                                count = await fetch_and_store_calendar(db)
                                db.commit()
                                logger.info("Economic calendar refreshed: %d events", count)
                            finally:
                                db.close()
                        except Exception as e:
                            logger.warning("Economic calendar refresh failed: %s", e)
                    loop.run_until_complete(_run())
                    loop.close()
                threading.Thread(target=_load_initial_data, daemon=True, name="startup-levels").start()

                # Start news impact recorder (measures NQ price after economic events)
                from ..ml.macro.news_impact_recorder import news_impact_loop
                asyncio.create_task(news_impact_loop(_get_db_session, _databento_stream))

            except Exception as e:
                logger.error("Trading features startup failed: %s", e, exc_info=True)

            # Backfill market_candles in a background thread (lowest priority).
            import threading
            def _run_backfill():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_backfill_candles())
                except Exception as e:
                    logger.warning("Candle backfill failed: %s", e)
                finally:
                    loop.close()
            threading.Thread(target=_run_backfill, daemon=True, name="startup-backfill").start()

        async def _backfill_candles():
            from ..market_data.databento_provider import DabentoProvider
            from ..repositories.market_repo import MarketRepo
            from ..config.trading_loader import get_market_data_config
            from datetime import timedelta

            config = get_market_data_config()
            symbol = "NQ"
            db_symbol = config.get("symbol", "NQ.v.0")
            now = datetime.now(timezone.utc)
            fetch_end = now - timedelta(minutes=15)  # Databento ~15 min delay

            interval_targets = {
                "5m": now - timedelta(days=30),   # 1 month of 5m bars (monthly VP)
                "1m": now - timedelta(days=36),
            }

            inner = DabentoProvider(config)

            for interval, target_start in interval_targets.items():
                db = _get_db_session()
                try:
                    repo = MarketRepo(db)
                    oldest = repo.get_oldest_candle(symbol, interval)
                    latest = repo.get_latest_candle(symbol, interval)
                finally:
                    db.close()

                fetch_start = target_start
                if oldest:
                    oldest_ts = oldest.ts if oldest.ts.tzinfo else oldest.ts.replace(tzinfo=timezone.utc)
                    if oldest_ts <= target_start + timedelta(days=1):
                        fetch_start = latest.ts if latest.ts.tzinfo else latest.ts.replace(tzinfo=timezone.utc)

                if fetch_start >= fetch_end - timedelta(minutes=2):
                    logger.info("Candle backfill %s: already up to date (latest %s)", interval, fetch_start)
                else:
                    logger.info("Candle backfill %s: %s → %s", interval, fetch_start, fetch_end)
                    try:
                        bars = await asyncio.wait_for(
                            inner.get_bars(db_symbol, interval, fetch_start, fetch_end),
                            timeout=300.0,
                        )
                        if bars:
                            db = _get_db_session()
                            try:
                                repo = MarketRepo(db)
                                for b in bars:
                                    repo.upsert_candle(
                                        symbol, interval, b.timestamp,
                                        b.open, b.high, b.low, b.close, b.volume,
                                    )
                                logger.info("Candle backfill %s: upserted %d bars", interval, len(bars))
                            finally:
                                db.close()
                        else:
                            logger.info("Candle backfill %s: no bars in range (market closed?)", interval)
                    except Exception as e:
                        logger.warning("Candle backfill %s failed: %s", interval, e)

                # Detect and fill mid-series gaps for today (e.g. stream was down for hours)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
                db = _get_db_session()
                try:
                    repo = MarketRepo(db)
                    rows = repo.get_candles(symbol, interval, today_start, now)
                finally:
                    db.close()

                bucket_s = 60 if interval == "1m" else 300
                max_gap = bucket_s * 3
                min_age = 15 * 60
                now_epoch = int(now.timestamp())
                candle_times = sorted(
                    int(r.ts.replace(tzinfo=timezone.utc).timestamp() if not r.ts.tzinfo else r.ts.timestamp())
                    for r in rows
                )
                gaps = []
                for i in range(1, len(candle_times)):
                    diff = candle_times[i] - candle_times[i - 1]
                    if diff > max_gap and (now_epoch - candle_times[i]) > min_age:
                        gaps.append((candle_times[i - 1], candle_times[i]))

                if gaps:
                    logger.info("Candle gap backfill %s: found %d mid-series gaps", interval, len(gaps))
                    for gap_start, gap_end in gaps:
                        start_dt = datetime.fromtimestamp(gap_start, tz=timezone.utc)
                        end_dt = datetime.fromtimestamp(gap_end, tz=timezone.utc)
                        try:
                            bars = await asyncio.wait_for(
                                inner.get_bars(db_symbol, interval, start_dt, end_dt),
                                timeout=60.0,
                            )
                            if bars:
                                db = _get_db_session()
                                try:
                                    repo = MarketRepo(db)
                                    count = repo.bulk_insert_candles(symbol, interval, bars)
                                    logger.info("Candle gap backfill %s: filled %s → %s (%d bars)", interval, start_dt, end_dt, count)
                                finally:
                                    db.close()
                        except Exception as e:
                            logger.warning("Candle gap backfill %s: %s → %s failed: %s", interval, start_dt, end_dt, e)

        async def _trading_gate():
            """Gate: if market is open, start immediately. If closed, sleep until open."""
            if not _MS._is_globex_closed():
                await _start_trading_features()
                return

            # Market is closed — sleep until Globex opens, then boot everything
            sleep_s = DatabentoLiveStream._seconds_until_globex_open()
            logger.info(
                "Trading features halted — Globex closed. "
                "Sleeping %.1f hours until market opens (zero resources used)",
                sleep_s / 3600,
            )
            # Sleep in 60s chunks so we can be cancelled cleanly on shutdown
            slept = 0.0
            while slept < sleep_s:
                await asyncio.sleep(min(60, sleep_s - slept))
                slept += 60
            logger.info("Globex open — starting trading features now")
            await _start_trading_features()

        _trading_gate_task = asyncio.create_task(_trading_gate())
    else:
        logger.warning("DATABENTO_API_KEY not set — trading features disabled")

    # Auto-start all mirror browsers (always-on recording)
    from .routes.mirror import _mirrors, _load_all_providers
    from ..mirror.service import MirrorService
    from ..pipeline.broadcast import odds_broadcaster as _mirror_broadcaster

    async def _start_all_mirrors():
        """Auto-start mirrors for providers that have saved browser profiles.

        Only opens browsers for sites you've previously logged into —
        determined by the existence of a mirror_profiles/{provider} directory.
        First-time providers must be started manually via the sidebar menu.

        Delays start by 30s and staggers launches by 3s each to keep the
        event loop responsive for API requests during startup.
        """
        await asyncio.sleep(30)  # Let API stabilize before launching browsers
        try:
            from ..paths import get_data_dir
            profiles_dir = get_data_dir() / "mirror_profiles"
            if not profiles_dir.exists():
                logger.info("No mirror profiles found — skipping auto-start")
                return

            providers = _load_all_providers()
            started = 0
            for pid, pconf in providers.items():
                profile_dir = profiles_dir / pid
                if not profile_dir.exists():
                    continue
                has_parser = pconf["type"] == "gecko_v2"
                mirror = MirrorService(
                    provider_id=pid,
                    broadcaster=_mirror_broadcaster,
                    discovery=not has_parser,
                )
                try:
                    await mirror.start(site_url=pconf["url"])
                    _mirrors[pid] = mirror
                    started += 1
                    logger.info(f"Mirror auto-started: {pid}")
                    await asyncio.sleep(3)  # Stagger: let event loop breathe between launches
                except Exception as e:
                    logger.warning(f"Mirror auto-start failed for {pid}: {e}")
            logger.info(f"Mirror auto-start complete: {started} browsers")
        except Exception as e:
            logger.warning(f"Mirror auto-start failed: {e}")

    # Mirror auto-start disabled — launching 20 Playwright browsers on the main
    # event loop freezes it permanently. Mirrors should be started on-demand via UI.
    # asyncio.create_task(_start_all_mirrors())

    # Start live RL episode collector (measures outcomes from market_trades)
    _live_collector_task = None
    if not _mirror_only:
        try:
            from ..rl.live_collector import get_live_collector

            async def _get_recent_trades(since, until):
                """Query market_trades for outcome measurement."""
                from sqlalchemy import text, create_engine
                market_url = os.environ.get(
                    "MARKET_DATABASE_URL",
                    "postgresql://firev:firev2026secure@postgres:5432/market",
                ).replace("+asyncpg", "")  # Use sync driver for thread safety
                engine = create_engine(market_url, pool_size=20, max_overflow=10, pool_pre_ping=True)
                with engine.connect() as conn:
                    rows = conn.execute(text(
                        "SELECT ts, price, size FROM market_trades "
                        "WHERE ts >= :since AND ts <= :until ORDER BY ts"
                    ), {"since": since, "until": until}).fetchall()
                return [{"ts": r[0], "price": r[1], "size": r[2]} for r in rows]

            collector = get_live_collector()
            _live_collector_task = asyncio.create_task(
                collector.measure_outcomes_loop(_get_recent_trades)
            )
            logger.info("Live RL episode collector started")
        except Exception:
            logger.debug("Live RL episode collector not available", exc_info=True)

    yield  # App is running

    # Graceful shutdown
    if _live_collector_task and not _live_collector_task.done():
        _live_collector_task.cancel()
        try:
            await _live_collector_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutting down...")

    # Cancel the trading gate sleep loop (can block for hours when market closed)
    if '_trading_gate_task' in dir() and not _trading_gate_task.done():
        _trading_gate_task.cancel()
        try:
            await _trading_gate_task
        except asyncio.CancelledError:
            pass

    # Stop all mirrors
    for pid in list(_mirrors.keys()):
        try:
            mirror = _mirrors.pop(pid)
            await mirror.stop()
        except Exception as e:
            logger.warning(f"Mirror stop failed for {pid}: {e}")

    if _databento_stream:
        await _databento_stream.stop()
    if not _mirror_only:
        try:
            from ..pipeline.scheduler import get_scheduler
            get_scheduler().stop_all()
        except Exception:
            pass
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Firev API",
    description="Betting analytics & value betting backend",
    version="0.1.0",
    lifespan=lifespan,
)

# GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)


# App-level API key auth — defense-in-depth behind nginx basic auth
_api_key = os.environ.get("FIREV_API_KEY")
_auth_exempt = {"/health", "/health/live", "/health/ready"}

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if _api_key and request.url.path not in _auth_exempt:
        # Skip if request already passed nginx basic auth
        passed_nginx = request.headers.get("X-Nginx-Authenticated")
        if not passed_nginx:
            provided = request.headers.get("X-API-Key")
            if provided != _api_key:
                return JSONResponse(status_code=401, content={"error": "Invalid or missing API key"})
    return await call_next(request)


# Cache-Control for GET API responses — lets the browser skip redundant fetches
@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and path.startswith("/api/") and "/stream" not in path:
        # Short private cache — browser can reuse within window, must revalidate after
        response.headers.setdefault("Cache-Control", "private, max-age=5")
    return response

# Allow CORS for frontend
_default_origins = "http://localhost:5173,http://localhost:5174,http://localhost:3000,tauri://localhost"
_cors_origins = os.environ.get("CORS_ORIGINS", _default_origins).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a safe JSON response."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# Health check endpoints
@app.get("/health")
async def health():
    """Basic health check endpoint with boot ID for restart detection."""
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "boot_id": _boot_id,
        "uptime": round(time.time() - _startup_time) if _startup_time else 0,
    }


@app.get("/health/live")
async def health_live():
    """
    Liveness check - is the service running?

    Returns 200 if the service is alive and can handle requests.
    Used by Kubernetes/Docker for liveness probes.
    """
    return {
        "status": "alive",
        "uptime_seconds": time.time() - _startup_time if _startup_time else 0,
    }


@app.get("/health/ready")
async def health_ready():
    """
    Readiness check - is the service ready to accept traffic?

    Checks database connectivity and provider availability.
    Used by Kubernetes/Docker for readiness probes.
    """
    from .deps import get_db
    from ..db.models import Provider

    status = "ready"
    database_ok = False
    db_latency_ms = 0.0
    providers_available = 0
    providers_total = 0

    # Check database connectivity (run in thread to avoid blocking event loop)
    def _check_db():
        db = None
        try:
            db = next(get_db())
            providers = db.query(Provider).all()
            total = len(providers)
            available = sum(1 for p in providers if p.is_enabled)
            return True, total, available
        except Exception:
            return False, 0, 0
        finally:
            if db:
                db.close()

    try:
        db_start = time.time()
        database_ok, providers_total, providers_available = await asyncio.wait_for(
            asyncio.to_thread(_check_db), timeout=5.0
        )
        db_latency_ms = (time.time() - db_start) * 1000
    except (asyncio.TimeoutError, Exception):
        status = "not_ready"
        database_ok = False

    # Determine overall status
    if not database_ok:
        status = "not_ready"
    elif providers_available == 0 and providers_total > 0:
        status = "degraded"

    return {
        "status": status,
        "database": database_ok,
        "database_latency_ms": round(db_latency_ms, 2),
        "providers_available": providers_available,
        "providers_total": providers_total,
    }


# Include routers
app.include_router(providers_router)
app.include_router(bankroll_router)
app.include_router(events_router)
app.include_router(opportunities_router)
app.include_router(bets_router)
app.include_router(profiles_router)
app.include_router(extraction_router)
app.include_router(metrics_router)
app.include_router(monitoring_router)
app.include_router(chat_router)
app.include_router(polymarket_router)
app.include_router(risk_router)
# app.include_router(specials_router)  # DISABLED — boosts/specials turned off
app.include_router(trading_router)
app.include_router(market_router)
app.include_router(settings_router)
app.include_router(limits_router)
app.include_router(postmortem_router)
app.include_router(mirror_router)
app.include_router(fire_window_router)


# Version endpoint
@app.get("/api/version")
async def get_version():
    """Return app version and runtime info."""
    from ..paths import get_data_dir
    return {
        "version": app.version,
        "data_dir": str(get_data_dir()),
    }


# Serve frontend static files (when dist/ exists — bundled mode or pre-built dev)
from ..paths import get_frontend_dir

_frontend_dir = get_frontend_dir()
if _frontend_dir.exists():
    # Mount JS/CSS/image assets
    _assets_dir = _frontend_dir / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # Serve favicon and other root static files
    @app.get("/terminal.svg")
    async def serve_favicon():
        svg = _frontend_dir / "terminal.svg"
        if svg.exists():
            return FileResponse(str(svg), media_type="image/svg+xml")

    # SPA catch-all: serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve React app for client-side routing."""
        index = _frontend_dir / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")


# Dev entry point (no --reload). On Windows, --reload forces SelectorEventLoop
# which breaks patchright subprocess spawning. Without --reload, uvicorn uses
# ProactorEventLoop correctly. Use run_dev.py if you need hot-reload.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="127.0.0.1", port=8000)
