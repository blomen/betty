# Arnold TradingView Overlay — Chrome Extension

One-click alternative to the Tampermonkey userscript. Loads as an
unpacked extension in Chrome.

## Why use this instead of the userscript?

- No Tampermonkey dependency — pure Chrome MV3 extension.
- The CSP issue is solved at the architecture level: the WebSocket runs
  in the extension's isolated world (privileged origin, bypasses page
  CSP), and the chart drawing runs in the main world (access to
  `window.TradingViewApi`). The two halves communicate via custom DOM
  events.

## Install

1. Open Chrome → `chrome://extensions/`
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked**
4. Point at this directory: `arnold/tv_overlay/extension/`
5. Open any TradingView chart at `https://*.tradingview.com/*`

That's it. The extension auto-connects to `ws://127.0.0.1:8000/stocks/ws/tv-overlay`
(the local arnold.bat server) and starts drawing zones / open positions.

Verify with:

```
curl http://127.0.0.1:8000/stocks/api/tv-overlay/status
```

`attached_clients` should be ≥ 1 once a TV tab is open.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest, content_scripts wiring |
| `bridge.js` | Isolated world — owns the WebSocket |
| `page.js` | Main world — owns chart access + drawing |

## Userscript alternative

The Tampermonkey userscript at `../userscript/arnold-overlay.user.js`
provides the same functionality. Use whichever fits your workflow.
