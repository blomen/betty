"""Discovery engine — mines JSONL recordings + live DOM to populate intel JSON."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Keywords that identify API endpoint categories
_BALANCE_KEYWORDS = ("/wallet", "/balance", "/account/balance", "/wallets")
_HISTORY_KEYWORDS = ("/bets", "/history", "/coupons", "/bethistory", "/bet-history", "/mybets")
_PLACEMENT_KEYWORDS = ("/place", "/placewidget", "/placebet")

# Domains to ignore (CDNs, tracking, etc.)
_IGNORE_DOMAINS = (
    "google", "facebook", "hotjar", "clarity", "analytics",
    "doubleclick", "cloudflare", "fonts.googleapis",
)


def analyze_recordings(
    provider_id: str,
    recordings_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Mine JSONL recordings for API endpoint patterns.

    Returns: {
        "balance": ["GET /api/wallet/balance", ...],
        "history": ["GET /api/bets/history", ...],
        "placement": ["POST /api/bets/place", ...],
        "other_api": ["GET /api/settings", ...],
    }
    """
    if recordings_dir is None:
        try:
            from ...paths import get_data_dir
            recordings_dir = get_data_dir() / "mirror_recordings"
        except ImportError:
            import os
            recordings_dir = Path(os.environ.get("FIREV_DATA_DIR", str(Path(__file__).parent.parent.parent.parent / "data"))) / "mirror_recordings"

    provider_dir = recordings_dir / provider_id
    if not provider_dir.exists():
        return {"balance": [], "history": [], "placement": [], "other_api": []}

    seen: set[str] = set()
    categorized: dict[str, list[str]] = {"balance": [], "history": [], "placement": [], "other_api": []}

    for jsonl_file in sorted(provider_dir.glob("*.jsonl")):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Skip DOM events
                    if entry.get("type") == "dom":
                        continue

                    url = entry.get("url", "")
                    method = entry.get("method", "GET")
                    resource_type = entry.get("resource_type", "")

                    # Only care about XHR/fetch
                    if resource_type not in ("xhr", "fetch", ""):
                        continue

                    # Skip ignored domains
                    url_lower = url.lower()
                    if any(d in url_lower for d in _IGNORE_DOMAINS):
                        continue

                    # Deduplicate by method + path (strip query params)
                    path = url.split("?")[0]
                    key = f"{method} {path}"
                    if key in seen:
                        continue
                    seen.add(key)

                    # Categorize
                    path_lower = path.lower()
                    entry_str = f"{method} {path}"

                    if any(kw in path_lower for kw in _BALANCE_KEYWORDS):
                        categorized["balance"].append(entry_str)
                    elif any(kw in path_lower for kw in _PLACEMENT_KEYWORDS):
                        categorized["placement"].append(entry_str)
                    elif any(kw in path_lower for kw in _HISTORY_KEYWORDS):
                        categorized["history"].append(entry_str)
                    elif resource_type in ("xhr", "fetch") and "/api/" in path_lower:
                        categorized["other_api"].append(entry_str)

        except OSError as e:
            logger.warning(f"[discovery] Error reading {jsonl_file}: {e}")

    return categorized


def _infer_balance_path(response: dict, depth: int = 0, prefix: str = "") -> str | None:
    """Walk a JSON response to find a field that looks like a balance value."""
    if depth > 5:
        return None

    balance_keys = ("balance", "amount", "cash", "total", "real")

    if isinstance(response, dict):
        for key, val in response.items():
            current_path = f"{prefix}.{key}" if prefix else key
            key_lower = key.lower()

            if key_lower in balance_keys and isinstance(val, (int, float)):
                return current_path

            if isinstance(val, dict):
                result = _infer_balance_path(val, depth + 1, current_path)
                if result:
                    return result

    return None


