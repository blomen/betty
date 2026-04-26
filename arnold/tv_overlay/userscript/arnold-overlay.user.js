// ==UserScript==
// @name         Arnold TradingView Overlay
// @namespace    https://github.com/blomen/arnold
// @version      0.1.0
// @description  Draws Arnold zones and open positions on TradingView charts via WebSocket from local Arnold server.
// @match        https://*.tradingview.com/*
// @match        https://tradingview.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  // --- Config ---
  const SERVER_WS = 'ws://127.0.0.1:8000/stocks/ws/tv-overlay';
  const RECONNECT_MS = 2000;
  const ATTACH_POLL_MS = 1000;
  const ATTACH_MAX_TRIES = 60;

  const COLOR_BY_STRENGTH = (s) => {
    if (s < 0.25) return '#475569';
    if (s < 0.5)  return '#6366f1';
    if (s < 0.7)  return '#d946ef';
    if (s < 0.9)  return '#f97316';
    return '#ef4444';
  };

  // --- TV chart attach ---
  // Phase 0 confirmed window.TradingViewApi.activeChart() works on
  // tradingview.com web. tvWidget / TradingView are kept as fallbacks
  // for future TV builds where the entry path may differ.
  function getChart() {
    try {
      if (window.TradingViewApi && typeof window.TradingViewApi.activeChart === 'function') {
        return window.TradingViewApi.activeChart();
      }
    } catch (_) {}
    try {
      if (window.tvWidget && typeof window.tvWidget.activeChart === 'function') {
        return window.tvWidget.activeChart();
      }
    } catch (_) {}
    try {
      if (window.TradingView && window.TradingView.activeChart) {
        return window.TradingView.activeChart();
      }
    } catch (_) {}
    return null;
  }

  let chart = null;
  let attachAttempts = 0;
  const attachPromise = new Promise((resolve) => {
    const tick = () => {
      attachAttempts += 1;
      const c = getChart();
      if (c) { chart = c; resolve(c); return; }
      if (attachAttempts >= ATTACH_MAX_TRIES) { resolve(null); return; }
      setTimeout(tick, ATTACH_POLL_MS);
    };
    tick();
  });

  // --- Drawing registry ---
  const drawn = new Map(); // key → entityId

  function safeRemove(key) {
    const entityId = drawn.get(key);
    if (entityId == null || !chart) return;
    try { chart.removeEntity(entityId); } catch (e) { /* ignore */ }
    drawn.delete(key);
  }

  function drawZone(p) {
    if (!chart) return;
    safeRemove(p.key);
    const now = Math.floor(Date.now() / 1000);
    const tStart = now - 8 * 60 * 60; // 8h back
    const tEnd = now;
    const color = COLOR_BY_STRENGTH(p.strength);
    try {
      const id = chart.createMultipointShape(
        [
          { time: tStart, price: p.top },
          { time: tEnd,   price: p.bottom },
        ],
        {
          shape: 'rectangle',
          text: `${p.kind} ×${p.members}`,
          overrides: {
            color: color,
            backgroundColor: color,
            transparency: Math.max(20, 80 - Math.round(p.strength * 60)),
            showLabel: true,
          },
        }
      );
      if (id != null) drawn.set(p.key, id);
    } catch (e) {
      sendError(`drawZone failed: ${e && e.message}`);
    }
  }

  function drawPosition(p) {
    if (!chart) return;
    const baseKey = p.key;
    safeRemove(baseKey + ':entry');
    safeRemove(baseKey + ':stop');
    safeRemove(baseKey + ':tp');

    const sideColor = p.side === 'long' ? '#10b981' : '#ef4444';
    const now = Math.floor(Date.now() / 1000);

    try {
      const entryId = chart.createMultipointShape(
        [{ time: now, price: p.entry }],
        { shape: 'horizontal_line', text: `${p.side.toUpperCase()} entry ${p.entry.toFixed(2)}`,
          overrides: { linecolor: sideColor, showLabel: true } }
      );
      if (entryId != null) drawn.set(baseKey + ':entry', entryId);

      if (p.stop != null) {
        const stopId = chart.createMultipointShape(
          [{ time: now, price: p.stop }],
          { shape: 'horizontal_line', text: `stop ${p.stop.toFixed(2)}`,
            overrides: { linecolor: '#dc2626', showLabel: true } }
        );
        if (stopId != null) drawn.set(baseKey + ':stop', stopId);
      }
      if (p.tp != null) {
        const tpId = chart.createMultipointShape(
          [{ time: now, price: p.tp }],
          { shape: 'horizontal_line', text: `tp ${p.tp.toFixed(2)}`,
            overrides: { linecolor: '#22c55e', showLabel: true } }
        );
        if (tpId != null) drawn.set(baseKey + ':tp', tpId);
      }
    } catch (e) {
      sendError(`drawPosition failed: ${e && e.message}`);
    }
  }

  function removePosition(key) {
    safeRemove(key + ':entry');
    safeRemove(key + ':stop');
    safeRemove(key + ':tp');
  }

  // --- WebSocket loop ---
  let ws = null;
  let reconnectTimer = null;

  function sendAck(count) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: 'ack', count })); } catch (_) {}
  }

  function sendError(message) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: 'error', message })); } catch (_) {}
  }

  function connect() {
    try { ws = new WebSocket(SERVER_WS); } catch (e) { return scheduleReconnect(); }

    ws.onopen = () => {
      console.log('[arnold-overlay] connected');
      try { ws.send(JSON.stringify({ type: 'hello', version: '0.1.0', href: location.href })); } catch (_) {}
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      switch (msg.type) {
        case 'zone_upsert': drawZone(msg); sendAck(1); break;
        case 'zone_remove': safeRemove(msg.key); sendAck(1); break;
        case 'position_upsert': drawPosition(msg); sendAck(1); break;
        case 'position_remove': removePosition(msg.key); sendAck(1); break;
        case 'ping_zone': {
          // Flash-ping a zone — bring camera to it (best effort).
          try {
            const entityId = drawn.get(msg.zone_key);
            if (entityId != null && chart && typeof chart.bringToFront === 'function') {
              chart.bringToFront(entityId);
            }
          } catch (_) {}
          break;
        }
      }
    };

    ws.onclose = () => { ws = null; scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, RECONNECT_MS);
  }

  // --- Boot ---
  attachPromise.then((c) => {
    if (!c) {
      console.warn('[arnold-overlay] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay] attached to chart', c);
    connect();
  });
})();
