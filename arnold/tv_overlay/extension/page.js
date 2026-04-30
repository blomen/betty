// Main world — runs in page context, has access to window.TradingViewApi.
// Receives server messages from bridge.js (isolated world) via custom events
// and translates them to chart.createMultipointShape / removeEntity calls.

(function () {
  'use strict';

  const ATTACH_POLL_MS = 1000;
  const ATTACH_MAX_TRIES = 60;

  // Global color hierarchy — TradingView's swatch palette, left-to-right:
  // RED → ORANGE → YELLOW → GREEN → TEAL → CYAN → BLUE → INDIGO → PURPLE → PINK.
  // RED = strongest / highest priority; PINK = weakest. Used for both zone
  // strength gradients and VP window hierarchy (monthly > weekly > daily).
  const PALETTE = [
    '#f23645', // 0 RED      — strongest
    '#ff9800', // 1 ORANGE
    '#ffeb3b', // 2 YELLOW
    '#22ab94', // 3 GREEN
    '#009688', // 4 TEAL
    '#00bcd4', // 5 CYAN
    '#2962ff', // 6 BLUE
    '#673ab7', // 7 INDIGO
    '#9c27b0', // 8 PURPLE
    '#e91e63', // 9 PINK     — weakest
  ];

  // Map strength [0..1] → palette index where 1.0 = RED, 0.0 = PINK.
  function _paletteFor(strength) {
    const s = Math.max(0, Math.min(1, strength));
    const idx = Math.round((1 - s) * (PALETTE.length - 1));
    return PALETTE[idx];
  }
  // Hex `#rrggbb` → `rgba(r, g, b, alpha)`. Default alpha 0.4 so even
  // before the highlighter's `transparency` property kicks in, the stroke
  // is already 60% see-through. Final visible opacity = alpha * (1 - transparency/100).
  function _hexToRgba(hex, alpha = 0.4) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return `rgba(120, 120, 120, ${alpha})`;
    return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${alpha})`;
  }

  function strengthStyle(s) {
    const clamped = Math.max(0, Math.min(1, s));
    // Hierarchy from PALETTE: RED=strongest → PINK=weakest.
    const color = _paletteFor(clamped);
    // Truly faint. Range 97-99 — strong zone 3% visible per shape, weak 1%.
    // With 6+ zones stacking at a price band this still sums to ~15-20%
    // effective opacity, so chart underneath stays readable.
    const transparency = Math.round(99 - 2 * clamped * clamped);
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

  // ── Shape groups ──────────────────────────────────────────────────────
  // TV's object-tree groups: lets the user collapse/hide a whole family
  // (zones, active position, closed trades) with one click. Created lazily
  // on first shape add, then `addShapeToGroup` for subsequent shapes.
  // Studies (FRVP) can't be grouped — TV's `canBeGroupped` returns false.
  const _groups = new Map(); // logical-name → tv-group-id
  function _ensureGroupAndAdd(logicalName, shapeId) {
    if (!chart || shapeId == null) return;
    try {
      const gc = chart.shapesGroupController && chart.shapesGroupController();
      const sel = chart.selection && chart.selection();
      if (!gc || !sel) return;
      const existing = _groups.get(logicalName);
      if (existing != null) {
        try { gc.addShapeToGroup(existing, shapeId); } catch (_) {}
        return;
      }
      // First shape for this group → create via selection.
      const priorSel = sel.allSources ? sel.allSources().slice() : [];
      sel.clear();
      sel.add(shapeId);
      const newId = gc.createGroupFromSelection();
      sel.clear();
      // Restore prior selection (best effort) so we don't disrupt the user.
      for (const s of priorSel) { try { sel.add(s); } catch (_) {} }
      if (newId != null) {
        try { gc.setGroupName(newId, logicalName); } catch (_) {}
        _groups.set(logicalName, newId);
      }
    } catch (_) { /* group failure is non-fatal */ }
  }
  // Per-zone-key in-flight lock. Without this, two zone_upserts for the same
  // key can race: both pass safeRemove (drawn[key] still empty), both call
  // createMultipointShape, last writer wins, the other shape leaks. Chaining
  // each op through this map serializes them per-key.
  const zoneOps = new Map();

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

  // Remove every drawn entity whose registry key starts with `prefix`. Used
  // to wipe a zone's per-member brush lines (keyed `${zone.key}:member:...`)
  // before redrawing or on zone_remove.
  async function safeRemovePrefix(prefix) {
    const keys = [];
    for (const k of drawn.keys()) {
      if (k.startsWith(prefix)) keys.push(k);
    }
    for (const k of keys) {
      const id = await _resolve(drawn.get(k));
      drawn.delete(k);
      if (id != null && chart) {
        try { chart.removeEntity(id); } catch (_) {}
      }
    }
  }

  // Every member family paints its own line inside the zone — that's the
  // whole point of the per-member render. The standalone level_upsert
  // rectangles for FVG/OB are a different visualization (distribution
  // boxes far right of price) and shouldn't preempt the in-zone lines.

  // Recognizable text marker on every shape we draw so cleanupStaleShapes
  // can find and remove them on extension reload, without touching the
  // user's manual drawings (LuxAlgo session boxes, hand-drawn levels, etc).
  // Zero-width-space + zero-width-non-joiner — both invisible glyphs. Used as
  // an opaque marker in shape text so cleanupStaleShapes can identify our
  // shapes vs. user drawings, without leaking visible characters into labels.
  // Old code used "[arn]" which was clearly visible — DON'T put readable
  // ASCII in this marker.
  const ARNOLD_TAG = '​‌';

  // chart.createMultipointShape and createStudy can return either a sync
  // entity-id or a Promise<entity-id> depending on TV build. Always resolve
  // before storing so removeEntity gets the real id, not "[object Promise]".
  async function _resolve(maybePromise) {
    if (maybePromise && typeof maybePromise.then === 'function') {
      try { return await maybePromise; } catch (_) { return null; }
    }
    return maybePromise;
  }

  // Mutate-in-place pattern. TV shape objects (returned by getShapeById)
  // expose setPoints + setProperties. Re-creating the shape on every diff
  // makes zones flicker; mutating keeps geometry stable and avoids the
  // safeRemove+create race entirely. Only when a zone is *gone* (server
  // dropped the cluster) do we removeEntity.
  async function drawZone(p) {
    if (!chart) return false;
    const now = Math.floor(Date.now() / 1000);
    // Anchor zones close to current price action: 30min back, 2h forward.
    // Far enough to read the band, close enough to not float far off
    // screen at the right edge. Both anchors update on every upsert via
    // mutate-in-place — broadcaster's diff quantization keeps the upsert
    // rate low so this is cheap.
    const tStart = now - 30 * 60;
    const tEnd = now + 2 * 3600;
    if (!zoneFirstSeenAt.has(p.key)) zoneFirstSeenAt.set(p.key, true);
    const { color, transparency } = strengthStyle(p.strength);

    // Rectangle primitive — Y axis is price-anchored, so zone height
    // stays locked to actual `top`/`bottom` regardless of zoom level.
    // Highlighter (the previous primitive) used pixel width, which shifted
    // apparent height as user zoomed in/out.
    const top = Math.max(p.top, p.bottom);
    const bottom = Math.min(p.top, p.bottom);
    const rectOverrides = {
      color,
      backgroundColor: color,
      transparency,
      // 1px border at full color so the zone outline is always visible
      // even when the fill is 99% transparent. Without this, very faint
      // fills disappear entirely (zones existed in the shape registry
      // but couldn't be seen on chart).
      linewidth: 1,
      showLabel: false,
    };

    // Try mutate-in-place first.
    let rectOk = false;
    const existingId = await _resolve(drawn.get(p.key));
    if (existingId != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existingId);
        if (obj) {
          let mutated = false;
          if (typeof obj.setProperties === 'function') {
            try { obj.setProperties(rectOverrides); mutated = true; } catch (_) {}
          }
          if (typeof obj.setPoints === 'function') {
            try {
              obj.setPoints([
                { time: tStart, price: top },
                { time: tEnd,   price: bottom },
              ]);
              mutated = true;
            } catch (_) {}
          }
          if (mutated) rectOk = true;
        }
      } catch (_) { /* fall through to recreate */ }
    }

    if (!rectOk) {
      // No existing shape (or mutation unavailable) → create a fresh rectangle.
      try {
        const id = await _resolve(chart.createMultipointShape(
          [
            { time: tStart, price: top },
            { time: tEnd,   price: bottom },
          ],
          {
            shape: 'rectangle',
            disableSave: true,
            overrides: rectOverrides,
          }
        ));
        if (id != null) {
          // Replace any prior id we might have under this key (if mutate path
          // failed and we landed here) before storing the fresh one.
          const prior = drawn.get(p.key);
          if (prior != null && prior !== id) {
            try { chart.removeEntity(await _resolve(prior)); } catch (_) {}
          }
          drawn.set(p.key, id);
          _ensureGroupAndAdd('Arnold • Zones', id);
          rectOk = true;
        }
      } catch (e) {
        sendError(`drawZone failed: ${e instanceof Error ? e.message : String(e)}`);
        return false;
      }
    }
    if (!rectOk) return false;

    // Per-member brush lines. Each member draws a 1px brush stroke as a
    // horizontal segment confined to the zone's time window so it visually
    // "lives inside" the rectangle. Color via _paletteFor(weight) — same
    // RED→PINK hierarchy palette as the zone fill, applied to each member's
    // own hierarchy weight, so strong dims (POC, monthly swings ≈ 1.0) burn
    // red and weak bands (VWAP σ3 ≈ 0.3) sit purple inside the same zone.
    // Recreated on every upsert: cheap because broadcaster diff-gating keeps
    // upsert rate low and members are quantized server-side.
    await safeRemovePrefix(`${p.key}:member:`);
    for (const m of (p.members_detail || [])) {
      const family = m.family || 'unknown';
      const weight = typeof m.weight === 'number' ? m.weight : 0.5;
      const linecolor = _paletteFor(weight);
      const memberKey = `${p.key}:member:${family}:${Number(m.price).toFixed(2)}`;
      try {
        const mid = await _resolve(chart.createMultipointShape(
          [
            { time: tStart, price: m.price },
            { time: tEnd,   price: m.price },
          ],
          {
            shape: 'brush',
            disableSave: true,
            overrides: {
              linecolor,
              linewidth: 1,
              showLabel: false,
              extendLeft: false,
              extendRight: false,
            },
          },
        ));
        if (mid != null) {
          drawn.set(memberKey, mid);
          _ensureGroupAndAdd('Arnold • Zones', mid);
        }
      } catch (e) {
        // Per-member failure shouldn't kill the zone draw — skip and continue.
        sendError(`drawZone member failed (${m.name}): ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    return true;
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

    // Live ids we currently own — never sweep these, only stale orphans.
    const liveIds = new Set();
    for (const id of drawn.values()) {
      const real = await _resolve(id);
      if (real != null) liveIds.add(real);
    }
    for (const entry of drawnPositions.values()) {
      if (entry && entry.shapeId != null) liveIds.add(entry.shapeId);
    }
    for (const entry of closedPositions.values()) {
      if (entry && entry.shapeId != null) liveIds.add(entry.shapeId);
    }

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
        if (
          name === 'rectangle' ||
          name === 'horizontal_line' ||
          name === 'horizontal_ray' ||
          name === 'highlighter' ||
          name === 'long_position' ||
          name === 'short_position'
        ) return true;
      }
      return false;
    };

    let scanned = 0, cleaned = 0;
    for (const s of shapes) {
      scanned += 1;
      if (liveIds.has(s.id)) continue;  // never touch our active shapes
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

  // Native TV "Long position" / "Short position" drawing tools (the ones in
  // the Forecasting menu): a single multipoint shape with three price anchors
  // (entry, stop, target). TV renders the green/red R:R box, Open P&L, and
  // Risk/reward ratio labels automatically. Removed via chart.removeEntity
  // like any other shape, so we can reuse `drawn` for them.
  //
  // Anchored Volume Profile attached at entry-time still uses createStudy and
  // lives in a separate registry because removal needs the resolved study id.
  const drawnPositions = new Map(); // key → { shapeKey, avpStudyId }
  const drawnLevels = new Map(); // level key → { shapeId, family }

  // Map a level name → (family, hierarchy color, primitive). Family drives
  // grouping in TV's object tree; primitive determines render type
  // (horizontal_line for static price, horizontal_ray for ongoing
  // session-anchored levels, rectangle for FVG/OB ranges).
  // Mapped against the ACTUAL `type` strings in expanded_session["levels"]
  // (probed via /api/stocks/runtime-diagnostic raw_level_types). Server
  // does NOT currently produce TPO (tpoc/tvah/tval/tibh/tibl), naked_poc,
  // or daily_swing_* — those are missing upstream and need to be added in
  // session expansion before they can render here.
  const _LEVEL_META = {
    // Covered by Anchored VWAP study + 3 FRVPs — skip our redraw.
    vwap: { skip: true }, vwap_sd1: { skip: true }, vwap_sd2: { skip: true }, vwap_sd3: { skip: true },
    poc: { skip: true }, vah: { skip: true }, val: { skip: true },
    // Sessions covered by LuxAlgo Sessions study + TV native — skip.
    pdh: { skip: true }, pdl: { skip: true },
    tokyo_high: { skip: true }, tokyo_low: { skip: true },
    london_high: { skip: true }, london_low: { skip: true },
    ib_high: { skip: true }, ib_low: { skip: true },
    // Swings (server emits as <tf>_high/low, NOT <tf>_swing_<dir>).
    // monthly = RED (strongest), weekly = ORANGE.
    monthly_high: { family: 'Swings', color: PALETTE[0], shape: 'horizontal_line' },
    monthly_low: { family: 'Swings', color: PALETTE[0], shape: 'horizontal_line' },
    weekly_high: { family: 'Swings', color: PALETTE[1], shape: 'horizontal_line' },
    weekly_low: { family: 'Swings', color: PALETTE[1], shape: 'horizontal_line' },
    // SMC ranges — drawn as distribution rectangles far right of price.
    fvg_bullish: { family: 'FVG', color: PALETTE[3], shape: 'rectangle' },
    fvg_bearish: { family: 'FVG', color: PALETTE[0], shape: 'rectangle' },
    order_block_bullish: { family: 'Order Blocks', color: PALETTE[3], shape: 'rectangle' },
    order_block_bearish: { family: 'Order Blocks', color: PALETTE[0], shape: 'rectangle' },
  };

  async function applyLevel(p) {
    if (!chart) return false;
    const meta = _LEVEL_META[p.name];
    if (!meta) return false;
    if (meta.skip) return true;

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
      const linecolor = _hexToRgba(meta.color, 0.6);
      if (meta.shape === 'rectangle' && p.top != null && p.bottom != null) {
        // FVG / Order Block rectangles: filled box bounded by price_high
        // and price_low. Faint fill + thin border so the SMC zone reads
        // as a translucent band the user can spot at any zoom level.
        id = await _resolve(chart.createMultipointShape(
          [{ time: tStart, price: p.top }, { time: tEnd, price: p.bottom }],
          { shape: 'rectangle', disableSave: true,
            overrides: { color: meta.color, backgroundColor: meta.color, transparency: 92, linewidth: 1 } },
        ));
      } else {
        id = await _resolve(chart.createMultipointShape(
          [{ time: now, price: p.price }],
          { shape: meta.shape, disableSave: true,
            overrides: { linecolor, linewidth: 1, showPrice: false, showLabel: false } },
        ));
      }
      if (id == null) return false;
      drawnLevels.set(p.key, { shapeId: id, family: meta.family });
      _ensureGroupAndAdd(`Arnold • ${meta.family}`, id);
      return true;
    } catch (e) {
      sendError(`level draw failed for ${p.name}: ${e.message || e}`);
      return false;
    }
  }

  async function removeLevel(key) {
    const entry = drawnLevels.get(key);
    if (!entry) return;
    drawnLevels.delete(key);
    if (entry.shapeId != null && chart) {
      try { chart.removeEntity(entry.shapeId); } catch (_) {}
    }
  }

  // Persistent native studies we attach once at boot (VWAP, anchored VPs).
  // Keyed by a logical name so we can replace/remove without scanning.
  const drawnStudies = new Map(); // name → studyId (resolved)

  async function ensureStudy(name, studyName, inputs) {
    // Idempotent — if we've already attached this study this session, skip.
    if (drawnStudies.has(name)) return drawnStudies.get(name);
    if (!chart || typeof chart.createStudy !== 'function') return null;
    try {
      const id = await _resolve(chart.createStudy(studyName, false, false, inputs || {}));
      if (id != null) drawnStudies.set(name, id);
      return id;
    } catch (e) {
      sendError(`createStudy(${studyName}) failed: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  }

  async function removeStudy(name) {
    const id = drawnStudies.get(name);
    drawnStudies.delete(name);
    if (id == null || !chart) return;
    try { chart.removeEntity(id); } catch (_) {}
  }

  // Fixed Range Volume Profile per server window (daily/weekly/monthly).
  // Audit (docs/tv-overlay-api-audit.md) confirmed paid plan accepts
  // `Fixed Range Volume Profile` via createStudy and exposes
  // `first_bar_time` / `last_bar_time` (epoch ms) as hidden inputs that
  // setInputValues can drive. Anchored Volume Profile is still
  // interactive-only and rejects createStudy regardless of plan.
  const vpRanges = new Map(); // window → "<start_ms>:<end_ms>" last applied
  async function applyVpAnchor(msg) {
    const win = String(msg.window || '');
    const startMs = Number(msg.start_ms);
    const endMs = Number(msg.end_ms);
    if (!win || !Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
    const studyName = `vp:${win}`;
    const sig = `${startMs}:${endMs}`;
    if (vpRanges.get(win) === sig && drawnStudies.has(studyName)) return true;

    // Reconcile FRVP studies every call — TV's chart-saved-state retains
    // studies across reloads even though our drawnStudies Map starts empty,
    // so prior sessions' FRVPs pile up. Sweep any FRVP NOT in our map.
    if (typeof chart.getAllStudies === 'function') {
      try {
        const all = chart.getAllStudies() || [];
        const ourIds = new Set([...drawnStudies.values()]);
        for (const s of all) {
          if (!s || !/Volume Profile/i.test(String(s.name || ''))) continue;
          if (!ourIds.has(s.id)) {
            try { chart.removeEntity(s.id); } catch (_) {}
          }
        }
      } catch (_) {}
    }

    // If we already have this window's study, mutate its range via
    // setInputValues — much cheaper than removeStudy + createStudy and
    // avoids the visual flicker of TV recomputing from scratch.
    const winColor = ({ monthly: PALETTE[0], weekly: PALETTE[1], daily: PALETTE[2] })[win] || PALETTE[2];
    // Horizlines schema has `color` + `style` + `visible` + `width` +
    // `showPrice` + `showLastValue` only. Bake alpha into the rgba color
    // for fade since there's no `transparency` field. POC at full opacity
    // (primary level); VAH/VAL at 50%. showPrice=false hides the inline
    // price-on-line text; showLastValue=false hides the right-axis price
    // tag. Both needed to remove all price labels.
    const fadedColor = _hexToRgba(winColor, 0.5);
    const overrides = {
      'graphics.horizlines.pocLines.visible': true,
      'graphics.horizlines.pocLines.color': winColor,
      'graphics.horizlines.pocLines.showPrice': false,
      'graphics.horizlines.pocLines.showLastValue': false,
      'graphics.horizlines.pocLines.width': 1,
      'graphics.horizlines.vahLines.visible': true,
      'graphics.horizlines.vahLines.color': fadedColor,
      'graphics.horizlines.vahLines.showPrice': false,
      'graphics.horizlines.vahLines.showLastValue': false,
      'graphics.horizlines.vahLines.width': 1,
      'graphics.horizlines.valLines.visible': true,
      'graphics.horizlines.valLines.color': fadedColor,
      'graphics.horizlines.valLines.showPrice': false,
      'graphics.horizlines.valLines.showLastValue': false,
      'graphics.horizlines.valLines.width': 1,
      // Kill the developing* plot labels too (display: 0 = price-scale only,
      // no last-value bubble on right axis).
      'styles.developingPoc.display': 0,
      'styles.developingVAHigh.display': 0,
      'styles.developingVALow.display': 0,
      'styles.developingPoc.showLastValue': false,
      'styles.developingVAHigh.showLastValue': false,
      'styles.developingVALow.showLastValue': false,
      showLastValue: false,
      showStudyLastValue: false,
      // Histogram visible but compressed.
      'graphics.hhists.histBars2.visible': true,
      'graphics.hhists.histBars2.colors': [winColor, winColor],
      'graphics.hhists.histBars2.percentWidth': 8,
      'graphics.hhists.histBars2.transparencies': [85, 85],
      'graphics.hhists.histBarsVA.visible': true,
      'graphics.hhists.histBarsVA.colors': [winColor, winColor],
      'graphics.hhists.histBarsVA.percentWidth': 8,
      'graphics.hhists.histBarsVA.transparencies': [60, 60],
      'graphics.polygons.histBoxBg.transparency': 100,
      // Hide the FRVP from the chart's status line / arguments header
      // so the top strip stays clean.
      showStudyArguments: false,
      showStudyTitle: false,
    };
    const existingId = drawnStudies.get(studyName);
    if (existingId != null && typeof chart.getStudyById === 'function') {
      try {
        const obj = chart.getStudyById(existingId);
        if (obj && typeof obj.setInputValues === 'function') {
          obj.setInputValues([
            { id: 'first_bar_time', value: startMs },
            { id: 'last_bar_time', value: endMs },
            { id: 'extendToRight', value: true },
          ]);
          // Re-apply every cycle — TV may not preserve overrides across
          // setInputValues calls, and applying is idempotent.
          if (typeof obj.applyOverrides === 'function') {
            try { obj.applyOverrides(overrides); } catch (_) {}
          }
          vpRanges.set(win, sig);
          return true;
        }
      } catch (_) { /* fall through to recreate */ }
    }

    // Otherwise create + initialize.
    await removeStudy(studyName);
    const id = await ensureStudy(studyName, 'Fixed Range Volume Profile', {});
    if (id == null) return false;
    try {
      const obj = chart.getStudyById(id);
      if (obj && typeof obj.setInputValues === 'function') {
        obj.setInputValues([
          { id: 'first_bar_time', value: startMs },
          { id: 'last_bar_time', value: endMs },
          { id: 'extendToRight', value: true },
        ]);
      }
      // Per-window color: monthly=RED, weekly=ORANGE, daily=YELLOW.
      // Histograms HIDDEN — only POC/VAH/VAL lines visible (histBars2
      // paints 30%-of-time-range solid bands which dominate the chart).
      if (obj && typeof obj.applyOverrides === 'function') {
        obj.applyOverrides(overrides);
      }
    } catch (e) {
      sendError(`vp_anchor setInputValues failed: ${e.message || e}`);
    }
    vpRanges.set(win, sig);
    return true;
  }

  // Closed positions live here forever (or until force_cleanup) — the user
  // wants a historical record of every trade that fired, persisting on the
  // chart after the position closes. Unique per close-event so we don't
  // collide on the stable "pos:current" key.
  const closedPositions = new Map(); // closedKey → { shapeId, avpStudyId }
  let _closedCounter = 0;

  // Persistent anchor time for the active position. Captured on first
  // upsert so trail-stop updates don't move the entry-time anchor (the
  // long_position shape stays rooted where the trade actually opened).
  const positionAnchors = new Map(); // key → epoch seconds

  async function drawPosition(p) {
    if (!chart) return false;
    const isLong = p.side === 'long';
    const shapeName = isLong ? 'long_position' : 'short_position';

    // Prefer the broker fill time from the broadcaster (`entry_time`) so the
    // shape's entry handle lands on the actual fill candle, not on the
    // moment the broadcaster first emitted. Fall back to "now" only if the
    // broadcaster didn't supply it (older payload shape).
    const fillEpoch = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;
    const now = Math.floor(Date.now() / 1000);
    if (!positionAnchors.has(p.key)) positionAnchors.set(p.key, fillEpoch ?? now);
    const anchor = positionAnchors.get(p.key);

    // Treat stop=0 as missing — broken server-side tracker.stop_price is
    // 0.0 in autonomous mode and 0 would translate into a wild tick offset.
    const stopPrice = (p.stop != null && Number(p.stop) > 0) ? Number(p.stop) : (isLong ? p.entry - 1 : p.entry + 1);
    const tpPrice   = (p.tp   != null && Number(p.tp)   > 0) ? Number(p.tp)   : (isLong ? p.entry + 1 : p.entry - 1);

    // TV's long_position / short_position shapes take 1 OR 2 points. 2 pts
    // bound the time range. **stopLevel and profitLevel are TICK OFFSETS
    // from entry, not absolute prices.** Probe: passing 27349 to a SHORT
    // at entry 27342.5 placed stop at price 34179.75 (= entry + 27349*0.25).
    // Convert prices → ticks here.
    const NQ_TICK = 0.25;
    const stopOffsetTicks = Math.round(Math.abs(stopPrice - p.entry) / NQ_TICK);
    const tpOffsetTicks   = Math.round(Math.abs(tpPrice   - p.entry) / NQ_TICK);

    // Always emit a 2-point form so TV doesn't auto-extend long_position
    // rightward indefinitely. Floor end_time to entry+60s if it's missing
    // or non-positive (instant-close trades where ts == closed_at).
    let endEpoch = (typeof p.end_time === 'number') ? Math.floor(p.end_time) : null;
    if (endEpoch == null || endEpoch <= anchor) endEpoch = anchor + 60;
    const points = [
      { time: anchor, price: p.entry },
      { time: endEpoch, price: p.entry },
    ];

    const positionOverrides = {
      stopLevel: stopOffsetTicks,
      profitLevel: tpOffsetTicks,
      // Hide the price labels at stop/entry/target — they clutter the chart
      // when many trades are persisted. Auto P&L / R:R label still renders.
      showPriceLabels: false,
    };

    // Mutate-in-place if we already have a live shape for this key.
    const existing = drawnPositions.get(p.key);
    if (existing && existing.shapeId != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existing.shapeId);
        if (obj) {
          let mutated = false;
          if (typeof obj.setPoints === 'function') {
            try {
              obj.setPoints(points);
              mutated = true;
            } catch (_) {}
          }
          if (typeof obj.setProperties === 'function') {
            try {
              obj.setProperties(positionOverrides);
              mutated = true;
            } catch (_) {}
          }
          if (mutated) return true;
        }
      } catch (_) {}
    }

    // No live shape (or mutation unavailable) → create.
    try {
      const shapeId = await _resolve(chart.createMultipointShape(
        points,
        {
          shape: shapeName,
          // disableSave: TRUE — never let TV's chart-saved-state hold these
          // shapes. On reload, drawnPositions resets to empty; saved-state
          // shapes become orphans that drift on the chart with no way to
          // mutate or remove them. The broadcaster's replay_to re-emits
          // every trade to new clients, so closed positions naturally
          // re-appear in their correct spots after reload.
          disableSave: true,
          overrides: positionOverrides,
        },
      ));
      if (shapeId == null) {
        sendError(`drawPosition: createMultipointShape returned null for ${shapeName}`);
        return false;
      }

      // Replace any prior id under this key (if mutate path failed).
      if (existing && existing.shapeId != null && existing.shapeId !== shapeId) {
        try { chart.removeEntity(existing.shapeId); } catch (_) {}
      }

      const entry = { shapeId };
      // Group by lifecycle: synthetic active trade → "Active Trade",
      // historical broker_trade rows (trade:<id>) → "Closed Trades".
      const groupName = (p.key === 'trade:active' || p.key === 'pos:current')
        ? 'Arnold • Active Trade'
        : 'Arnold • Closed Trades';
      _ensureGroupAndAdd(groupName, shapeId);

      // Anchored Volume Profile at entry time was here, but the createStudy
      // call rejects our input format with "Passed color string does not
      // match any of the known color representations". Disabled until we
      // verify the correct input schema for this TV build. Tracked separately
      // from the daily/weekly/monthly AVP broadcaster (also disabled until
      // input format confirmed).

      drawnPositions.set(p.key, entry);
      return true;
    } catch (e) {
      sendError(`drawPosition failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  // Position closed — DON'T preserve the synthetic "trade:active" key
  // (the real broker_trade row is about to take over with a different id;
  // preserving would double-render). For real trade ids, move the shape
  // to the closed registry so it persists on chart.
  async function finalizePosition(key) {
    const entry = drawnPositions.get(key);
    if (!entry) return;
    drawnPositions.delete(key);
    positionAnchors.delete(key);
    if (key === 'trade:active' || key === 'pos:current') {
      // Synthetic / legacy keys — drop the shape; real trade row will redraw it.
      if (entry.shapeId != null && chart) {
        try { chart.removeEntity(entry.shapeId); } catch (_) {}
      }
      try {
        const studyId = await entry.avpStudyId;
        if (studyId != null && chart) try { chart.removeEntity(studyId); } catch (_) {}
      } catch (_) {}
      return;
    }
    const closedKey = `closed:${key}:${++_closedCounter}`;
    closedPositions.set(closedKey, entry);
  }

  // Hard-remove (only used by force_cleanup nuke). Walks both registries.
  async function removePosition(key) {
    const entry = drawnPositions.get(key);
    if (entry) {
      drawnPositions.delete(key);
      positionAnchors.delete(key);
      if (entry.shapeId != null && chart) {
        try { chart.removeEntity(entry.shapeId); } catch (_) {}
      }
      try {
        const studyId = await entry.avpStudyId;
        if (studyId != null && chart) try { chart.removeEntity(studyId); } catch (_) {}
      } catch (_) {}
    }
  }

  async function removeAllClosedPositions() {
    for (const [, entry] of closedPositions) {
      if (entry.shapeId != null && chart) {
        try { chart.removeEntity(entry.shapeId); } catch (_) {}
      }
      try {
        const studyId = await entry.avpStudyId;
        if (studyId != null && chart) try { chart.removeEntity(studyId); } catch (_) {}
      } catch (_) {}
    }
    closedPositions.clear();
  }

  attachPromise.then(async (c) => {
    if (!c) {
      console.warn('[arnold-overlay/page] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay/page] attached to chart', c);

    // Hide study arguments + values in the chart's status-line legend
    // (otherwise every FRVP shows "Number Of Rows 100 Up/Down 70" etc.,
    // stacking 3+ deep in the top-left).
    try {
      chart.applyOverrides({
        'paneProperties.legendProperties.showStudyArguments': false,
        'paneProperties.legendProperties.showStudyValues': false,
      });
    } catch (_) {}

    // Strip right-axis last-value labels off every existing study (VWAP,
    // Volume, LuxAlgo Sessions, RSI, etc.) — the user's preference is a
    // clean axis. FRVPs already get this via the per-window overrides.
    // Skips FRVPs to avoid double-processing.
    try {
      const studies = chart.getAllStudies && chart.getAllStudies() || [];
      for (const s of studies) {
        if (/Fixed Range Volume Profile/i.test(String(s.name || ''))) continue;
        const obj = chart.getStudyById(s.id);
        if (!obj || typeof obj.getStyleValues !== 'function') continue;
        const sv = obj.getStyleValues();
        const styleKeys = Object.keys(sv.styles || {});
        const ov = {};
        for (const k of styleKeys) {
          ov[`styles.${k}.display`] = 0;
          ov[`styles.${k}.showLastValue`] = false;
        }
        try { obj.applyOverrides(ov); } catch (_) {}
      }
    } catch (_) {}

    // Sweep up shapes left over from previous extension sessions before
    // we start drawing fresh. TV may not have finished loading shapes from
    // the chart's saved state at the moment attachPromise resolves, so we
    // run the sweep twice: once now, and again 4s later to catch anything
    // that loaded after the first pass.
    await cleanupStaleShapes();
    setTimeout(() => { cleanupStaleShapes().catch(() => {}); }, 4000);

    // No periodic re-sweep — cleanupStaleShapes is meant to clear orphans
    // from previous sessions. Running it on a timer would race against
    // mutate-in-place drawZone and yank live shapes that haven't yet been
    // re-registered in `drawn`. Boot + 4s post-attach is the full coverage
    // window for state-saved orphans loaded by TV asynchronously.

    // Periodic re-anchor of zone shapes. Broadcaster only emits upserts
    // when strength/top/bottom buckets change, so otherwise tStart/tEnd
    // freeze at first-draw moment and drift left over a long session
    // (zones end up with mixed-length time bounds — some long-tailed,
    // some current). Every 60s, slide all live zones to (now-30m, now+2h)
    // via setPoints; cheap because mutate-in-place doesn't recreate.
    setInterval(async () => {
      if (!chart || typeof chart.getShapeById !== 'function') return;
      const now2 = Math.floor(Date.now() / 1000);
      const tS = now2 - 30 * 60;
      const tE = now2 + 2 * 3600;
      for (const [, idMaybe] of drawn.entries()) {
        try {
          const id = await _resolve(idMaybe);
          if (id == null) continue;
          const obj = chart.getShapeById(id);
          if (!obj || typeof obj.getPoints !== 'function' || typeof obj.setPoints !== 'function') continue;
          const pts = obj.getPoints();
          if (!pts || pts.length < 2) continue;
          // Preserve top/bottom prices; refresh only time anchors.
          // For rectangles, pts[0] is the upper-left corner and pts[1]
          // the lower-right (or whichever order TV stored them in) —
          // either way we keep their prices and just slide time.
          obj.setPoints([
            { time: tS, price: pts[0].price },
            { time: tE, price: pts[1].price },
          ]);
        } catch (_) { /* skip */ }
      }
    }, 60000);

    // Expose internal state for diagnostic eval from /mirror/browser/tv-eval.
    // Stripped after debug stabilizes.
    try {
      window.__arnold_overlay_debug = () => ({
        drawn: Array.from(drawn.entries()).map(([k, v]) => [k, String(v)]),
        drawnPositions: Array.from(drawnPositions.keys()),
        closedPositions: Array.from(closedPositions.keys()),
        positionAnchors: Array.from(positionAnchors.entries()),
        drawnStudies: Array.from(drawnStudies.keys()),
        vpRanges: Array.from(vpRanges.entries()),
        zoneOpsKeys: Array.from(zoneOps.keys()),
        zoneFirstSeenAt: zoneFirstSeenAt.size,
      });
    } catch (_) {}

    // Serialize all ops on a given zone key. Returns the chained tail so the
    // caller can await it.
    function withZoneLock(key, fn) {
      const prev = zoneOps.get(key) || Promise.resolve();
      const next = prev.then(fn, fn);
      zoneOps.set(key, next);
      // Free the slot once nothing newer is queued
      next.finally(() => { if (zoneOps.get(key) === next) zoneOps.delete(key); });
      return next;
    }

    document.addEventListener('arnold:msg', async (ev) => {
      let msg;
      try { msg = JSON.parse(ev.detail); } catch (_) { return; }
      // One-line trace per inbound message — strip after debug
      console.log('[arnold-overlay/page] msg', msg.type, msg.key || msg.window || '');
      switch (msg.type) {
        case 'zone_upsert': {
          const ok = await withZoneLock(msg.key, () => drawZone(msg));
          if (ok) sendAck(1);
          break;
        }
        case 'zone_remove': {
          await withZoneLock(msg.key, async () => {
            await safeRemove(msg.key);
            await safeRemovePrefix(`${msg.key}:member:`);
          });
          sendAck(1);
          break;
        }
        case 'position_upsert': if (await drawPosition(msg)) sendAck(1); break;
        case 'level_upsert':    if (await applyLevel(msg)) sendAck(1); break;
        case 'level_remove':    await removeLevel(msg.key); sendAck(1); break;
        case 'position_remove': await finalizePosition(msg.key); sendAck(1); break;
        case 'vp_anchor':
          if (await applyVpAnchor(msg)) sendAck(1);
          break;
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
          zoneOps.clear();
          drawnLevels.clear();
          _groups.clear();  // group ids are stale once shapes are wiped
          for (const name of [...drawnStudies.keys()]) {
            await removeStudy(name);
          }
          vpRanges.clear();
          for (const key of [...drawnPositions.keys()]) {
            await removePosition(key);
          }
          await removeAllClosedPositions();
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
