// Main world — runs in page context, has access to window.TradingViewApi.
// Receives server messages from bridge.js (isolated world) via custom events
// and translates them to chart.createMultipointShape / removeEntity calls.

(function () {
  'use strict';

  const ATTACH_POLL_MS = 1000;
  const ATTACH_MAX_TRIES = 60;

  const COLOR_BY_STRENGTH = (s) => {
    if (s < 0.25) return '#475569';
    if (s < 0.5)  return '#6366f1';
    if (s < 0.7)  return '#d946ef';
    if (s < 0.9)  return '#f97316';
    return '#ef4444';
  };

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

  const drawn = new Map();

  function up(payload) {
    document.dispatchEvent(new CustomEvent('arnold:up', { detail: JSON.stringify(payload) }));
  }

  function sendAck(count) { up({ type: 'ack', count }); }
  function sendError(message) { up({ type: 'error', message }); }

  function safeRemove(key) {
    const entityId = drawn.get(key);
    if (entityId == null || !chart) return;
    try { chart.removeEntity(entityId); } catch (_) {}
    drawn.delete(key);
  }

  function drawZone(p) {
    if (!chart) return false;
    safeRemove(p.key);
    const now = Math.floor(Date.now() / 1000);
    const tStart = now - 8 * 60 * 60;
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
      if (id != null) {
        drawn.set(p.key, id);
        return true;
      }
      return false;
    } catch (e) {
      sendError(`drawZone failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  function drawPosition(p) {
    if (!chart) return false;
    const baseKey = p.key;
    safeRemove(baseKey + ':entry');
    safeRemove(baseKey + ':stop');
    safeRemove(baseKey + ':tp');

    const sideColor = p.side === 'long' ? '#10b981' : '#ef4444';
    const now = Math.floor(Date.now() / 1000);

    let entryDrawn = false;
    try {
      const entryId = chart.createMultipointShape(
        [{ time: now, price: p.entry }],
        { shape: 'horizontal_line', text: `${p.side.toUpperCase()} entry ${p.entry.toFixed(2)}`,
          overrides: { linecolor: sideColor, showLabel: true } }
      );
      if (entryId != null) {
        drawn.set(baseKey + ':entry', entryId);
        entryDrawn = true;
      }

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
      sendError(`drawPosition failed: ${e instanceof Error ? e.message : String(e)}`);
      return entryDrawn;
    }
    return entryDrawn;
  }

  function removePosition(key) {
    safeRemove(key + ':entry');
    safeRemove(key + ':stop');
    safeRemove(key + ':tp');
  }

  attachPromise.then((c) => {
    if (!c) {
      console.warn('[arnold-overlay/page] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay/page] attached to chart', c);

    document.addEventListener('arnold:msg', (ev) => {
      let msg;
      try { msg = JSON.parse(ev.detail); } catch (_) { return; }
      switch (msg.type) {
        case 'zone_upsert':     if (drawZone(msg)) sendAck(1); break;
        case 'zone_remove':     safeRemove(msg.key); sendAck(1); break;
        case 'position_upsert': if (drawPosition(msg)) sendAck(1); break;
        case 'position_remove': removePosition(msg.key); sendAck(1); break;
        case 'ping_zone': {
          try {
            const entityId = drawn.get(msg.zone_key);
            if (entityId != null && chart && typeof chart.bringToFront === 'function') {
              chart.bringToFront(entityId);
            }
          } catch (_) {}
          break;
        }
      }
    });
  });
})();
