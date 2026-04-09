# firevstocks — Local NQ Futures Trading Client

**Date:** 2026-04-09
**Status:** Approved

## Purpose

Local Windows client that connects to the Hetzner server for ML inference and to TopstepX for market data + order execution. Complies with Topstep's "personal device" rule (no VPS) while keeping heavy compute on the server.

Drops Databento live feed ($100/mo saved). Databento stays for historical RL replay only.

## Architecture

```
Hetzner Server (24/7)                        Your Windows PC (firevstocks.bat)
┌─────────────────────────────┐              ┌──────────────────────────────────┐
│ /ws/signals  WebSocket      │◄── WS ──────│ SignalRelayClient                │
│   ├── receives ticks        │              │   ├── forwards ticks to server   │
│   ├── runs LevelMonitor     │── WS ──────►│   ├── receives signals back      │
│   └── runs Specialists      │              │   └── triggers order execution   │
│                              │              │                                  │
│ PostgreSQL (zones, candles,  │◄── SSH ─────│ SSH tunnel (DB reads for UI)     │
│   trades, session data)      │   tunnel    │                                  │
└─────────────────────────────┘              │ TopstepXClient (REST)            │
                                              │   ├── auth (loginKey)            │
                                              │   ├── place/cancel/modify orders │
                                              │   ├── get positions/account      │
                                              │   └── get historical bars        │
                                              │                                  │
                                              │ TopstepXStream (SignalR)         │
                                              │   ├── live ticks → relay to srv  │
                                              │   ├── live fills → update UI     │
                                              │   └── position updates           │
                                              │                                  │
                                              │ Local FastAPI (127.0.0.1:8001)   │
                                              │   └── serves React UI            │
                                              │       ├── Chart tab              │
                                              │       ├── DQN tab                │
                                              │       ├── Bankroll tab           │
                                              │       └── Stats tab              │
                                              └──────────────────────────────────┘
```

## Data Flow

### Tick → Signal → Order (hot path)

```
TopstepX SignalR  ──tick──►  Local PC  ──tick──►  Server /ws/signals
                                                      │
                                                      ▼
                                                  LevelMonitor.on_tick()
                                                  Zone touch detected?
                                                      │ yes
                                                      ▼
                                                  SpecialistEnsemble.decide()
                                                      │
                                                      ▼
                                              signal {action, stop, size, confidence}
                                                      │
Server /ws/signals  ──signal──►  Local PC  ──────►  TopstepX REST
                                    │                place_market_order()
                                    │                place_stop_order()
                                    ▼
                              UI updates (chart marker, DQN panel)
```

### Candle Persistence

```
TopstepX SignalR  ──tick──►  Local PC  ──tick──►  Server /ws/signals
                                                      │
                                                      ▼
                                                  CandleFlow aggregates 1m/5m bars
                                                  Persists to market_candles DB
```

The server's existing CandleFlow receives forwarded ticks and aggregates them into 1m/5m bars, same as it does today with Databento. CandleFlow is kept and fed from the WS tick stream instead of Databento. No change to MarketRepo or candle logic.

### Fill Tracking

```
TopstepX SignalR  ──fill──►  Local PC  ──fill──►  Server /ws/signals
                                 │                     │
                                 ▼                     ▼
                           UI: P&L update        PositionTracker.on_fill()
                                                 BrokerAdapter risk checks
```

## Server-Side Changes

### New: SignalRelay WebSocket Endpoint (`/ws/signals`)

Added to the existing FastAPI app. Accepts a WebSocket connection from the local client.

**Protocol (JSON messages):**

```
Client → Server:
  {"type": "tick", "price": 21450.25, "size": 3, "ts": 1712678400.123}
  {"type": "fill", "side": "long", "price": 21450.50, "size": 1, "order_id": "abc123"}
  {"type": "ping"}

Server → Client:
  {"type": "signal", "action": "enter_long", "price": 21450.25, "stop_price": 21446.25, "size": 1, "confidence": 0.82, "zone": "VWAP", "specialist": "continuation"}
  {"type": "zone_update", "zones": [...]}  (on session recompute)
  {"type": "status", "connected": true, "level_monitor": "active"}
  {"type": "pong"}
```

**Implementation:**

