"""Phase-0 discovery framework — produces a minimal F17-mode provider config.

Workflow (Phase 0 of the platform rebuild spec, 2026-05-08):
    1. POST /mirror/discover/start/{pid} {known_balance, known_event_id}
       → opens a fresh tab, marks the recording session_start_ts
    2. Operator clicks through: log in, view history, navigate to one event,
       place a small bet
    3. POST /mirror/discover/finish/{pid} → analyzes the JSONL slice from
       session_start_ts onward and produces a candidate ProviderConfig
    4. Operator reviews + commits to data/mirror_intel/{pid}.json

The output config is intentionally small (~30 lines) — F17 means we don't
need DOM matchers, just URL patterns + interceptor keywords. The runner
navigates and the user clicks; the interceptor catches the placement.

This module does NOT replace `arnold/mirror/workflows/discovery.py` (which
still drives the legacy DOM-matcher discovery used by older value-bet
workflows). Once Phase 1 (F17 sweep) lands across all soft workflows, this
module becomes the canonical onboarding tool and the old discovery.py can
retire.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Domains/paths to ignore — pure noise (analytics, CDNs, fonts, tracking)
_IGNORE_DOMAINS = (
    "google",
    "facebook",
    "hotjar",
    "clarity",
    "analytics",
    "doubleclick",
    "cloudflare",
    "fonts.googleapis",
    "googletagmanager",
    "taboola",
    "sardine.ai",
)


def _is_noise(url: str, resource_type: str) -> bool:
    """Filter out static assets, tracking, and irrelevant traffic.

    Keep `document` resource_type — page navigations are exactly what we need
    for `event_url_template` and `home_url` inference. Drop scripts / images /
    fonts / stylesheets and anything from analytics domains.
    """
    if resource_type and resource_type not in ("xhr", "fetch", "websocket", "document", ""):
        return True
    lower = url.lower()
    return any(d in lower for d in _IGNORE_DOMAINS)


def _walk_json_for_value(
    data: Any, target: float, tolerance: float = 0.01, depth: int = 0, prefix: str = ""
) -> str | None:
    """Walk a JSON structure to find a numeric field equal to `target` (within tolerance %)."""
    if depth > 6:
        return None
    if isinstance(data, dict):
        for key, val in data.items():
            current = f"{prefix}.{key}" if prefix else key
            if isinstance(val, (int, float)):
                if target > 0 and abs(val - target) / max(target, 1e-9) <= tolerance:
                    return current
            elif isinstance(val, (dict, list)):
                hit = _walk_json_for_value(val, target, tolerance, depth + 1, current)
                if hit:
                    return hit
    elif isinstance(data, list):
        for i, item in enumerate(data):
            hit = _walk_json_for_value(item, target, tolerance, depth + 1, f"{prefix}[{i}]")
            if hit:
                return hit
    return None


def _looks_like_history_response(body_text: str) -> int:
    """Score how 'history-like' a response body is. Higher = more bet-like fields present."""
    score = 0
    lower = body_text.lower()
    for keyword in ("odds", "stake", "status", "payout", "won", "lost", "bet_id", "betid"):
        if f'"{keyword}"' in lower:
            score += 1
    # Bonus for arrays of objects (bet lists are typically arrays)
    if "[{" in body_text and "}]" in body_text:
        score += 2
    return score


def _looks_like_event_url(url: str, event_id: str) -> bool:
    """Does this URL contain the event_id string in path or query?"""
    return bool(event_id) and event_id in url


def _make_event_url_template(url: str, event_id: str) -> str:
    """Replace the literal event_id with a {event_id} placeholder."""
    return url.replace(event_id, "{event_id}")


def analyze_session(
    jsonl_path: Path,
    known_balance: float,
    known_event_id: str | None = None,
    session_start_ts: str | None = None,
) -> dict[str, Any]:
    """Process a discovery JSONL recording → candidate F17 config.

    Args:
        jsonl_path: path to the recording produced by NetworkRecorder
        known_balance: balance the operator just verified visually (in native currency)
        known_event_id: provider's event_id for the bet they placed (e.g. Altenar's '16373899')
        session_start_ts: ISO timestamp; only entries on/after this are analyzed (skip pre-session noise)

    Returns:
        dict with keys: provider_id (filled by caller), domain, home_url,
        balance_url, balance_json_path, history_url, placement_url_pattern,
        event_url_template, interceptor_keywords, _meta (debug info)
    """
    if not jsonl_path.exists():
        return {"_meta": {"error": "recording_not_found", "path": str(jsonl_path)}}

    entries: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Skip DOM events — not useful for HTTP endpoint discovery
            if entry.get("type") == "dom":
                continue
            ts = entry.get("ts", "")
            if session_start_ts and ts < session_start_ts:
                continue
            url = entry.get("url", "")
            if _is_noise(url, entry.get("resource_type", "")):
                continue
            entries.append(entry)

    config: dict[str, Any] = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "entries_analyzed": len(entries),
        "_meta": {"warnings": []},
    }

    # ------------------------------------------------------------------
    # 1. Domain + home_url — most-frequent host on session GETs
    # ------------------------------------------------------------------
    host_counts: dict[str, int] = {}
    for e in entries:
        u = e.get("url", "")
        host = urlparse(u).hostname or ""
        if host and not any(d in host for d in _IGNORE_DOMAINS):
            host_counts[host] = host_counts.get(host, 0) + 1
    if host_counts:
        # Pick the most common bookmaker-like host (longest match wins ties)
        primary_host = max(host_counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        # Strip api/cdn subdomain for the home URL
        home_host = primary_host
        for prefix in ("api.", "cdn.", "data-api.", "sb2bethistory-gateway-", "sb2betgateway-"):
            if home_host.startswith(prefix):
                home_host = home_host[len(prefix) :]
                break
        config["domain"] = home_host
        # home_url heuristic: most-frequent /sport-y root URL on the host
        home_candidates: dict[str, int] = {}
        for e in entries:
            u = e.get("url", "")
            if home_host not in u:
                continue
            path = urlparse(u).path.rstrip("/")
            # Prefer short paths (root, /sport, /en/sport, /sv/sport)
            if path.count("/") <= 2 and any(t in path.lower() for t in ("/sport", "/en", "/sv", "")):
                base = f"https://{home_host}{path}"
                home_candidates[base] = home_candidates.get(base, 0) + 1
        if home_candidates:
            config["home_url"] = max(home_candidates.items(), key=lambda kv: kv[1])[0]
        else:
            config["home_url"] = f"https://{home_host}/"

    # ------------------------------------------------------------------
    # 2. Balance endpoint — JSON response with the known balance value
    # ------------------------------------------------------------------
    balance_hits: list[tuple[str, str, int]] = []  # (url, json_path, score)
    for e in entries:
        body = e.get("response_body")
        if not body or not isinstance(body, str):
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        path = _walk_json_for_value(data, known_balance, tolerance=0.01)
        if path:
            url = e.get("url", "")
            # Strip query params for the canonical endpoint URL
            canonical = url.split("?")[0]
            balance_hits.append((canonical, path, len(body)))
    if balance_hits:
        # Prefer the hit with shortest body (most-focused endpoint, not a kitchen-sink response)
        best = min(balance_hits, key=lambda h: h[2])
        config["balance_url"] = best[0]
        config["balance_json_path"] = best[1]
    else:
        config["_meta"]["warnings"].append(
            f"balance: no response contained {known_balance} ±1% — verify operator's known_balance"
        )

    # ------------------------------------------------------------------
    # 3. History endpoint — response body has highest bet-like-keyword score
    # ------------------------------------------------------------------
    history_hits: list[tuple[str, int, int]] = []  # (url, score, body_size)
    for e in entries:
        body = e.get("response_body")
        if not body or not isinstance(body, str):
            continue
        score = _looks_like_history_response(body)
        if score >= 3:  # threshold tuned to require multiple bet-shaped fields
            url = e.get("url", "").split("?")[0]
            history_hits.append((url, score, len(body)))
    if history_hits:
        # Prefer highest-score; tie-breaker: largest body (more bets recorded)
        history_hits.sort(key=lambda h: (h[1], h[2]), reverse=True)
        config["history_url"] = history_hits[0][0]
    else:
        config["_meta"]["warnings"].append("history: no XHR response had bet-shaped fields")

    # ------------------------------------------------------------------
    # 4. Placement endpoint — last POST in the session is the placed bet
    # ------------------------------------------------------------------
    placement_hits = [e for e in entries if e.get("method") == "POST" and not _is_noise(e.get("url", ""), "")]
    if placement_hits:
        # The user's bet placement is typically the LAST POST in the session
        # (after navigate, after slip is built). Sort by ts and take the last.
        placement_hits.sort(key=lambda e: e.get("ts", ""))
        last_post = placement_hits[-1]
        post_url = last_post.get("url", "")
        # Extract a keyword fragment for interceptor matching (last path segment)
        path = urlparse(post_url).path
        last_segment = path.rstrip("/").rsplit("/", 1)[-1] if "/" in path else path
        config["placement_url_pattern"] = post_url.split("?")[0]
        config["placement_keyword"] = last_segment.lower() if last_segment else None
    else:
        config["_meta"]["warnings"].append("placement: no POST requests found in session")

    # ------------------------------------------------------------------
    # 5. Event URL template — GET request containing the known_event_id
    # ------------------------------------------------------------------
    if known_event_id:
        event_hits = [e for e in entries if _looks_like_event_url(e.get("url", ""), known_event_id)]
        if event_hits:
            # Prefer the EVENT PAGE URL (what `navigate_to_event` will actually
            # use), not API-detail URLs that happen to contain the event_id.
            # Priority order:
            #   (1) resource_type=document on the bookmaker domain — the actual page nav
            #   (2) any URL on the bookmaker domain
            #   (3) any matching URL (api/cdn fallback — least reliable)
            domain = config.get("domain", "")
            doc_hits = [
                e for e in event_hits if e.get("resource_type") == "document" and domain and domain in e.get("url", "")
            ]
            page_hits = [e for e in event_hits if domain and domain in e.get("url", "")]
            chosen = (doc_hits or page_hits or event_hits)[0]
            url = chosen.get("url", "")
            config["event_url_template"] = _make_event_url_template(url, known_event_id)
        else:
            config["_meta"]["warnings"].append(f"event_url_template: no URL contained event_id={known_event_id}")

    # ------------------------------------------------------------------
    # 6. Interceptor keywords — derived from discovered URLs
    # ------------------------------------------------------------------
    interceptor_keywords: dict[str, list[str]] = {"balance": [], "history": [], "placement": []}
    if config.get("balance_url"):
        path = urlparse(config["balance_url"]).path
        # last 2 segments often unique enough for keyword match
        segs = [s for s in path.split("/") if s]
        if segs:
            interceptor_keywords["balance"].append(segs[-1])
            if len(segs) >= 2:
                interceptor_keywords["balance"].append("/".join(segs[-2:]))
    if config.get("history_url"):
        segs = [s for s in urlparse(config["history_url"]).path.split("/") if s]
        if segs:
            interceptor_keywords["history"].append(segs[-1])
    if config.get("placement_keyword"):
        interceptor_keywords["placement"].append(config["placement_keyword"])
    config["interceptor_keywords"] = interceptor_keywords

    return config


def slice_jsonl_to_session(
    full_path: Path,
    session_start_ts: str,
    out_path: Path | None = None,
) -> Path:
    """Read `full_path`, keep only entries on/after `session_start_ts`, write to `out_path`.

    If out_path is None, writes to {full_path.parent}/_session_{ts}.jsonl. Returns the path.
    """
    if out_path is None:
        out_path = full_path.parent / f"_session_{session_start_ts.replace(':', '-')}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with (
        open(full_path, encoding="utf-8") as src,
        open(out_path, "w", encoding="utf-8") as dst,
    ):
        for line in src:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("ts", "") >= session_start_ts:
                dst.write(line)
                kept += 1
    logger.info(f"[discovery_v2] sliced {full_path.name} → {out_path.name} ({kept} entries)")
    return out_path
