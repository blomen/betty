// ==UserScript==
// @name         Arnold TradingView Overlay
// @namespace    https://github.com/blomen/arnold
// @version      0.8.1
// @description  Active trade widget now extends 5 minutes into the future (visual cue that it's still open). Trail-stop line hard-gated to active trades only — closed trades get an explicit _removeTrailStopLine on every redraw as belt-and-suspenders. Unified TV widget + daily scope from 0.8.0 preserved.
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

    // Hide low-strength single-family zones — see page.js for rationale.
    // Kept in sync between extension and userscript so the chart looks
    // identical regardless of which renderer is active. Server still emits
    // every zone; the DQN observation is unaffected. The safeRemove +
    // safeRemovePrefix at the top of drawZone already cleared any prior
    // shape for this key, so we just return without redrawing.
    const ZONE_PAINT_MIN_STRENGTH = 0.5;
    if (!hasSwing && Number(p.strength) < ZONE_PAINT_MIN_STRENGTH) {
      return false;
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

  // Per-position registry. Active trade gets TV's long_position widget
  // (live R:R bands, Open P&L); closed trades get a simple time-bounded
  // rectangle (no auto-drawn stop/tp bands — those stack into visual soup
  // when 8+ trades cluster in a tight window). Tracking shape kind so
  // mutate-in-place doesn't try to setProperties across primitive types.
  const drawnPositions = new Map(); // key → { shapeId, kind }
  // Active trade caches its entry-time anchor so stop/tp updates don't
  // scrub the entry handle each tick. Closed trades sync to broker
  // timestamps every render.
  const positionAnchors = new Map();
  // Every closed-trade payload received from the WS, keyed by payload.key.
  // Never evicted by drawPosition — entries persist across visible-range
  // changes so reconcileClosedTradeVisibility can redraw a previously-
  // undrawn trade when the user scrolls to its time window.
  const closedTradePayloads = new Map(); // key → payload
  // Per-active-position trail-stop line. The long_position widget's
  // built-in stop band is fixed at draw time and does NOT re-flow when
  // the broker trails the stop, so we paint an explicit horizontal_line
  // on top that updates each position_upsert tick. Keyed by position key.
  // Only drawn in Phase 2 — Phase 1 is sacred and the widget's stop band
  // alone communicates the locked conf-based stop.
  const trailStopShapes = new Map(); // key → shapeId
  // Phase 1 sacred snapshot — once a Phase 1 trade is drawn, freeze its
  // stop / profit offsets so live mutations to p.stop / p.tp don't redraw
  // the widget. Keyed by position key; auto-invalidated when entry_time
  // changes (which happens on every fresh entry, including FLIP re-entries
  // that re-use the same active WS key).
  const phase1Snapshots = new Map(); // key → { entryTime, stopOffsetTicks, profitOffsetTicks }

  // True iff the closed trade's [entry_time, end_time] window overlaps
  // the chart's visible range. Both ranges are epoch seconds. Returns
  // true if the range is unavailable (fail-open during early boot before
  // chart.onChartReady fires).
  function _closedTradeOverlapsRange(payload, range) {
    if (!range || range.from == null || range.to == null) return true;
    const start = Number(payload.entry_time);
    const end = Number(payload.end_time);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
    return !(end < range.from || start > range.to);
  }

  function _currentVisibleRange() {
    if (!chart || typeof chart.getVisibleRange !== 'function') return null;
    try { return chart.getVisibleRange(); } catch (_) { return null; }
  }

  // Walk the closed-trade buffer and bring drawn shapes in sync with the
  // current visible range: draw newly-overlapping ones, remove newly-off-
  // range ones. Active trades are untouched (their shape lives in
  // drawnPositions under key 'trade:active' but isn't in
  // closedTradePayloads, so this loop never sees them).
  function reconcileClosedTradeVisibility() {
    if (!chart) return;
    const range = _currentVisibleRange();
    for (const [key, p] of closedTradePayloads) {
      const overlap = _closedTradeOverlapsRange(p, range);
      const isDrawn = drawnPositions.has(key);
      if (overlap && !isDrawn) {
        const fillEpoch = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;
        const now = Math.floor(Date.now() / 1000);
        const anchor = fillEpoch != null ? fillEpoch : now;
        let endEpoch = (typeof p.end_time === 'number') ? Math.floor(p.end_time) : null;
        if (endEpoch == null || endEpoch <= anchor) endEpoch = anchor + 60;
        _drawWidget(p, anchor, endEpoch, false);
      } else if (!overlap && isDrawn) {
        const existing = drawnPositions.get(key);
        if (existing && existing.shapeId != null) {
          try { chart.removeEntity(existing.shapeId); } catch (_) {}
        }
        drawnPositions.delete(key);
        positionAnchors.delete(key);
      }
    }
  }

  // Trailing-edge debounce — coalesces drag/scroll/zoom storms into one
  // reconcile pass. 200 ms keeps the chart responsive without spamming
  // shape redraws.
  let _rangeChangeTimer = null;
  function _scheduleReconcile() {
    if (_rangeChangeTimer != null) clearTimeout(_rangeChangeTimer);
    _rangeChangeTimer = setTimeout(() => {
      _rangeChangeTimer = null;
      try { reconcileClosedTradeVisibility(); }
      catch (e) { sendError(`reconcileClosedTradeVisibility failed: ${e instanceof Error ? e.message : String(e)}`); }
    }, 200);
  }

  function drawPosition(p) {
    if (!chart) return false;
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

    if (isActive) return _drawWidget(p, anchor, endEpoch, true);

    // Closed trade — always buffer, then draw only if it overlaps the
    // current visible range. Off-range trades stay in the buffer so the
    // range-change reconciler can draw them when scrolled into view.
    closedTradePayloads.set(p.key, p);
    const range = _currentVisibleRange();
    if (_closedTradeOverlapsRange(p, range)) {
      return _drawWidget(p, anchor, endEpoch, false);
    }
    // Out of range: ensure no stale shape lingers from a prior in-range emit.
    // Inline the shape-only cleanup — do NOT call removePosition here (that
    // would also clear closedTradePayloads, which we explicitly want to keep
    // so the range-change reconciler can redraw this trade later).
    const existing = drawnPositions.get(p.key);
    if (existing && existing.shapeId != null) {
      try { chart.removeEntity(existing.shapeId); } catch (_) {}
      drawnPositions.delete(p.key);
      positionAnchors.delete(p.key);
    }
    return true; // accepted (buffered), so the WS sender still gets an ack.
  }

  function _drawWidget(p, anchor, endEpoch, isLive) {
    const isLong = p.side === 'long';
    const shapeName = isLong ? 'long_position' : 'short_position';
    const NQ_TICK = 0.25;
    const phase = Number(p.phase) || 0;
    const entryTime = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;

    // Phase 1 sacred: lock the widget at the broker's original stop + TP
    // captured on the first tick of the trade. Both are frozen for the
    // life of the Phase 1 entry even if p.stop / p.tp values change. The
    // broker places TP at 2R (broker_adapter._execute_entry: price + 2 ×
    // stop_offset), pre-slippage — using broker's actual p.tp ensures the
    // chart matches the order book even when fill slippage shifts the
    // R-ratio off exactly 2.0.
    // 2R (not 1.5R) so Phase 2 has room above the BE-lock trigger (peak_R
    // = 1.5) to actually run before TP fires — 1.5R TP would race the
    // BE-lock and the trail architecture never activates.
    // Phase 2 (or unknown): follow live broker values so the widget evolves
    // with the trail / TP moves.
    let stopOffsetTicks;
    let tpOffsetTicks;
    // trailStopPrice is the price of the explicit horizontal red line drawn
    // ON TOP of the widget — Phase 2 only. Null in Phase 1 (line removed if
    // it was there from a previous Phase 2 trade under the same key).
    let trailStopPrice = null;
    if (phase === 1) {
      const cached = phase1Snapshots.get(p.key);
      if (cached && cached.entryTime === entryTime) {
        stopOffsetTicks = cached.stopOffsetTicks;
        tpOffsetTicks = cached.profitOffsetTicks;
      } else {
        const origStop = (typeof p.original_stop_price === 'number' && p.original_stop_price > 0)
          ? Number(p.original_stop_price) : null;
        const baseStop = (origStop != null)
          ? origStop
          : ((typeof p.stop === 'number' && p.stop > 0) ? Number(p.stop) : (isLong ? p.entry - 1 : p.entry + 1));
        stopOffsetTicks = Math.max(1, Math.round(Math.abs(baseStop - p.entry) / NQ_TICK));
        // Use broker's actual tp_price when present so the widget matches
        // the live order book (slippage between signal and fill can push
        // the R-ratio off exactly 2.0 — we trust broker over the formula).
        // Fallback to synthetic 2R only if broker hasn't sent a TP yet.
        const liveTp = (typeof p.tp === 'number' && Number(p.tp) > 0) ? Number(p.tp) : null;
        if (liveTp != null) {
          tpOffsetTicks = Math.max(1, Math.round(Math.abs(liveTp - p.entry) / NQ_TICK));
        } else {
          tpOffsetTicks = Math.max(1, Math.round(stopOffsetTicks * 2));
        }
        phase1Snapshots.set(p.key, { entryTime, stopOffsetTicks, profitOffsetTicks: tpOffsetTicks });
      }
    } else {
      const stopPrice = (p.stop != null && Number(p.stop) > 0) ? Number(p.stop) : (isLong ? p.entry - 1 : p.entry + 1);
      const tpPrice   = (p.tp   != null && Number(p.tp)   > 0) ? Number(p.tp)   : (isLong ? p.entry + 1 : p.entry - 1);
      stopOffsetTicks = Math.max(1, Math.round(Math.abs(stopPrice - p.entry) / NQ_TICK));
      tpOffsetTicks   = Math.max(1, Math.round(Math.abs(tpPrice   - p.entry) / NQ_TICK));
      // Trail line: live Phase 2 only. Closed trades use the widget's own
      // stop band, frozen at the close-time value — no separate horizontal
      // line needed since nothing is going to move after exit.
      if (isLive) trailStopPrice = stopPrice;
    }

    // Hard guarantee: closed trades NEVER carry a trail-stop line. Defensive
    // belt-and-suspenders on top of the Phase 2 `if (isLive)` gate above —
    // ensures any stale line from a previous active-state of the same key
    // (or from an earlier userscript version) is cleared on close.
    if (!isLive) {
      _removeTrailStopLine(p.key);
      trailStopPrice = null;
    }

    const points = [
      { time: anchor, price: p.entry },
      { time: endEpoch, price: p.entry },
    ];
    const positionOverrides = {
      stopLevel: stopOffsetTicks,
      profitLevel: tpOffsetTicks,
      showPriceLabels: false,
    };

    const existing = drawnPositions.get(p.key);
    if (existing && existing.shapeId != null && existing.kind === 'long_position' && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existing.shapeId);
        if (obj) {
          if (typeof obj.setPoints === 'function') obj.setPoints(points);
          if (typeof obj.setProperties === 'function') obj.setProperties(positionOverrides);
          _updateTrailStopLine(p.key, trailStopPrice);
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
        sendError(`_drawWidget: createMultipointShape returned null for ${shapeName}`);
        return false;
      }
      if (existing && existing.shapeId != null && existing.shapeId !== shapeId) {
        try { chart.removeEntity(existing.shapeId); } catch (_) {}
      }
      drawnPositions.set(p.key, { shapeId, kind: 'long_position' });
      _updateTrailStopLine(p.key, trailStopPrice);
      return true;
    } catch (e) {
      sendError(`_drawWidget failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  // Re-anchored after the mutate-in-place branch returns true too — call it
  // there as well so a stop trail without a full recreate still moves the line.
  function _updateTrailStopLine(key, stopPrice) {
    if (!chart) return;
    if (stopPrice == null || !Number.isFinite(stopPrice) || stopPrice <= 0) {
      _removeTrailStopLine(key);
      return;
    }
    const tNow = Math.floor(Date.now() / 1000);
    const existing = trailStopShapes.get(key);
    if (existing != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existing);
        if (obj && typeof obj.setPoints === 'function') {
          obj.setPoints([{ time: tNow, price: stopPrice }]);
          return;
        }
      } catch (_) { /* fall through to recreate */ }
    }
    try {
      const shapeId = chart.createMultipointShape(
        [{ time: tNow, price: stopPrice }],
        {
          shape: 'horizontal_line',
          overrides: {
            linecolor: '#ef4444',
            linewidth: 2,
            linestyle: 0,
            showPrice: true,
            showLabel: true,
            textcolor: '#ef4444',
            horzLabelsAlign: 'right',
            vertLabelsAlign: 'middle',
          },
        },
      );
      if (shapeId != null) {
        if (existing != null && existing !== shapeId) {
          try { chart.removeEntity(existing); } catch (_) {}
        }
        trailStopShapes.set(key, shapeId);
      }
    } catch (e) {
      sendError(`_updateTrailStopLine failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function _removeTrailStopLine(key) {
    const shapeId = trailStopShapes.get(key);
    trailStopShapes.delete(key);
    if (shapeId != null && chart) {
      try { chart.removeEntity(shapeId); } catch (_) {}
    }
  }

  function removePosition(key) {
    const entry = drawnPositions.get(key);
    drawnPositions.delete(key);
    positionAnchors.delete(key);
    closedTradePayloads.delete(key);
    phase1Snapshots.delete(key);
    _removeTrailStopLine(key);
    const shapeId = entry && entry.shapeId;
    if (shapeId != null && chart) {
      try { chart.removeEntity(shapeId); } catch (_) {}
    }
  }

  // --- Standalone level rendering (level_upsert / level_remove) ---
  // Mirrors arnold/tv_overlay/extension/page.js applyLevel. The userscript
  // path was missing this handler — server emits level_upsert for swings +
  // FVG/OB but the messages were silently dropped on the userscript path,
  // leaving the chart with zone shapes only. Swings in particular need a
  // chart-spanning horizontal line so structural pivots stay visible when
  // scrolled away from the zone's time window.
  const drawnLevels = new Map();
  const _LEVEL_META = {
    // Swings — chart-spanning horizontal lines, tier-graded amber→red.
    daily_swing_high:   { color: '#fbbf24', shape: 'horizontal_line', showPrice: true },
    daily_swing_low:    { color: '#fbbf24', shape: 'horizontal_line', showPrice: true },
    weekly_swing_high:  { color: '#f59e0b', shape: 'horizontal_line', showPrice: true },
    weekly_swing_low:   { color: '#f59e0b', shape: 'horizontal_line', showPrice: true },
    monthly_swing_high: { color: '#dc2626', shape: 'horizontal_line', showPrice: true },
    monthly_swing_low:  { color: '#dc2626', shape: 'horizontal_line', showPrice: true },
    // FVG / Order Block — translucent rectangles spanning a price range.
    fvg_bullish:         { color: '#34d399', shape: 'rectangle' },
    fvg_bearish:         { color: '#f87171', shape: 'rectangle' },
    order_block_bullish: { color: '#34d399', shape: 'rectangle' },
    order_block_bearish: { color: '#f87171', shape: 'rectangle' },
  };

  function applyLevel(p) {
    if (!chart) return false;
    const meta = _LEVEL_META[p.name];
    if (!meta) return false;  // unknown / skipped level type — silently ignore

    const now = Math.floor(Date.now() / 1000);
    const tStart = now - 6 * 3600;
    const tEnd = now + 6 * 3600;

    const existing = drawnLevels.get(p.key);
    if (existing && existing.shapeId != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existing.shapeId);
        if (obj && typeof obj.setPoints === 'function') {
          if (meta.shape === 'rectangle' && p.top != null && p.bottom != null) {
            obj.setPoints([{ time: tStart, price: p.top }, { time: tEnd, price: p.bottom }]);
          } else {
            obj.setPoints([{ time: now, price: p.price }]);
          }
          return true;
        }
      } catch (_) { /* fall through to recreate */ }
    }

    try {
      let id = null;
      if (meta.shape === 'rectangle' && p.top != null && p.bottom != null) {
        id = chart.createMultipointShape(
          [{ time: tStart, price: p.top }, { time: tEnd, price: p.bottom }],
          {
            shape: 'rectangle',
            disableSave: true,
            overrides: { color: meta.color, backgroundColor: meta.color, transparency: 92, linewidth: 1 },
          }
        );
      } else {
        id = chart.createMultipointShape(
          [{ time: now, price: p.price }],
          {
            shape: meta.shape,
            disableSave: true,
            overrides: { linecolor: meta.color, linewidth: 1, showPrice: !!meta.showPrice, showLabel: false },
          }
        );
      }
      if (id == null) return false;
      drawnLevels.set(p.key, { shapeId: id });
      return true;
    } catch (e) {
      sendError(`level draw failed for ${p.name}: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  function removeLevel(key) {
    const entry = drawnLevels.get(key);
    if (!entry) return;
    drawnLevels.delete(key);
    if (entry.shapeId != null && chart) {
      try { chart.removeEntity(entry.shapeId); } catch (_) {}
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
        case 'level_upsert':    if (applyLevel(msg)) sendAck(1); break;
        case 'level_remove':    removeLevel(msg.key); sendAck(1); break;
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
    // Reconcile closed-trade visibility on scroll/zoom. Wrapped in
    // try/catch in case onVisibleRangeChanged is unavailable on this TV
    // build — we still get correct first-paint behavior from the
    // drawPosition filter, just no auto-redraw on scroll.
    try {
      if (typeof c.onVisibleRangeChanged === 'function') {
        c.onVisibleRangeChanged().subscribe(null, _scheduleReconcile);
      } else {
        console.warn('[arnold-overlay] chart.onVisibleRangeChanged unavailable — closed-trade auto-redraw disabled');
      }
    } catch (e) {
      console.warn('[arnold-overlay] visible-range subscribe failed:', e);
    }
    connect();
  });
})();
