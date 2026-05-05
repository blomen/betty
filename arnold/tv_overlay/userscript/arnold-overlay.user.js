// ==UserScript==
// @name         Arnold TradingView Overlay
// @namespace    https://github.com/blomen/arnold
// @version      0.4.0
// @description  Draws Arnold zones AND every closed trade as a time-bounded long_position / short_position shape anchored at the real entry→exit window (was: horizontal lines at "now" that stacked invisibly). Closed trades persist on chart across reconnects via finalizePosition.
// @match        https://*.tradingview.com/*
// @match        https://tradingview.com/*
// @run-at       document-idle
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// @updateURL    http://127.0.0.1:8000/stocks/api/tv-overlay/userscript
// @downloadURL  http://127.0.0.1:8000/stocks/api/tv-overlay/userscript
// ==/UserScript==

// Why @grant unsafeWindow + @connect: TradingView's CSP blocks ws:// from
// the page context. With any non-`none` grant, Tampermonkey runs the
// userscript in its privileged sandbox where page CSP doesn't apply to
// fetch / WebSocket calls initiated by this script. We then reach into the
// page's chart object via unsafeWindow.

(function () {
  'use strict';

  // Resolve the page-side window. In sandbox mode `window` is the script's
  // own scope; `unsafeWindow` is the actual page window with TradingViewApi.
  // Falls back to `window` if running without a sandbox (rare).
  const PAGE = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;

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

  // Families that contribute to a zone's strength score server-side but
  // should NOT paint their own thin line inside the zone — keeps the chart
  // readable and removes the "what is this random pink line" question.
  // FVGs/OBs already feed _HIERARCHY_WEIGHTS in zone_builder.py, so they
  // still strengthen zones; they just don't draw.
  const SKIP_MEMBER_DRAW_FAMILIES = new Set(['order_block', 'fvg']);

  // Anchor types render solid; σ-bands / dispersion render dashed so the
  // primary structural prices visually dominate over their bands.
  const DASHED_TYPES = new Set([
    'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
    // VAH/VAL are anchors but visually secondary to POC for picking stops.
    'daily_vah', 'daily_val',
    'weekly_vah', 'weekly_val',
    'monthly_vah', 'monthly_val',
    'tvah', 'tval', 'tibh', 'tibl',
  ]);

  // Console-toggleable. Set `unsafeWindow.arnoldOverlay.showMembers = false`
  // and the next zone diff will redraw without the thin lines. Defaults on.
  PAGE.arnoldOverlay = PAGE.arnoldOverlay || { showMembers: true };

  // --- TV chart attach ---
  // Phase 0 confirmed PAGE.TradingViewApi.activeChart() works on
  // tradingview.com web. tvWidget / TradingView are kept as fallbacks
  // for future TV builds where the entry path may differ.
  function getChart() {
    try {
      if (PAGE.TradingViewApi && typeof PAGE.TradingViewApi.activeChart === 'function') {
        return PAGE.TradingViewApi.activeChart();
      }
    } catch (_) {}
    try {
      if (PAGE.tvWidget && typeof PAGE.tvWidget.activeChart === 'function') {
        return PAGE.tvWidget.activeChart();
      }
    } catch (_) {}
    try {
      if (PAGE.TradingView && PAGE.TradingView.activeChart) {
        return PAGE.TradingView.activeChart();
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

  // Remove every entity whose registry key starts with the given prefix.
  // Used to clear all per-member lines belonging to a zone before redraw —
  // members are stored as `${zone.key}:member:${family}:${price}`.
  function safeRemovePrefix(prefix) {
    const toRemove = [];
    for (const k of drawn.keys()) {
      if (k.startsWith(prefix)) toRemove.push(k);
    }
    for (const k of toRemove) safeRemove(k);
  }

  function drawZone(p) {
    if (!chart) return false;
    safeRemove(p.key);
    safeRemovePrefix(`${p.key}:member:`);

    const now = Math.floor(Date.now() / 1000);
    const tStart = now - 8 * 60 * 60; // 8h back
    const tEnd = now;
    let color = COLOR_BY_STRENGTH(p.strength);
    let transparency = Math.max(20, 80 - Math.round(p.strength * 60));

    // Swing-family override — daily/weekly/monthly swing pivots are
    // structurally important even when they form 1-member zones (which
    // would otherwise paint the same hue as any other zone of the same
    // strength and disappear into the cluster). When ANY zone member is
    // from a swing family, force bright amber + higher opacity so swing
    // pivots pop out regardless of hierarchy strength.
    const hasSwing = (p.members_detail || []).some(m => /swing/.test(m.family || ''));
    if (hasSwing) {
      color = '#fbbf24'; // tailwind amber-400
      transparency = 50;
    }

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
            transparency: transparency,
            showLabel: true,
          },
        }
      );
      if (id != null) {
        drawn.set(p.key, id);
      } else {
        return false;
      }
    } catch (e) {
      sendError(`drawZone failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }

    // Per-member thin lines. Each member draws a 1px brush stroke as a
    // horizontal segment confined to the zone's time window so it visually
    // "lives inside" the rectangle. Color via COLOR_BY_STRENGTH(weight) so
    // each dim is pinpointed by its own hierarchy weight — strong anchors
    // (POC, monthly swings ~1.0) burn red, weak bands (VWAP σ3 ~0.3) sit
    // slate. Dashed style for σ-bands and VAH/VAL keeps POC/VWAP/swing
    // anchors visually dominant when picking stops.
    if (PAGE.arnoldOverlay && PAGE.arnoldOverlay.showMembers) {
      for (const m of (p.members_detail || [])) {
        const family = m.family || 'unknown';
        if (SKIP_MEMBER_DRAW_FAMILIES.has(family)) continue;
        const weight = typeof m.weight === 'number' ? m.weight : 0.5;
        const linecolor = COLOR_BY_STRENGTH(weight);
        const linestyle = DASHED_TYPES.has(m.type) ? 1 : 0; // 0=solid, 1=dashed
        const memberKey = `${p.key}:member:${family}:${m.price.toFixed(2)}`;
        try {
          const mid = chart.createMultipointShape(
            [
              { time: tStart, price: m.price },
              { time: tEnd,   price: m.price },
            ],
            {
              shape: 'brush',
              text: '',
              overrides: {
                linecolor,
                linewidth: 1,
                linestyle,
                transparency: 50,
                showLabel: false,
                extendLeft: false,
                extendRight: false,
              },
            }
          );
          if (mid != null) drawn.set(memberKey, mid);
        } catch (e) {
          // Individual member-line failure shouldn't kill the zone draw.
          // Log once via sendError, continue with other members.
          sendError(`drawZone member failed (${m.name}): ${e instanceof Error ? e.message : String(e)}`);
        }
      }
    }
    return true;
  }

  // Per-position registry. Each trade gets a single TV long_position /
  // short_position shape anchored at the real entry→exit window the
  // broadcaster sends. Mutate-in-place via setPoints / setProperties so
  // trail-stop updates on the active trade don't flicker.
  const drawnPositions = new Map(); // key → shapeId
  // Active trade caches its entry-time anchor so stop/tp updates don't
  // scrub the entry handle each tick. Closed trades sync to broker
  // timestamps every render.
  const positionAnchors = new Map();

  function drawPosition(p) {
    if (!chart) return false;
    const isLong = p.side === 'long';
    const isActive = (p.key === 'trade:active' || p.key === 'pos:current');

    const fillEpoch = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;
    const now = Math.floor(Date.now() / 1000);
    let anchor;
    if (isActive) {
      if (!positionAnchors.has(p.key)) positionAnchors.set(p.key, fillEpoch != null ? fillEpoch : now);
      anchor = positionAnchors.get(p.key);
    } else {
      anchor = fillEpoch != null ? fillEpoch : now;
    }
    let endEpoch = (typeof p.end_time === 'number') ? Math.floor(p.end_time) : null;
    if (endEpoch == null || endEpoch <= anchor) endEpoch = anchor + 60;

    const shapeName = isLong ? 'long_position' : 'short_position';
    const NQ_TICK = 0.25;
    const stopPrice = (p.stop != null && Number(p.stop) > 0) ? Number(p.stop) : (isLong ? p.entry - 1 : p.entry + 1);
    const tpPrice   = (p.tp   != null && Number(p.tp)   > 0) ? Number(p.tp)   : (isLong ? p.entry + 1 : p.entry - 1);
    const stopOffsetTicks = Math.round(Math.abs(stopPrice - p.entry) / NQ_TICK);
    const tpOffsetTicks   = Math.round(Math.abs(tpPrice   - p.entry) / NQ_TICK);
    const points = [
      { time: anchor, price: p.entry },
      { time: endEpoch, price: p.entry },
    ];
    const positionOverrides = {
      stopLevel: stopOffsetTicks,
      profitLevel: tpOffsetTicks,
      showPriceLabels: false,
    };

    const existingId = drawnPositions.get(p.key);
    if (existingId != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existingId);
        if (obj) {
          if (typeof obj.setPoints === 'function') obj.setPoints(points);
          if (typeof obj.setProperties === 'function') obj.setProperties(positionOverrides);
          return true;
        }
      } catch (_) { /* fall through to recreate */ }
    }

    try {
      const shapeId = chart.createMultipointShape(points, {
        shape: shapeName,
        overrides: positionOverrides,
      });
      if (shapeId == null) {
        sendError(`drawPosition: createMultipointShape returned null for ${shapeName}`);
        return false;
      }
      if (existingId != null && existingId !== shapeId) {
        try { chart.removeEntity(existingId); } catch (_) {}
      }
      drawnPositions.set(p.key, shapeId);
      return true;
    } catch (e) {
      sendError(`drawPosition failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  function removePosition(key) {
    const shapeId = drawnPositions.get(key);
    drawnPositions.delete(key);
    positionAnchors.delete(key);
    if (shapeId != null && chart) {
      try { chart.removeEntity(shapeId); } catch (_) {}
    }
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
      try { ws.send(JSON.stringify({ type: 'hello', version: '0.2.0', href: PAGE.location.href })); } catch (_) {}
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      switch (msg.type) {
        case 'zone_upsert':     if (drawZone(msg)) sendAck(1); break;
        case 'zone_remove':     safeRemove(msg.key); safeRemovePrefix(`${msg.key}:member:`); sendAck(1); break;
        case 'position_upsert': if (drawPosition(msg)) sendAck(1); break;
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