```python
@app.websocket("/ws/signals")
async def signal_relay(ws: WebSocket):
    await ws.accept()
    # Reuse existing LevelMonitor + SpecialistEnsemble
    level_monitor = app.state.level_monitor
    
    # Register callback: when specialist fires, send signal to client
    async def on_signal(signal: dict):
        await ws.send_json({"type": "signal", **signal})
    
    level_monitor.set_signal_callback(on_signal)
    
    try:
        while True:
            msg = await ws.receive_json()
            if msg["type"] == "tick":
                level_monitor.on_tick(msg["price"], msg["size"], msg["ts"])
            elif msg["type"] == "fill":
                adapter = app.state.broker_adapter
                if adapter:
                    adapter.tracker.on_fill(...)
    finally:
        level_monitor.set_signal_callback(None)
```

### Modified: LevelMonitor

Add `set_signal_callback(fn)` method. When set, zone touch signals are sent to the callback instead of (or in addition to) the broker adapter. This decouples signal generation from order execution — the local client handles execution.

### Modified: Startup (`api/__init__.py`)

When `FIREV_STOCKS_MODE=1` is set:
- Skip Databento stream initialization
- Skip broker adapter initialization (local client handles orders)
- Still initialize LevelMonitor + SpecialistEnsemble
- Still run session computation + periodic recompute
- Still run candle persistence (from forwarded ticks)

## Local-Side Components

### TopstepXClient (`stocks/topstepx_client.py`)

REST client for TopstepX API. Implements the same interface as TradovateClient so BrokerAdapter works unchanged.

```python
class TopstepXClient:
    BASE_URL = "https://api.topstepx.com"
    
    async def connect(self) -> bool:
        """Authenticate with loginKey, get account ID."""
        resp = await self._post("/api/Auth/loginKey", {
            "userName": self.config.username,
            "apiKey": self.config.api_key,
        })
        self._token = resp["token"]
        # Fetch accounts
        accounts = await self._post("/api/Account/search", {"onlyActiveAccounts": True})
        self._account_id = accounts[0]["id"]
        return True
    
    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place market order. action: 'Buy' or 'Sell'."""
        return await self._post("/api/Order/place", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,  # e.g. "CON.F.US.NQ.M25"
            "type": 2,  # market
            "side": 0 if action == "Buy" else 1,
            "size": quantity,
        })
    
    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        return await self._post("/api/Order/place", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,
            "type": 3,  # stop
            "side": 0 if action == "Buy" else 1,
            "size": quantity,
            "stopPrice": stop_price,
        })
    
    async def modify_order(self, order_id: int, new_stop_price: float) -> dict:
        return await self._post("/api/Order/modify", {
            "accountId": self._account_id,
            "orderId": order_id,
            "stopPrice": new_stop_price,
        })
    
    async def cancel_order(self, order_id: int) -> dict:
        return await self._post("/api/Order/cancel", {
            "accountId": self._account_id,
            "orderId": order_id,
        })
    
    async def liquidate_position(self) -> dict:
        return await self._post("/api/Position/closeContract", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,
        })
    
    async def get_positions(self) -> list[dict]:
        resp = await self._post("/api/Position/searchOpen", {
            "accountId": self._account_id,
        })
        return resp.get("positions", [])
    
    async def get_orders(self) -> list[dict]:
        resp = await self._post("/api/Order/searchOpen", {
            "accountId": self._account_id,
        })
        return resp.get("orders", [])
    
    async def close(self) -> None:
        pass  # httpx client cleanup

    async def _post(self, path: str, body: dict) -> dict:
        """POST with auth header and token refresh."""
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        resp = await self._http.post(f"{self.BASE_URL}{path}", json=body, headers=headers)
        data = resp.json()
        if not data.get("success", True):
            raise TopstepXError(data.get("errorMessage", f"API error: {data}"))
        return data
```

### TopstepXStream (`stocks/topstepx_stream.py`)

SignalR client for real-time data from TopstepX.

