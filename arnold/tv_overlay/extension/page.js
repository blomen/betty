// Main world — runs in page context, has access to window.TradingViewApi.
// Receives server messages from bridge.js (isolated world) via custom events
// and translates them to chart.createMultipointShape / removeEntity calls.

(function () {
  'use strict';

  const ATTACH_POLL_MS = 1000;
  const ATTACH_MAX_TRIES = 60;

  // Continuous HSL heatmap: hue rotates blue (cold/weak) → red (hot/strong),
  // saturation + lightness scale with strength so weak zones look washed-out
  // and strong zones glow. TV's `transparency` override (0=opaque, 100=fully
  // transparent) controls alpha — we want strong zones bold (~25% transparent)
  // and weak zones nearly invisible (~85% transparent) so the chart stays
  // readable when many zones overlap.
  function _hslToHex(h, s, l) {
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = l - c / 2;
    let r = 0, g = 0, b = 0;
    if (h < 60)       { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else              { r = c; b = x; }
    const toHex = v => Math.round((v + m) * 255).toString(16).padStart(2, '0');
    return '#' + toHex(r) + toHex(g) + toHex(b);
  }
  function strengthStyle(s) {
    const clamped = Math.max(0, Math.min(1, s));
    // 240° (blue) → 0° (red) as strength rises
    const hue = 240 - 240 * clamped;
    const sat = 0.6 + 0.4 * clamped;
    const lit = 0.55 - 0.1 * clamped;
    const color = _hslToHex(hue, sat, lit);
    // strength² for transparency so the curve emphasizes high-confidence
    // zones. Range: weak ≈ 95% transparent (barely visible), strong ≈ 60%
    // transparent (clearly visible but chart underneath stays readable).
    const transparency = Math.round(95 - 35 * clamped * clamped);
    return { color, transparency };
  }

  // Persist first-seen timestamps per zone key so the rectangle's left edge
  // tracks when the zone first appeared on this client (rather than being
  // anchored at "now - 8h" for every zone, which made the chart look like
  // a stack of bars all ending at the same point in time).
  const zoneFirstSeenAt = new Map();

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

  async function safeRemove(key) {
    const entityId = await _resolve(drawn.get(key));
    drawn.delete(key);
    zoneFirstSeenAt.delete(key);
    if (entityId == null || !chart) return;
    try { chart.removeEntity(entityId); } catch (_) {}
  }

  // Recognizable text marker on every shape we draw so cleanupStaleShapes
  // can find and remove them on extension reload, without touching the
  // user's manual drawings (LuxAlgo session boxes, hand-drawn levels, etc).
  const ARNOLD_TAG = '​[arn]';  // zero-width-space + tag, basically invisible

  // chart.createMultipointShape and createStudy can return either a sync
  // entity-id or a Promise<entity-id> depending on TV build. Always resolve
  // before storing so removeEntity gets the real id, not "[object Promise]".
  async function _resolve(maybePromise) {
    if (maybePromise && typeof maybePromise.then === 'function') {
      try { return await maybePromise; } catch (_) { return null; }
    }
    return maybePromise;
  }

  async function drawZone(p) {
    if (!chart) return false;
    await safeRemove(p.key);
    const now = Math.floor(Date.now() / 1000);
    if (!zoneFirstSeenAt.has(p.key)) zoneFirstSeenAt.set(p.key, now);
    const tStart = zoneFirstSeenAt.get(p.key);
    // Project 24h forward so zones look like ongoing levels instead of
    // time-bounded boxes that all end at "now".
    const tEnd = now + 24 * 60 * 60;
    const { color, transparency } = strengthStyle(p.strength);
    // Only label confluence zones (members ≥ 2) — labelling every "zone ×1"
    // creates the clutter the user complained about.
    const showLabel = (p.members ?? 0) >= 2;
    const labelText = showLabel ? `${p.kind} ×${p.members}` : '';
    try {
      const id = await _resolve(chart.createMultipointShape(
        [
          { time: tStart, price: p.top },
          { time: tEnd,   price: p.bottom },
        ],
        {
          shape: 'rectangle',
          // Tag every shape so cleanup can find them, even when label is empty
          text: ARNOLD_TAG + labelText,
          // disableSave so TV doesn't persist these shapes in the chart's
          // saved state — when extension reloads, no leftover orphans
          disableSave: true,
          overrides: {
            color,
            backgroundColor: color,
            transparency,
            showLabel,
          },
        }
      ));
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

  // Cleanup any stale arnold-tagged or legacy shapes left over from previous
  // extension sessions. TV persists shapes in chart-saved-state by default
  // and our older code didn't set disableSave or include a marker, so anything
  // drawn before this fix needs aggressive sweeping.
  //
  // Strategy: remove any rectangle/horizontal_line/text shape whose:
  //   (a) text contains ARNOLD_TAG (current shapes — should be empty since
  //       disableSave is on, but covers in-session redraws), OR
  //   (b) text matches a legacy arnold pattern (zone ×N, arnold-*, bare
  //       "entry"/"stop"/"tp" labels from Phase 0 probes, etc.), OR
  //   (c) text is empty AND shape is a rectangle/horizontal_line that could
  //       only be ours (LuxAlgo session boxes always have a session-name
  //       text like "Tokyo"/"London"/"New York" — confirmed by user
  //       screenshot — so empty text on these shape types is a strong
  //       arnold signal).
  //
  // Manual drawings with text (entry lines, custom levels) are preserved
  // because they have non-empty user-typed text that doesn't match (a) or (b).
  async function cleanupStaleShapes() {
    if (!chart || typeof chart.getAllShapes !== 'function') return;
    let shapes;
    try { shapes = chart.getAllShapes(); } catch (_) { return; }
    if (!Array.isArray(shapes)) return;

    const isOurs = (text, name) => {
      if (text && text.includes(ARNOLD_TAG)) return true;
      if (text) {
        const t = String(text).trim();
        if (/×\d+/.test(t)) return true;                          // zone ×N
        if (/arnold[-_]?(test|probe|overlay)/i.test(t)) return true;
        if (/^(LONG|SHORT)\s+entry/i.test(t)) return true;        // our position label
        if (/^stop(\s|$)/i.test(t)) return true;                  // our stop label
        if (/^tp(\s|$)/i.test(t)) return true;                    // our tp label
        if (/^entry$/i.test(t)) return true;                      // bare label from Phase 0
      } else {
        // Empty text + drawable shape ⇒ almost certainly ours. LuxAlgo
        // session boxes (Tokyo/London/New York) always carry a label, as
        // do user-typed levels. Empty-label rectangles are arnold zones
        // from before the ARNOLD_TAG marker existed.
        if (name === 'rectangle' || name === 'horizontal_line') return true;
      }
      return false;
    };

    let scanned = 0, cleaned = 0;
    for (const s of shapes) {
      scanned += 1;
      try {
        const obj = typeof chart.getShapeById === 'function' ? chart.getShapeById(s.id) : null;
        if (!obj) continue;
        const props = typeof obj.getProperties === 'function' ? obj.getProperties() : null;
        const text = (props && (props.text || props.title)) || '';
        const name = (s && s.name) || (props && props.name) || '';
        if (isOurs(text, name)) {
          try { chart.removeEntity(s.id); cleaned += 1; } catch (_) {}
        }
      } catch (_) { /* skip */ }
    }
    console.log(`[arnold-overlay/page] cleanup: scanned ${scanned} shapes, removed ${cleaned}`);
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
          .setBodyBackgroundColor(sideColor);  // TV applies default alpha
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
              .setBodyBackgroundColor('#dc2626');
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
              .setBodyBackgroundColor('#22c55e');
          } catch (_) {}
          entry.tpOrder = tpOrder;
        } catch (_) {}
      }

      // Anchored Volume Profile rooted at the entry time so volume
      // distribution since entry is visible at a glance. TV deprecated the
      // ordered-array input form ("Passing study inputs as an ordered array
      // is now deprecated"), so use the object form keyed by the input name.
      // The actual key name varies by TV version; pass both common names
      // so at least one anchors correctly.
      try {
        const now = Math.floor(Date.now() / 1000);
        const avpPromise = chart.createStudy(
          'Anchored Volume Profile',
          false,
          false,
          { time: now, anchor_time: now, anchorTime: now },
        );
        entry.avpStudyId = avpPromise;
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

  attachPromise.then(async (c) => {
    if (!c) {
      console.warn('[arnold-overlay/page] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay/page] attached to chart', c);

    // Sweep up shapes left over from previous extension sessions before
    // we start drawing fresh. TV may not have finished loading shapes from
    // the chart's saved state at the moment attachPromise resolves, so we
    // run the sweep twice: once now, and again 4s later to catch anything
    // that loaded after the first pass.
    await cleanupStaleShapes();
    setTimeout(() => { cleanupStaleShapes().catch(() => {}); }, 4000);

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
        case 'force_cleanup': {
          // User-triggered nuke from the SignalsPage button. Clears in-session
          // drawn state and uses chart.removeAllShapes() — the nuclear option
          // that hits shapes loaded from TV's chart-saved-state too (which
          // getAllShapes() doesn't always enumerate). User drawings ARE
          // wiped — the button's UX (orange "Clear chart") signals that.
          for (const id of drawn.values()) {
            const realId = await _resolve(id);
            if (realId != null) {
              try { chart.removeEntity(realId); } catch (_) {}
            }
          }
          drawn.clear();
          zoneFirstSeenAt.clear();
          for (const key of [...drawnPositions.keys()]) {
            await removePosition(key);
          }
          if (typeof chart.removeAllShapes === 'function') {
            try { chart.removeAllShapes(); console.log('[arnold-overlay/page] removeAllShapes() invoked'); } catch (e) {
              console.warn('[arnold-overlay/page] removeAllShapes failed', e);
            }
          }
          await cleanupStaleShapes();
          sendAck(1);
          break;
        }
      }
    });
  });
})();
