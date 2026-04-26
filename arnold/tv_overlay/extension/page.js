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

  // Native trading widgets — TradingView's createPositionLine / createOrderLine
  // / createStudy return Promises and produce the same blue-handle position
  // body + colored stop/target lines + Anchored Volume Profile that the TV
  // platform uses for its built-in trade panel. Way more useful than 3 raw
  // horizontal_line shapes.
  //
  // The position widgets live in a separate registry from `drawn` (which holds
  // shape entity ids) because they have a different removal API (.remove()
  // method on the resolved object, not chart.removeEntity).
  const drawnPositions = new Map(); // key → { posLine, stopOrder, tpOrder, avpStudyId }

  async function drawPosition(p) {
    if (!chart) return false;
    await removePosition(p.key);

    const sideColor = p.side === 'long' ? '#10b981' : '#ef4444';
    const isLong = p.side === 'long';

    try {
      // Position body (entry + qty + auto-computed P&L + R:R when TV knows
      // the live price, which it does on this chart).
      const posLine = await chart.createPositionLine();
      try {
        posLine
          .setPrice(p.entry)
          .setQuantity(String(p.size ?? 1))
          .setText(`${p.side.toUpperCase()} entry ${p.entry.toFixed(2)}`)
          .setExtendLeft(false)
          .setLineColor(sideColor)
          .setBodyBorderColor(sideColor)
          .setBodyBackgroundColor(isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)');
      } catch (_) { /* tolerate missing chained methods */ }

      const entry = { posLine };

      if (p.stop != null) {
        try {
          const stopOrder = await chart.createOrderLine();
          try {
            stopOrder
              .setPrice(p.stop)
              .setQuantity(String(p.size ?? 1))
              .setText(`stop ${p.stop.toFixed(2)}`)
              .setLineColor('#dc2626')
              .setBodyBorderColor('#dc2626')
              .setBodyBackgroundColor('rgba(220,38,38,0.15)');
          } catch (_) {}
          entry.stopOrder = stopOrder;
        } catch (_) {}
      }
      if (p.tp != null) {
        try {
          const tpOrder = await chart.createOrderLine();
          try {
            tpOrder
              .setPrice(p.tp)
              .setQuantity(String(p.size ?? 1))
              .setText(`tp ${p.tp.toFixed(2)}`)
              .setLineColor('#22c55e')
              .setBodyBorderColor('#22c55e')
              .setBodyBackgroundColor('rgba(34,197,94,0.15)');
          } catch (_) {}
          entry.tpOrder = tpOrder;
        } catch (_) {}
      }

      // Anchored Volume Profile rooted at the entry time so volume
      // distribution since entry is visible at a glance. Best-effort —
      // the study id isn't always returned synchronously, so we hold the
      // Promise and resolve the actual id when removing.
      try {
        const now = Math.floor(Date.now() / 1000);
        const avpPromise = chart.createStudy('Anchored Volume Profile', false, false, [now]);
        entry.avpStudyId = avpPromise; // may be a Promise<id> or an id
      } catch (_) { /* AVP unsupported on this account/symbol — non-fatal */ }

      drawnPositions.set(p.key, entry);
      return true;
    } catch (e) {
      sendError(`drawPosition failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  async function removePosition(key) {
    const entry = drawnPositions.get(key);
    if (!entry) return;
    drawnPositions.delete(key);
    for (const widget of [entry.posLine, entry.stopOrder, entry.tpOrder]) {
      if (widget && typeof widget.remove === 'function') {
        try { widget.remove(); } catch (_) {}
      }
    }
    try {
      const studyId = await entry.avpStudyId;
      if (studyId != null && chart && typeof chart.removeEntity === 'function') {
        try { chart.removeEntity(studyId); } catch (_) {}
      }
    } catch (_) {}
  }

  attachPromise.then((c) => {
    if (!c) {
      console.warn('[arnold-overlay/page] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay/page] attached to chart', c);

    document.addEventListener('arnold:msg', async (ev) => {
      let msg;
      try { msg = JSON.parse(ev.detail); } catch (_) { return; }
      switch (msg.type) {
        case 'zone_upsert':     if (drawZone(msg)) sendAck(1); break;
        case 'zone_remove':     safeRemove(msg.key); sendAck(1); break;
        case 'position_upsert': if (await drawPosition(msg)) sendAck(1); break;
        case 'position_remove': await removePosition(msg.key); sendAck(1); break;
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