```python
class TopstepXStream:
    MARKET_HUB = "wss://rtc.topstepx.com/hubs/market"
    USER_HUB = "wss://rtc.topstepx.com/hubs/user"
    
    def __init__(self, token: str, contract_id: str):
        self._token = token
        self._contract_id = contract_id
        self._on_tick = None   # callback
        self._on_fill = None   # callback
    
    async def start(self):
        """Connect to both hubs, subscribe to contract."""
        # Market hub — ticks
        self._market_conn = HubConnectionBuilder() \
            .with_url(f"{self.MARKET_HUB}?access_token={self._token}",
                      options={"skip_negotiation": True}) \
            .build()
        self._market_conn.on("GatewayTrade", self._handle_trade)
        self._market_conn.start()
        self._market_conn.send("SubscribeContractTrades", [self._contract_id])
        
        # User hub — fills, positions
        self._user_conn = HubConnectionBuilder() \
            .with_url(f"{self.USER_HUB}?access_token={self._token}",
                      options={"skip_negotiation": True}) \
            .build()
        self._user_conn.on("GatewayUserTrade", self._handle_fill)
        self._user_conn.on("GatewayUserPosition", self._handle_position)
        self._user_conn.start()
        self._user_conn.send("SubscribeToPositions", [self._account_id])
    
    def _handle_trade(self, args):
        """Market trade tick → forward to callback."""
        tick = args[0] if args else args
        if self._on_tick:
            self._on_tick(tick["price"], tick["size"], tick["timestamp"])
    
    def _handle_fill(self, args):
        """User trade fill → forward to callback."""
        fill = args[0] if args else args
        if self._on_fill:
            self._on_fill(fill)
```

### SignalRelayClient (`stocks/signal_relay.py`)

WebSocket client that connects to the server's `/ws/signals` endpoint.

```python
class SignalRelayClient:
    def __init__(self, server_ws_url: str, topstepx_client: TopstepXClient):
        self._url = server_ws_url
        self._client = topstepx_client
        self._on_signal = None  # UI callback
    
    async def connect(self):
        """Connect to server signal relay."""
        self._ws = await websockets.connect(self._url)
        asyncio.create_task(self._listen())
    
    async def forward_tick(self, price: float, size: int, ts: float):
        """Forward tick from TopstepX to server for ML inference."""
        await self._ws.send(json.dumps({
            "type": "tick", "price": price, "size": size, "ts": ts,
        }))
    
    async def forward_fill(self, fill: dict):
        """Forward fill from TopstepX to server for tracking."""
        await self._ws.send(json.dumps({"type": "fill", **fill}))
    
    async def _listen(self):
        """Listen for signals from server."""
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg["type"] == "signal":
                await self._execute_signal(msg)
            elif msg["type"] == "zone_update":
                if self._on_signal:
                    self._on_signal(msg)
    
    async def _execute_signal(self, signal: dict):
        """Execute a signal by placing orders on TopstepX."""
        action = signal["action"]
        stop_price = signal["stop_price"]
        size = signal.get("size", 1)
        
        is_long = "long" in action
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        
        # Place market order
        result = await self._client.place_market_order(order_action, size)
        
        # Place stop-loss
        if stop_price:
            await self._client.place_stop_order(stop_action, size, stop_price)
        
        # Notify UI
        if self._on_signal:
            self._on_signal(signal)
```

### TopstepXConfig (`stocks/config.py`)

```python
@dataclass
class TopstepXConfig:
    username: str = ""
    api_key: str = ""
    contract_id: str = "CON.F.US.NQ.M25"
    server_ws_url: str = "ws://127.0.0.1:8000/ws/signals"  # via SSH tunnel
    
    # Risk (same as BrokerConfig, applied locally)
    max_position: int = 2
    max_daily_loss: float = 1000.0
    max_trailing_dd: float = 2000.0
    flatten_et: str = "15:55"
    
    @classmethod
    def from_env(cls) -> TopstepXConfig:
        return cls(
            username=os.environ.get("TOPSTEPX_USERNAME", ""),
            api_key=os.environ.get("TOPSTEPX_API_KEY", ""),
            contract_id=os.environ.get("TOPSTEPX_CONTRACT", "CON.F.US.NQ.M25"),
            server_ws_url=os.environ.get("SIGNAL_RELAY_URL", "ws://127.0.0.1:8000/ws/signals"),
            max_position=int(os.environ.get("BROKER_MAX_POSITION", "2")),
            max_daily_loss=float(os.environ.get("BROKER_MAX_DAILY_LOSS", "1000")),
            max_trailing_dd=float(os.environ.get("BROKER_MAX_TRAILING_DD", "2000")),
            flatten_et=os.environ.get("BROKER_FLATTEN_ET", "15:55"),
        )
```