async def discover_balance_dom(page: "Page") -> dict | None:
    """Scan the current page DOM for balance-like elements in nav/header."""
    result = await page.evaluate("""
        () => {
            const moneyRegex = /\\d[\\d\\s,.]+/;
            const candidates = [];
            const areas = document.querySelectorAll('nav, header, [class*="header"], [class*="nav"], [class*="balance"], [class*="wallet"], [class*="user"]');
            for (const area of areas) {
                const els = area.querySelectorAll('span, div, p, a');
                for (const el of els) {
                    const text = (el.textContent || '').trim();
                    if (moneyRegex.test(text) && text.length < 30) {
                        const hasNumber = /\\d/.test(text);
                        const hasCurrency = /[\\$\\u20ac\\u00a3kr\\sSEK]|\\bkr\\b/i.test(text);
                        if (hasNumber && (hasCurrency || text.match(/^[\\d\\s,.]+(\\s*kr)?$/))) {
                            let selector = el.tagName.toLowerCase();
                            if (el.id) selector = '#' + el.id;
                            else if (el.className && typeof el.className === 'string') {
                                const cls = el.className.trim().split(/\\s+/)[0];
                                if (cls) selector = '.' + cls;
                            }
                            candidates.push({
                                text: text,
                                selector: selector,
                                tag: el.tagName.toLowerCase(),
                                className: (el.className || '').toString().substring(0, 100),
                            });
                        }
                    }
                }
            }
            return candidates.slice(0, 5);
        }
    """)

    if not result:
        return None

    best = None
    for candidate in result:
        if best is None or len(candidate["text"]) < len(best["text"]):
            best = candidate

    if best:
        return {
            "method": "dom",
            "api": None,
            "dom": {
                "selector": best["selector"],
                "regex": r"[\d.,]+",
                "multiplier": 1.0,
            },
        }
    return None


async def discover(
    page: "Page",
    provider_id: str,
    recordings_dir: Path | None = None,
    intel_dir: Path | None = None,
) -> dict:
    """Run full discovery for a provider. Returns intel dict, saves to JSON."""
    from .generic import save_intel

    logger.info(f"[discovery] Starting discovery for {provider_id}")

    # Phase 1: Analyze recordings
    endpoints = analyze_recordings(provider_id, recordings_dir)
    logger.info(f"[discovery] {provider_id} recordings: balance={len(endpoints['balance'])}, "
                f"history={len(endpoints['history'])}, placement={len(endpoints['placement'])}, "
                f"other={len(endpoints['other_api'])}")

    # Phase 2: Discover balance (recordings first, DOM fallback)
    balance_intel = None
    for ep in endpoints.get("balance", []):
        parts = ep.split(" ", 1)
        method = parts[0] if len(parts) > 1 else "GET"
        url = parts[1] if len(parts) > 1 else parts[0]
        if method != "GET":
            continue
        data = await page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch("{url}", {{ method: "GET", credentials: "include" }});
                    if (!resp.ok) return {{ __error: resp.status }};
                    return await resp.json();
                }} catch(e) {{ return {{ __error: e.message }}; }}
            }}
        """)
        if data and "__error" not in (data or {}):
            path = _infer_balance_path(data)
            if path:
                balance_intel = {
                    "method": "api",
                    "api": {"url": url, "path": path, "currency": "SEK"},
                    "dom": None,
                }
                logger.info(f"[discovery] {provider_id} balance: API → {url} path={path}")
                break

    if not balance_intel:
        balance_intel = await discover_balance_dom(page)
        if balance_intel:
            logger.info(f"[discovery] {provider_id} balance: DOM fallback → {balance_intel['dom']['selector']}")

    # Phase 3: History endpoints from recordings
    history_intel = None
    if endpoints["history"]:
        ep = endpoints["history"][0]
        parts = ep.split(" ", 1)
        url = parts[1] if len(parts) > 1 else parts[0]
        history_intel = {
            "method": "api",
            "url": url,
            "api": {
                "endpoint": url,
                "settled_filter": {},
                "open_filter": {},
                "mapping": {
                    "bet_id": "id",
                    "odds": "odds",
                    "stake": "stake",
                    "status": "status",
                    "payout": "payout",
                    "event_name": "event",
                    "status_map": {},
                },
            },
            "dom": None,
        }
        logger.info(f"[discovery] {provider_id} history: API → {url} (mapping needs verification)")

    # Build intel
    now = datetime.now(timezone.utc).isoformat()
    intel = {
        "provider_id": provider_id,
        "platform": "unknown",
        "discovered_at": now,
        "updated_at": now,
        "capabilities": {
            "login": "discovered" if balance_intel else "none",
            "balance": "discovered" if balance_intel else "none",
            "history": "discovered" if history_intel else "none",
            "placement": "none",
        },
        "login": {
            "method": "balance_api" if (balance_intel and balance_intel.get("method") == "api") else "dom",
            "indicator": balance_intel.get("dom") if balance_intel else None,
        } if balance_intel else None,
        "balance": balance_intel,
        "history": history_intel,
        "betslip": None,
        "navigation": None,
        "api_endpoints": endpoints,
        "notes": f"Auto-discovered {now}. History field mapping needs manual verification.",
    }

    save_intel(provider_id, intel, intel_dir)
    logger.info(f"[discovery] {provider_id} discovery complete: {intel['capabilities']}")
    return intel
