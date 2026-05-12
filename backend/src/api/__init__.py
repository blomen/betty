"""
Arnold FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

# Dedicated executor for /health probes so they don't queue behind extraction
# threads on the default asyncio loop executor.
_health_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="health")

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path

load_dotenv(get_env_path(), override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from ..db.models import init_db
from .routes import (
    bankroll_router,
    bets_router,
    chat_router,
    events_router,
    extraction_router,
    fire_window_router,
    limits_router,
    market_router,
    metrics_router,
    mirror_router,
    mirror_state_router,
    mirror_stream_router,
    monitoring_router,
    opportunities_router,
    polymarket_router,
    postmortem_router,
    profiles_router,
    providers_router,
    risk_router,
    settings_router,
    signals_ws_router,
    slip_odds_router,
    specials_router,
    stocks_router,
    trading_router,
)

logger = logging.getLogger(__name__)

# Track startup time and boot ID for restart detection
_startup_time: float = 0.0
_boot_id: str = uuid.uuid4().hex[:8]
_background_tasks: set = set()  # prevent GC of fire-and-forget tasks


def _install_asyncio_exception_handler() -> None:
    """Surface "Future exception was never retrieved" errors with their task name.

    The default handler logs only the bare exception, which is useless for
    finding the offender among hundreds of background tasks. We add the
    task name + traceback so silent failures stop being silent.
    """

    def _handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        task = context.get("future") or context.get("task")
        task_name = getattr(task, "get_name", lambda: "<unnamed>")() if task else "<no-task>"
        if exc is not None:
            logger.error("[asyncio] uncaught exception in task=%s: %s — %s", task_name, msg, exc, exc_info=exc)
        else:
            logger.error("[asyncio] uncaught error in task=%s: %s — %r", task_name, msg, context)

    asyncio.get_event_loop().set_exception_handler(_handler)


def _build_rl_context_from_session(session_data: dict, expanded: dict | None) -> dict:
    """Convert the flat session_data dict from MarketService.compute_session()
    into the rl_context shape that level_monitor + observation extractors
    expect: typed VolumeProfile/VWAPBands/SessionLevels objects with
    attribute access, plus the macro/swing/atr scalars.

    Before this helper, the 4 rl_context sites all read
    `session_data.get("volume_profile") / .get("session_levels") /
    .get("vwap_bands")` — but SessionAnalysis.to_dict flattens those into
    scalar keys (poc/vah/val, vwap/vwap_1sd_upper/..., ib_high/ib_low) and
    drops the typed objects. Result: extract_structure_features got None
    for vwap_bands, volume_profile, session_levels — feats 0-8, 60-63 of
    the structure segment (12 dims) stayed at zero. Reconstructing the
    typed objects from the flat keys revives those dims.

    Session levels also need pdh/pdl + tokyo/london highs/lows which
    compute_session writes as top-level keys (see market_service.py:867).
    """
    from ..market_data.levels import SessionLevels, VolumeProfile, VWAPBands

    sd = session_data or {}

    vp = None
    if sd.get("poc") is not None and sd.get("vah") is not None and sd.get("val") is not None:
        vp = VolumeProfile(poc=float(sd["poc"]), vah=float(sd["vah"]), val=float(sd["val"]))

    vwap_bands = None
    if sd.get("vwap") is not None:
        v = float(sd["vwap"])
        vwap_bands = VWAPBands(
            vwap=v,
            sd1_upper=float(sd.get("vwap_1sd_upper") or v),
            sd1_lower=float(sd.get("vwap_1sd_lower") or v),
            sd2_upper=float(sd.get("vwap_2sd_upper") or v),
            sd2_lower=float(sd.get("vwap_2sd_lower") or v),
            sd3_upper=float(sd.get("vwap_3sd_upper") or v),
            sd3_lower=float(sd.get("vwap_3sd_lower") or v),
        )

    session_levels = None
    if any(sd.get(k) is not None for k in ("pdh", "pdl", "ib_high", "ib_low", "tokyo_high", "tokyo_low")):
        session_levels = SessionLevels(
            pdh=sd.get("pdh"),
            pdl=sd.get("pdl"),
            tokyo_high=sd.get("tokyo_high"),
            tokyo_low=sd.get("tokyo_low"),
            london_high=sd.get("london_high"),
            london_low=sd.get("london_low"),
            ib_high=sd.get("ib_high"),
            ib_low=sd.get("ib_low"),
        )

    return {
        "vwap_bands": vwap_bands,
        "volume_profile": vp,
        "session_levels": session_levels,
        "session_tpos": sd.get("session_tpos"),
        # session_context dict (time-varying fields like minute_of_day,
        # session_type, ib_broken) is computed PER zone touch in
        # level_monitor._build_rl_state_zone — too volatile to bake in at
        # init time. Keep this slot for future static fields if needed.
        "session_context": sd.get("session_context"),
        "macro": sd.get("macro"),
        "swing_structure": (expanded or {}).get("swing_structure") if expanded else None,
        "atr": sd.get("atr") or sd.get("ib_range") or 200.0,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _startup_time
    _startup_time = time.time()
    _install_asyncio_exception_handler()
    await asyncio.to_thread(init_db)

    # Add new columns to existing Postgres tables (create_all only makes new tables)
    def _pg_migrations():
        from sqlalchemy import inspect, text

        from ..db.models import get_engine

        engine = get_engine()
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("profiles")}
        if "liquid_balance" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE profiles ADD COLUMN liquid_balance FLOAT DEFAULT 0.0"))

    try:
        await asyncio.to_thread(_pg_migrations)
    except Exception:
        pass  # Column already exists or SQLite (handled by _run_migrations)

    # Clear any stale fire window from previous session
    from ..services.fire_window import close_window

    close_window()

    # Kill orphaned browser processes from previous mirror session
    import subprocess

    with suppress(Exception):
        subprocess.run(
            ["taskkill", "/F", "/IM", "firefox.exe", "/T"],
            capture_output=True,
            timeout=5,
        )

    # Add extraction-specific log file (INFO level) alongside root handlers
    # IMPORTANT: DEBUG floods the log with Databento tick data (hundreds/sec)
    # which blocks the event loop with synchronous disk I/O.
    import logging.handlers

    from ..paths import get_logs_dir

    extraction_handler = logging.handlers.RotatingFileHandler(
        get_logs_dir() / "extraction.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    extraction_handler.setLevel(logging.INFO)
    extraction_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
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
            from .routes.opportunities import _OPP_CACHE_TTL, _opp_cache

            _wdb = _warmup_session()
            try:
                _wsvc = OpportunityService(_wdb)
                result = _wsvc.list_opportunities(type="value", limit=500)
                # Also prime the route-level response cache
                import json

                from fastapi.encoders import jsonable_encoder

                cache_key = ("value", None, None, None, None, None, None, 500)
                serialized = json.dumps(jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"))
                _opp_cache[cache_key] = (serialized, time.time() + _OPP_CACHE_TTL)
                logger.info("[Startup] Opportunity cache warmed (%d opps)", result.get("count", 0))
            finally:
                _wdb.close()
        except Exception as e:
            logger.warning("[Startup] Opportunity warmup failed: %s", e)

    threading.Thread(target=_warmup_opportunities, daemon=True, name="startup-warmup").start()

    # Mirror-only mode: skip scheduler, trading features, RL collector
    _mirror_only = bool(os.environ.get("ARNOLD_MIRROR_ONLY"))
    if _mirror_only:
        logger.info("[Startup] Mirror-only mode — skipping scheduler, trading, RL")

    _stocks_mode = bool(os.environ.get("ARNOLD_STOCKS_MODE"))
    if _stocks_mode:
        logger.info("[Startup] Stocks mode — LevelMonitor + Specialists active, no Databento, no local broker")

    # Auto-start continuous extraction (server only — skip for local mirror)
    if not _mirror_only:
        from ..pipeline.scheduler import get_scheduler

        scheduler = get_scheduler()

        async def _start_scheduler():
            # Skip extraction when RL turbo mode is active (training needs all resources)
            turbo_flag = Path("/app/data/rl/turbo")
            if turbo_flag.exists():
                logger.info("[Startup] Extraction SKIPPED — RL turbo mode active (remove %s to re-enable)", turbo_flag)
                return
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
                # Check if daemon already running (stale PIDs from old containers are common)
                try:
                    with open(pid_file) as f:
                        old_pid = int(f.read().strip())
                    # Verify it's actually the daemon, not a recycled PID
                    with open(f"/proc/{old_pid}/cmdline") as cf:
                        cmdline = cf.read()
                    if "rl_train_daemon" in cmdline:
                        logger.info("[Startup] RL daemon already running (PID %d)", old_pid)
                        return
                except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
                    pass  # not running or stale PID
                _sp.Popen(
                    ["taskset", "-c", "0,1,4,5", "bash", daemon_script],
                    start_new_session=True,
                )
                logger.info("[Startup] RL training daemon started (pinned to cores 0-1)")
            except Exception as e:
                logger.warning("[Startup] RL daemon start failed: %s", e)

        threading.Thread(target=_start_rl_daemon, daemon=True, name="startup-rl-daemon").start()

        # Auto-start TopstepX trading service (server-side, 24/7).
        # Skipped when STOCKS_AUTONOMOUS=true — in that mode the FastAPI
        # process owns the broker via bootstrap_stocks_on_server, and
        # running a second TopstepX SignalR session in the subprocess
        # triggers a "Multiple sessions detected" reconnect storm that
        # blocks trading. One process, one broker.
        def _start_trading_service():
            import subprocess as _sp

            trading_script = "/app/backend/scripts/trading_service.py"
            if not os.path.exists(trading_script):
                return
            if not os.getenv("TOPSTEPX_USERNAME"):
                logger.info("[Startup] Trading service skipped (no TOPSTEPX_USERNAME)")
                return
            if os.getenv("STOCKS_AUTONOMOUS", "").lower() == "true":
                logger.info(
                    "[Startup] Trading service skipped (STOCKS_AUTONOMOUS=true; "
                    "FastAPI owns the TopstepX SignalR session)"
                )
                return
            try:
                _sp.Popen(
                    ["python", trading_script],
                    start_new_session=True,
                    stdout=open("/app/logs/trading_service.log", "a"),
                    stderr=open("/app/logs/trading_service.log", "a"),
                )
                logger.info("[Startup] Trading service started")
            except Exception as e:
                logger.warning("[Startup] Trading service start failed: %s", e)

        threading.Thread(target=_start_trading_service, daemon=True, name="startup-trading").start()
    else:
        logger.info("[Startup] Scheduler disabled (ARNOLD_NO_SCHEDULER set)")

    # ── Trading features (Databento stream, level monitor, candle backfill) ──
    # Everything is gated on market hours: when Globex is closed (weekend),
    # nothing starts — zero threads, zero network, zero CPU.
    # A lightweight watcher sleeps until market opens, then boots everything.
    databento_key = os.environ.get("DATABENTO_API_KEY")
    _databento_stream = None
    if databento_key and not _mirror_only and not _stocks_mode:
        from ..db.models import get_market_session as _get_market_session
        from ..db.models import get_session as _get_db_session
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

                # --- Check if Rithmic is configured (takes priority over Databento) ---
                from ..rithmic.config import RithmicConfig

                rithmic_config = RithmicConfig.from_env()

                if rithmic_config.is_configured:
                    from ..market_data.level_monitor import LevelMonitor
                    from ..rithmic.broker_client import RithmicBrokerClient
                    from ..rithmic.stream import RithmicStream
                    from ..services.market_service import MarketService

                    rithmic_stream = RithmicStream(rithmic_config, db_session_factory=_get_market_session)
                    level_monitor = LevelMonitor(publish_fn=rithmic_stream._publish)
                    rithmic_stream.set_level_monitor(level_monitor)
                    app.state.rithmic_stream = rithmic_stream
                    app.state.level_monitor = level_monitor

                    await rithmic_stream.start()
                    logger.info("Rithmic stream started (replaces Databento for live data)")

                    # Setup broker via Rithmic
                    from ..broker.config import BrokerConfig

                    broker_config = BrokerConfig.from_env()
                    if broker_config.enabled:
                        rithmic_broker = RithmicBrokerClient(rithmic_stream._client, rithmic_config)
                        connected = await rithmic_broker.connect()
                        if connected:
                            from ..broker.adapter import BrokerAdapter
                            from ..broker.flatten_scheduler import FlattenScheduler

                            _broker_adapter = BrokerAdapter(rithmic_broker, broker_config)
                            app.state.broker_adapter = _broker_adapter
                            level_monitor.set_broker_adapter(_broker_adapter)
                            flatten_sched = FlattenScheduler(_broker_adapter, broker_config.flatten_et)
                            flatten_sched.start()
                            logger.info("Broker enabled via Rithmic: %s", rithmic_config.symbol)
                        else:
                            logger.error("Broker: Rithmic connection failed — trading disabled")
                    else:
                        logger.info("Broker disabled (BROKER_ENABLED != true)")

                    logger.info("Trading features started: Rithmic stream + level monitor")
                else:
                    # Databento fallback (used when Rithmic is not configured)
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

                    # --- Broker (automated execution) ---
                    from ..broker.config import BrokerConfig

                    broker_config = BrokerConfig.from_env()
                    if broker_config.enabled:
                        from ..broker.adapter import BrokerAdapter
                        from ..broker.flatten_scheduler import FlattenScheduler
                        from ..broker.tradovate_client import TradovateClient

                        tv_client = TradovateClient(broker_config)
                        connected = await tv_client.connect()
                        if connected:
                            _broker_adapter = BrokerAdapter(tv_client, broker_config)
                            app.state.broker_adapter = _broker_adapter
                            level_monitor.set_broker_adapter(_broker_adapter)

                            flatten_sched = FlattenScheduler(_broker_adapter, broker_config.flatten_et)
                            flatten_sched.start()
                            logger.info(
                                "Broker enabled: %s %s (max_pos=%d, max_loss=$%.0f)",
                                broker_config.env,
                                broker_config.symbol,
                                broker_config.max_position,
                                broker_config.max_daily_loss,
                            )
                        else:
                            logger.error("Broker: Tradovate connection failed — trading disabled")
                    else:
                        logger.info("Broker disabled (BROKER_ENABLED != true)")

                # Load initial levels + COT in background thread (DB-heavy, would stall event loop)
                import threading

                def _load_initial_data():
                    loop = asyncio.new_event_loop()

                    async def _run():
                        try:
                            svc = MarketService(_get_db_session())
                            try:
                                # Try today first, fall back to yesterday if no data yet
                                session_data = None
                                expanded = None
                                # Try yesterday first (today may have no RTH data yet pre-market)
                                from datetime import date, timedelta

                                for attempt_date in [None, "yesterday"]:
                                    try:
                                        if attempt_date == "yesterday":
                                            yesterday = (date.today() - timedelta(days=1)).isoformat()
                                            session_data = await svc.compute_session(yesterday)
                                        else:
                                            session_data = await svc.compute_session()
                                        expanded = await svc.build_expanded_session()
                                        if expanded:
                                            if attempt_date == "yesterday":
                                                logger.info("Using yesterday's session data")
                                            break
                                    except Exception as exc:
                                        logger.debug("compute attempt %s failed: %s", attempt_date, exc)
                                        continue

                                if expanded:
                                    level_monitor.load_levels(expanded)
                                    logger.info("Initial levels loaded")

                                # Set session context so zones get correct ATR bounds
                                if session_data and isinstance(session_data, dict):
                                    rl_context = _build_rl_context_from_session(session_data, expanded)
                                    level_monitor.set_session_context(rl_context)
                                    logger.info(
                                        "Auto-compute: session context set (ATR=%.1f)", rl_context.get("atr", 0)
                                    )
                                else:
                                    logger.warning(
                                        "No session data available — zones may be empty until first recompute"
                                    )
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

                # Periodic session recompute — rebuild zones every 5 min as candle data accumulates
                async def _periodic_recompute():
                    await asyncio.sleep(300)  # wait 5 min for initial data to settle
                    while True:
                        try:
                            svc = MarketService(_get_db_session())
                            try:
                                session_data = None
                                expanded = None
                                from datetime import date, timedelta

                                for attempt_date in [None, "yesterday"]:
                                    try:
                                        if attempt_date == "yesterday":
                                            yesterday = (date.today() - timedelta(days=1)).isoformat()
                                            session_data = await svc.compute_session(yesterday)
                                        else:
                                            session_data = await svc.compute_session()
                                        expanded = await svc.build_expanded_session()
                                        if expanded:
                                            break
                                    except Exception:
                                        continue
                                if expanded and level_monitor:
                                    level_monitor.load_levels(expanded)
                                    if session_data and isinstance(session_data, dict):
                                        rl_context = _build_rl_context_from_session(session_data, expanded)
                                        level_monitor.set_session_context(rl_context)
                                    logger.info("Periodic recompute: zones rebuilt")
                            finally:
                                svc.db.close()
                        except Exception:
                            logger.info("Periodic recompute failed", exc_info=True)
                        await asyncio.sleep(300)  # every 5 min

                _recompute_task = asyncio.create_task(_periodic_recompute())
                _recompute_task.set_name("periodic-recompute")
                _background_tasks.add(_recompute_task)
                _recompute_task.add_done_callback(_background_tasks.discard)

                # Start news impact recorder (measures NQ price after economic events)
                from ..core.asyncio_supervision import supervise_task
                from ..ml.macro.news_impact_recorder import news_impact_loop

                supervise_task(
                    news_impact_loop(_get_db_session, _databento_stream),
                    name="news-impact-recorder",
                    keepalive=_background_tasks,
                )

            except Exception as e:
                logger.error("Trading features startup failed: %s", e, exc_info=True)

            # Phase 4 (2026-05-08): mirror health smoke-test loop. Runs every
            # MIRROR_SMOKE_INTERVAL_S (default 24h), HTTP-probes each provider's
            # home_url + recomputes event-derived health from `mirror_event_log`,
            # writes one row per provider into `mirror_provider_health`. Replaces
            # the static §9 capability matrix that "lied". Cancellation-safe.
            try:
                from ..jobs.mirror_smoke import smoke_loop

                asyncio.create_task(smoke_loop(), name="mirror_smoke_loop")
                logger.info("[lifespan] mirror_smoke_loop scheduled")
            except Exception as e:
                logger.error("mirror_smoke startup failed: %s", e, exc_info=True)

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
            from datetime import timedelta

            from ..config.trading_loader import get_market_data_config
            from ..market_data.databento_provider import DabentoProvider
            from ..repositories.market_repo import MarketRepo

            config = get_market_data_config()
            symbol = "NQ"
            db_symbol = config.get("symbol", "NQ.v.0")
            now = datetime.now(timezone.utc)
            fetch_end = now - timedelta(minutes=15)  # Databento ~15 min delay

            interval_targets = {
                "5m": now - timedelta(days=30),  # 1 month of 5m bars (monthly VP)
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
                                        symbol,
                                        interval,
                                        b.timestamp,
                                        b.open,
                                        b.high,
                                        b.low,
                                        b.close,
                                        b.volume,
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
                                    logger.info(
                                        "Candle gap backfill %s: filled %s → %s (%d bars)",
                                        interval,
                                        start_dt,
                                        end_dt,
                                        count,
                                    )
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
                "Trading features halted — Globex closed. Sleeping %.1f hours until market opens (zero resources used)",
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

    # ── Stocks mode: LevelMonitor + full data pipeline without Databento stream ──
    # Ticks arrive from local arnoldstocks client via /ws/signals WebSocket.
    # We wire up everything the Databento path does: tick buffer, candle flow,
    # session context, macro, AMT dynamics, orderflow — so the specialist
    # ensemble gets a full 276-dim observation vector, not zeros.
    if _stocks_mode and not _mirror_only:
        import threading

        from ..db.models import get_market_session as _get_market_session
        from ..db.models import get_session as _get_db_session
        from ..market_data.level_monitor import LevelMonitor
        from ..market_data.stream import CandleFlow, TickBuffer
        from ..repositories.market_repo import MarketRepo
        from ..services.market_service import MarketService

        def _stocks_publish(event: dict) -> None:
            pass  # SSE not needed for stocks mode (no browser UI on server)

        # 1. Create tick buffer + candle flows (same as Databento path)
        _stocks_tick_buffer = TickBuffer()
        _stocks_candle_flow_5m = CandleFlow(bucket_seconds=300, emit_interval=5.0)
        _stocks_candle_flow_1m = CandleFlow(bucket_seconds=60, emit_interval=1.0)

        # 2. Create LevelMonitor with full wiring
        level_monitor = LevelMonitor(publish_fn=_stocks_publish)
        level_monitor.set_tick_buffer(_stocks_tick_buffer)

        def _stocks_get_recent_candles():
            """Build candles from tick buffer for orderflow computation."""
            from ..market_data.orderflow import build_candle_flow

            ticks = list(_stocks_tick_buffer.ticks)
            if len(ticks) < 10:
                return []
            return build_candle_flow(ticks, period_seconds=300)

        level_monitor.set_candle_flow_source(_stocks_get_recent_candles)

        # 3. Set async context for ML/macro fetches during level touches
        _stocks_loop = asyncio.get_event_loop()
        level_monitor.set_async_context(_stocks_loop, _get_db_session)

        app.state.level_monitor = level_monitor
        # Store tick buffer + candle flows so /ws/signals can feed them
        app.state.stocks_tick_buffer = _stocks_tick_buffer
        app.state.stocks_candle_flow_5m = _stocks_candle_flow_5m
        app.state.stocks_candle_flow_1m = _stocks_candle_flow_1m
        logger.info("[Stocks] LevelMonitor initialized with tick buffer + candle flows")

        # 4. Seed CandleFlow from last DB candle (prevents fake wicks)
        try:
            _seed_db = _get_market_session()
            for _flow, _interval in [
                (_stocks_candle_flow_5m, "5m"),
                (_stocks_candle_flow_1m, "1m"),
            ]:
                _last = MarketRepo(_seed_db).get_latest_candle("NQ", _interval)
                if _last:
                    _ts = _last.ts.replace(tzinfo=timezone.utc) if not _last.ts.tzinfo else _last.ts
                    _age = (datetime.now(timezone.utc) - _ts).total_seconds()
                    if _age < 3600:  # Only seed if less than 1 hour old
                        _flow.seed(int(_ts.timestamp()), _last.o, _last.h, _last.l, _last.c, _last.v)
                        logger.info("[Stocks] Seeded %s CandleFlow from DB: %s", _interval, _ts)
                    else:
                        logger.info("[Stocks] Skipping stale %s seed (%.0fs old)", _interval, _age)
            _seed_db.close()
        except Exception:
            logger.warning("[Stocks] CandleFlow seed failed (will start fresh)")

        # 5. Load initial levels + session context in background thread
        def _load_initial_data_stocks():
            _init_loop = asyncio.new_event_loop()

            async def _run():
                try:
                    svc = MarketService(_get_db_session())
                    try:
                        from datetime import date, timedelta

                        session_data = None
                        expanded = None
                        for attempt_date in [None, "yesterday"]:
                            try:
                                if attempt_date == "yesterday":
                                    yesterday = (date.today() - timedelta(days=1)).isoformat()
                                    session_data = await svc.compute_session(yesterday)
                                else:
                                    session_data = await svc.compute_session()
                                expanded = await svc.build_expanded_session()
                                if expanded:
                                    level_monitor.load_levels(expanded)
                                    if attempt_date == "yesterday":
                                        logger.info("[Stocks] Using yesterday's session data")
                                    break
                            except Exception as exc:
                                logger.debug("[Stocks] compute attempt %s failed: %s", attempt_date, exc)

                        # Set full session context (VWAP, VP, IB, macro, swings, ATR).
                        # Also pull get_indicators() so day_type lands in the
                        # context dict the reasoning JSONB reads from.
                        if expanded and session_data and isinstance(session_data, dict):
                            rl_context = _build_rl_context_from_session(session_data, expanded)
                            try:
                                indicators = await svc.get_indicators()
                                rl_context["day_type"] = indicators.get("ml_day_type")
                                rl_context["day_type_confidence"] = indicators.get("ml_day_type_confidence")
                                if rl_context["day_type"] is None:
                                    # Predictor not loaded or feature build returned empty —
                                    # all overnight signals had day_type=null because of this.
                                    # Log loudly so we know which one to fix.
                                    logger.warning(
                                        "[Stocks] day_type unavailable (init): predictor returned None — model is not classifying day type"
                                    )
                            except Exception:
                                logger.exception("[Stocks] get_indicators raised in init load")
                            level_monitor.set_session_context(rl_context)
                            logger.info(
                                "[Stocks] Session context set: %d keys, ATR=%.1f",
                                len(rl_context),
                                rl_context.get("atr", 0),
                            )
                        elif expanded:
                            logger.info(
                                "[Stocks] Levels loaded (%d) but no session context", len(level_monitor._levels)
                            )
                    finally:
                        svc.db.close()
                except Exception:
                    logger.exception("[Stocks] Initial data load failed")

            _init_loop.run_until_complete(_run())
            _init_loop.close()

        threading.Thread(target=_load_initial_data_stocks, daemon=True, name="stocks-init").start()

        # 6. Periodic session recompute (every 5 minutes, same as Databento path)
        async def _stocks_periodic_recompute():
            await asyncio.sleep(300)  # initial settle time
            while True:
                try:
                    svc = MarketService(_get_db_session())
                    try:
                        session_data = await svc.compute_session()
                        expanded = await svc.build_expanded_session()
                        if expanded:
                            level_monitor.load_levels(expanded)
                            if session_data and isinstance(session_data, dict):
                                rl_context = _build_rl_context_from_session(session_data, expanded)
                                try:
                                    indicators = await svc.get_indicators()
                                    rl_context["day_type"] = indicators.get("ml_day_type")
                                    rl_context["day_type_confidence"] = indicators.get("ml_day_type_confidence")
                                    if rl_context["day_type"] is None:
                                        logger.warning(
                                            "[Stocks] day_type unavailable (recompute): predictor returned None"
                                        )
                                except Exception:
                                    logger.exception("[Stocks] get_indicators raised in periodic recompute")
                                level_monitor.set_session_context(rl_context)
                            logger.info(
                                "[Stocks] Session recomputed: %d levels, %d zones",
                                len(level_monitor._levels),
                                len(level_monitor._zones),
                            )
                    finally:
                        svc.db.close()
                except Exception:
                    logger.warning("[Stocks] Periodic recompute failed", exc_info=True)
                await asyncio.sleep(300)

        _recompute_task = asyncio.create_task(_stocks_periodic_recompute())
        _recompute_task.set_name("stocks-recompute")

        # 7. Autonomous server-side TopstepX bootstrap (gated by STOCKS_AUTONOMOUS=true).
        # Replaces the need for the local arnold app to feed ticks through /ws/signals:
        # the server authenticates, streams, places orders, and persists trades directly.
        #
        # Runs as a background task so the startup-grace sleep inside
        # bootstrap_stocks_on_server (meant to let a prior container's
        # TopstepX session tear down on TopstepX's side) does not block
        # FastAPI lifespan and stall /health.
        try:
            from ..stocks.server_bootstrap import bootstrap_stocks_on_server

            async def _stocks_bg_bootstrap():
                # Retry-forever supervisor: a single auth/network failure used to
                # leave the broker silently dead until container restart. Now we
                # back off and try again so transient failures self-heal.
                # Config-level skips (STOCKS_AUTONOMOUS off, missing creds) exit
                # immediately — retrying can't fix those.
                if os.environ.get("STOCKS_AUTONOMOUS", "").lower() != "true":
                    return
                # Exponential backoff: 60s → 120s → 240s → 480s → 960s → cap 1800s.
                # Weekend maintenance returns errorCode 3 indistinguishable from a
                # revoked key (see project_topstepx_api_subscription.md) — fixed
                # 60s retries hammer the auth endpoint for ~24h every weekend.
                BACKOFF_BASE_S = 60
                BACKOFF_CAP_S = 1800
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        rt = await bootstrap_stocks_on_server(app)
                        if rt is not None:
                            return  # success — runtime is installed on app.state
                        sleep_s = min(BACKOFF_BASE_S * (2 ** min(attempt - 1, 5)), BACKOFF_CAP_S)
                        logger.warning(
                            "[Stocks] Bootstrap returned None (attempt %d); retrying in %ds",
                            attempt,
                            sleep_s,
                        )
                    except Exception:
                        sleep_s = min(BACKOFF_BASE_S * (2 ** min(attempt - 1, 5)), BACKOFF_CAP_S)
                        logger.exception(
                            "[Stocks] Bootstrap raised (attempt %d); retrying in %ds",
                            attempt,
                            sleep_s,
                        )
                    await asyncio.sleep(sleep_s)

            _stocks_bootstrap_task = asyncio.create_task(
                _stocks_bg_bootstrap(),
                name="stocks-bootstrap",
            )
            _background_tasks.add(_stocks_bootstrap_task)
            _stocks_bootstrap_task.add_done_callback(_background_tasks.discard)
        except Exception:
            logger.exception("[Stocks] Autonomous bootstrap could not be scheduled")
        _background_tasks.add(_recompute_task)
        _recompute_task.add_done_callback(_background_tasks.discard)

    # Auto-start all mirror browsers (always-on recording)
    from ..mirror.service import MirrorService
    from ..pipeline.broadcast import odds_broadcaster as _mirror_broadcaster
    from .routes.mirror import _load_all_providers, _mirrors

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
            from sqlalchemy import create_engine
            from sqlalchemy import text as sa_text

            from ..rl.live_collector import get_live_collector

            _market_engine = create_engine(
                os.environ.get(
                    "MARKET_DATABASE_URL",
                    "postgresql://arnold:arnold2026secure@postgres:5432/market",
                ).replace("+asyncpg", ""),
                pool_size=5,
                max_overflow=5,
                pool_pre_ping=True,
            )

            async def _get_recent_trades(since, until):
                """Query market_trades for outcome measurement."""
                with _market_engine.connect() as conn:
                    rows = conn.execute(
                        sa_text(
                            "SELECT ts, price, size FROM market_trades WHERE ts >= :since AND ts <= :until ORDER BY ts"
                        ),
                        {"since": since, "until": until},
                    ).fetchall()
                return [{"ts": r[0], "price": r[1], "size": r[2]} for r in rows]

            collector = get_live_collector()
            _live_collector_task = asyncio.create_task(collector.measure_outcomes_loop(_get_recent_trades))
            logger.info("Live RL episode collector started")
        except Exception:
            logger.debug("Live RL episode collector not available", exc_info=True)

    # ── TopstepX candle poller (REST-based, no WebSocket session) ──
    _topstepx_poller = None
    if os.environ.get("TOPSTEPX_POLLER_ENABLED", "").lower() in ("1", "true", "yes"):
        from ..db.models import get_market_session as _poller_db
        from ..market_data.topstepx_poller import TopstepXPoller

        _topstepx_poller = TopstepXPoller(db_session_factory=_poller_db)
        app.state.topstepx_poller = _topstepx_poller

        _poller_task = asyncio.create_task(_topstepx_poller.start())
        _poller_task.set_name("topstepx-poller")
        _background_tasks.add(_poller_task)
        _poller_task.add_done_callback(_background_tasks.discard)
        logger.info("[Startup] TopstepX candle poller enabled")
    else:
        logger.info("[Startup] TopstepX candle poller disabled (set TOPSTEPX_POLLER_ENABLED=true)")

    yield  # App is running

    # Graceful shutdown — flatten any open TopstepX position FIRST. Deploying
    # while a trade is open would otherwise leave the position running naked
    # (no server-side stop monitor) until the next restart.
    _stocks_rt = getattr(app.state, "stocks_runtime", None)
    if _stocks_rt is not None:
        try:
            await _stocks_rt.shutdown(flatten_positions=True)
        except Exception:
            logger.exception("ServerStocksRuntime shutdown raised")

    # Graceful shutdown — flush live episodes before stopping
    if _live_collector_task and not _live_collector_task.done():
        _live_collector_task.cancel()
        with suppress(asyncio.CancelledError):
            await _live_collector_task
    try:
        from ..rl.live_collector import get_live_collector

        collector = get_live_collector()
        stats = collector.get_stats()
        if stats["buffered"] > 0:
            collector.flush()
            logger.info("Flushed %d live episodes on shutdown", stats["buffered"])
    except Exception:
        pass
    logger.info("Shutting down...")

    # Cancel the trading gate sleep loop (can block for hours when market closed)
    if "_trading_gate_task" in dir() and not _trading_gate_task.done():
        _trading_gate_task.cancel()
        with suppress(asyncio.CancelledError):
            await _trading_gate_task

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

    # Kill RL daemon subprocess so it doesn't outlive the app
    try:
        pid_file = "/app/data/rl/daemon.pid"
        import signal

        with open(pid_file) as f:
            rl_pid = int(f.read().strip())
        os.kill(rl_pid, signal.SIGTERM)
        logger.info(f"Sent SIGTERM to RL daemon (PID {rl_pid})")
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        pass  # Not running or already gone

    logger.info("Shutdown complete.")


app = FastAPI(
    title="Arnold API",
    description="Betting analytics & value betting backend",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/debug/zones")
async def debug_zones(request: Request):
    """Debug endpoint: show active zones vs current price."""
    lm = getattr(request.app.state, "level_monitor", None)
    if lm is None:
        return {"error": "LevelMonitor not initialized"}
    zones = [
        {
            "center": round(z.center_price, 2),
            "lower": round(z.lower_bound, 2),
            "upper": round(z.upper_bound, 2),
            "members": z.member_count,
        }
        for z in sorted(lm._zones, key=lambda z: z.center_price)
    ]
    return {
        "last_price": round(lm._last_price, 2),
        "zone_count": len(zones),
        "level_count": len(lm._levels),
        "zones": zones,
    }


# GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)


# App-level API key auth — defense-in-depth behind nginx basic auth
_api_key = os.environ.get("ARNOLD_API_KEY")
_auth_exempt = {"/health", "/health/live", "/health/ready", "/health/extraction", "/debug/zones", "/ws/signals"}


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
    result = {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "boot_id": _boot_id,
        "uptime": round(time.time() - _startup_time) if _startup_time else 0,
    }
    poller = getattr(app.state, "topstepx_poller", None)
    if poller:
        result["market_data_poller"] = poller.get_status()
    return result


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
    from ..db.models import Provider
    from .deps import get_db

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


@app.get("/health/extraction")
async def health_extraction():
    """Public extraction health endpoint — no auth required.

    Deep health assessment: checks sharp source freshness, consecutive
    provider failures, staleness vs expected intervals, DB integrity
    errors, and opportunity volume drops.
    """
    from ..db.models import ExtractionRun, ProviderRunMetrics
    from ..pipeline.health import assess_extraction_health, get_provider_intervals
    from .deps import get_db

    def _query():
        db = None
        try:
            db = next(get_db())

            # ── Deep health assessment ──
            intervals = get_provider_intervals()
            health_status, issues, providers_health = assess_extraction_health(db, intervals)

            # ── Last 3 runs for the response body ──
            runs = db.query(ExtractionRun).order_by(ExtractionRun.start_time.desc()).limit(3).all()
            run_data = []
            for run in runs:
                providers = db.query(ProviderRunMetrics).filter(ProviderRunMetrics.run_id == run.id).all()
                failed = [
                    {"provider": p.provider_id, "error": (p.error_message or "")[:200], "status": p.status}
                    for p in providers
                    if p.status in ("failed", "timeout")
                ]
                low_match = [
                    {
                        "provider": p.provider_id,
                        "matched": p.events_matched or 0,
                        "unmatched": p.events_unmatched or 0,
                        "match_rate": round(
                            (p.events_matched or 0) / max((p.events_matched or 0) + (p.events_unmatched or 0), 1) * 100
                        ),
                    }
                    for p in providers
                    if (p.events_matched or 0) + (p.events_unmatched or 0) > 0
                    and (p.events_matched or 0) / max((p.events_matched or 0) + (p.events_unmatched or 0), 1) < 0.3
                ]
                run_data.append(
                    {
                        "id": run.id,
                        "start_time": run.start_time.isoformat() if run.start_time else None,
                        "duration_seconds": run.duration_seconds,
                        "trigger": run.trigger,
                        "providers_attempted": run.providers_attempted,
                        "providers_succeeded": run.providers_succeeded,
                        "providers_failed": run.providers_failed,
                        "total_events": run.total_events,
                        "total_odds": run.total_odds,
                        "failed_providers": failed,
                        "low_match_rate": low_match,
                    }
                )

            return {
                "status": health_status,
                "issues": issues,
                "providers": providers_health,
                "runs": run_data,
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            if db:
                db.close()

    # Run on a dedicated executor so health probes don't compete with the
    # default asyncio executor, which is heavily used by per-sport storage
    # threads during extraction and can saturate the 8-thread default pool.
    try:
        data = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(_health_executor, _query),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Database query timed out"}

    if isinstance(data, dict) and "error" in data:
        return {"status": "error", "message": data["error"]}

    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    return data


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
app.include_router(mirror_state_router)
app.include_router(mirror_stream_router)
app.include_router(fire_window_router)
app.include_router(slip_odds_router)
app.include_router(signals_ws_router)
app.include_router(stocks_router)


# Version endpoint
@app.get("/api/version")
async def get_version():
    """Return app version and runtime info."""
    from ..paths import get_data_dir

    return {
        "version": app.version,
        "data_dir": str(get_data_dir()),
    }


@app.get("/")
async def root():
    """Backend is API-only. Visual clients (ArnoldSports / ArnoldStocks) run locally."""
    return {"status": "arnold-api", "version": app.version}


# Dev entry point (no --reload). On Windows, --reload forces SelectorEventLoop
# which breaks patchright subprocess spawning. Without --reload, uvicorn uses
# ProactorEventLoop correctly. Use run_dev.py if you need hot-reload.
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api:app", host="127.0.0.1", port=8000)