### Launcher (`run_firevstocks.py` + `firevstocks.bat`)

Same pattern as `run_mirror.py`:

1. Kill any previous instance on port 8001
2. Open SSH tunnel to server (port 15432 for DB, port 18000 for backend WS)
3. Authenticate with TopstepX
4. Connect TopstepX stream (ticks + fills)
5. Connect signal relay to server
6. Wire: TopstepX tick → relay.forward_tick(), server signal → execute on TopstepX
7. Start local FastAPI on 127.0.0.1:8001 serving UI
8. Open browser

```
firevstocks.bat:
@echo off
cd /d "%~dp0backend"
python run_firevstocks.py
```

**SSH tunnels:**
```
SSH tunnel 1: localhost:15432 → postgres:5432  (DB reads for UI)
SSH tunnel 2: localhost:18000 → localhost:8000  (server backend WS)
```

## What Stays The Same

| Component | Changes? | Why |
|-----------|----------|-----|
| LevelMonitor | Minor | Add `set_signal_callback()` method |
| SpecialistEnsemble | No | Pure inference, no data dependency |
| PositionTracker | No | Tracks fills from any source |
| BrokerAdapter | No | Same interface, swaps client |
| FlattenScheduler | No | Calls adapter.flatten() |
| CandleFlow | Minor | Fed from WS ticks instead of Databento |
| MarketRepo | No | Candle persistence unchanged |
| Session computation | No | Still runs on server periodically |
| Databento historical | Keep | Used by `rl replay` for training |

## What Gets Removed (from live pipeline)

| Component | Why |
|-----------|-----|
| Databento live subscription | TopstepX provides ticks ($100/mo saved) |
| DatabentoLiveStream (live mode) | Replaced by forwarded TopstepX ticks |
| TradovateClient | Replaced by TopstepXClient |

## File Structure

```
backend/
├── src/
│   ├── stocks/
│   │   ├── __init__.py
│   │   ├── config.py              # TopstepXConfig
│   │   ├── topstepx_client.py     # REST client (orders, positions, account)
│   │   ├── topstepx_stream.py     # SignalR client (ticks, fills)
│   │   └── signal_relay.py        # WS client to server /ws/signals
│   └── api/
│       └── routes/
│           └── signals_ws.py      # Server-side /ws/signals endpoint (new)
├── run_firevstocks.py             # Launcher (like run_mirror.py)
firevstocks.bat                    # Windows launcher (repo root)
```

## Configuration

```env
# TopstepX credentials (local .env)
TOPSTEPX_USERNAME=your_username
TOPSTEPX_API_KEY=your_api_key
TOPSTEPX_CONTRACT=CON.F.US.NQ.M25

# Server connection (via SSH tunnel)
SIGNAL_RELAY_URL=ws://127.0.0.1:18000/ws/signals

# Risk rules (same as existing broker config)
BROKER_MAX_POSITION=2
BROKER_MAX_DAILY_LOSS=1000
BROKER_MAX_TRAILING_DD=2000
BROKER_FLATTEN_ET=15:55
```

## Error Handling

- **TopstepX disconnect**: Reconnect with backoff, halt trading until reconnected
- **Server WS disconnect**: Reconnect SSH tunnel + WS, halt trading until signal relay is back
- **Order rejection**: Log reason, don't retry, update UI
- **Token expiry**: TopstepX tokens last ~23.5h, auto-refresh via `/api/Auth/validate`
- **All connections lost**: Flatten position via TopstepX REST (safety), then reconnect
- **PC shutdown during position**: On next startup, check open positions via TopstepX API, sync state

## Dependencies (local)

```
requests          # REST calls to TopstepX
signalrcore       # SignalR WebSocket (ticks, fills) — verified on Windows
websockets        # WS to server signal relay
httpx             # async HTTP client
```

## Topstep Compliance

- All orders originate from personal device (local PC) — compliant
- No VPS/VPN used for trading — compliant
- ~5-15 trades/day at human-like frequency — not HFT
- Server only does ML inference, never touches TopstepX — compliant
- SSH tunnel is for our own DB, not for trading — compliant

## Cost

| Item | Monthly |
|------|---------|
| Topstep 50K Standard eval | $49 |
| TopstepX API access | $14.50 (with promo "topstep") |
| Databento (dropped) | -$100 |
| **Net change** | **-$36.50/mo saved** |
