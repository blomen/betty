// Isolated world — has WebSocket privilege (bypasses page CSP because
// the request is initiated from the extension origin, not the page).
// Bridges between the local Arnold server and the page-world script.

(function () {
  'use strict';

  const SERVER_WS = 'ws://127.0.0.1:8000/stocks/ws/tv-overlay';
  const RECONNECT_MS = 2000;

  let ws = null;
  let reconnectTimer = null;

  function connect() {
    try { ws = new WebSocket(SERVER_WS); } catch (e) { scheduleReconnect(); return; }

    ws.onopen = () => {
      console.log('[arnold-overlay/bridge] connected');
      try { ws.send(JSON.stringify({ type: 'hello', version: '0.1.0', href: location.href })); } catch (_) {}
    };

    ws.onmessage = (ev) => {
      // Forward server message to the main-world script via custom event
      document.dispatchEvent(new CustomEvent('arnold:msg', { detail: ev.data }));
    };

    ws.onclose = () => { ws = null; scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, RECONNECT_MS);
  }

  // Receive ack/error events from the main-world script and forward upstream
  document.addEventListener('arnold:up', (ev) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(ev.detail); } catch (_) {}
  });

  connect();
})();
