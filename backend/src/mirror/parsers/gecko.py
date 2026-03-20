"""Gecko V2 (OBG) bet response parser.

Parses bet placement API responses from Betsson Group sites
(Betsson, Betsafe, NordicBet, Spelklubben).

Real API schema (discovered 2026-03-20):
  Endpoint: POST /api/sb/v2/coupons
  Request:  bets[].stake, bets[].betSelections[].marketSelectionId, bets[].betSelections[].odds
  Response: couponStatus.couponId, couponStatus.couponStatusPollingResult

  marketSelectionId format: "s-m-f-{eventId}-{marketTemplate}-{line}-{outcome}"
  Examples:
    s-m-f-K4Qf1s_QPkeNqhDn68MZsQ-MTG2W-3.5-over     (total over 3.5)
    s-m-f-abc123-MW3W-home                             (1x2 home)
    s-m-f-abc123-M2WHCP-1.5-HANDICAPHOME               (spread home -1.5)
"""

import logging
import json
from typing import Any

logger = logging.getLogger(__name__)

# URL path segments that indicate bet placement
_BET_URL_KEYWORDS = ("coupon", "betslip", "bet/place", "wager")

# Market template ID → standard market type (same mapping as gecko_v2.py extractor)
_MARKET_TEMPLATE_MAP: dict[str, str] = {
    "MW3W": "1x2",
    "MW2W": "moneyline",
    "ESNRTWINNER3W": "1x2",
    "ESNMOWINNER2W": "moneyline",
    "ESMW2W": "moneyline",
    "MTG2W": "total",
    "MTG2W25": "total",
    "TGOU": "total",
    "TGOUOT": "total",
    "MWOU": "total",
    "MROU": "total",
    "ESNMOTOTAL": "total",
    "OUALT": "total",
    "PTSOUROLMID": "total",
    "MTG2WIO": "total",
    "MTG2WP": "total",
    "MTP": "total",
    "M3WHCP": "spread",
    "M2WHCP": "spread",
    "MW2WHCP": "spread",
    "M2WHCPIO": "spread",
    "2WHCPROLMID": "spread",
    "MWHCPALT": "spread",
    "MHCPNOT": "spread",
    "MAHCP": "spread",
    "AHC": "spread",
    "ESNMOHANDICAP": "spread",
    "MSH": "spread",
    "ESHMTHANDICAP": "spread",
}

# Selection suffixes → standard outcome
_OUTCOME_MAP: dict[str, str] = {
    "home": "home",
    "away": "away",
    "draw": "draw",
    "over": "over",
    "under": "under",
    "handicaphome": "home",
    "handicapaway": "away",
    "handicapdraw": "draw",
    "1": "home",
    "2": "away",
    "x": "draw",
}


class GeckoBetParser:
    """Parse Gecko V2 bet placement API responses."""

    def is_bet_placement_url(self, url: str) -> bool:
        """Check if URL is a bet placement endpoint (not odds/events)."""
        lower = url.lower()
        return "/api/sb/" in lower and any(kw in lower for kw in _BET_URL_KEYWORDS)

    def is_rejection(self, response_body: dict) -> bool:
        """Check if response indicates a rejected bet."""
        coupon = response_body.get("couponStatus", {})
        result = coupon.get("couponStatusPollingResult", "").lower()
        if result in ("failed", "rejected", "error", "declined"):
            return True
        errors = coupon.get("couponPlacementErrors", [])
        if errors:
            return True
        # Legacy format fallback
        data = response_body.get("data", {})
        status = data.get("status", "").lower()
        return status in ("rejected", "failed", "error", "declined")

    def parse(self, response_body: dict, request_body: str | None = None) -> dict[str, Any] | None:
        """Parse a confirmed bet from request + response bodies.

        The response only contains the coupon ID. Odds, stake, market, and
        outcome are extracted from the request body.

        Returns dict with bet fields, or None if rejected/unparseable.
        """
        if self.is_rejection(response_body):
            return None

        # Get confirmation ID from response
        coupon = response_body.get("couponStatus", {})
        coupon_id = coupon.get("couponId")
        if not coupon_id:
            # Legacy format fallback
            coupon_id = response_body.get("data", {}).get("betId")
        if not coupon_id:
            logger.warning("No couponId in response — cannot parse")
            return None

        # Parse the request body for bet details
        if not request_body:
            logger.warning(f"No request body for coupon {coupon_id}")
            return None

        try:
            req = json.loads(request_body) if isinstance(request_body, str) else request_body
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON request body for coupon {coupon_id}")
            return None

        bets = req.get("bets", [])
        if not bets:
            logger.warning(f"No bets in request for coupon {coupon_id}")
            return None

        bet = bets[0]
        stake = float(bet.get("stake", 0))

        selections = bet.get("betSelections", [])
        if not selections:
            logger.warning(f"No betSelections for coupon {coupon_id}")
            return None

        sel = selections[0]
        odds = float(sel.get("odds", 0))
        selection_id = sel.get("marketSelectionId", "")

        # Parse marketSelectionId: s-m-f-{eventId}-{template}-{line}-{outcome}
        # or: s-m-f-{eventId}-{template}-{outcome}
        parsed_sel = self._parse_selection_id(selection_id)

        return {
            "confirmation_id": str(coupon_id),
            "odds": odds,
            "stake": stake,
            "market": parsed_sel.get("market"),
            "outcome": parsed_sel.get("outcome"),
            "point": parsed_sel.get("point"),
            "home_team": None,  # Not available in coupon API
            "away_team": None,  # Not available in coupon API
            "event_name": "",
            "gecko_event_id": parsed_sel.get("event_id", ""),
        }

    def _parse_selection_id(self, selection_id: str) -> dict[str, Any]:
        """Parse a marketSelectionId into components.

        Format: s-m-f-{eventId}-{marketTemplate}-{line}-{outcome}
        or:     s-m-f-{eventId}-{marketTemplate}-{outcome}

        Examples:
            s-m-f-K4Qf1s_QPkeNqhDn68MZsQ-MTG2W-3.5-over
            s-m-f-abc123-MW3W-home
        """
        result: dict[str, Any] = {
            "event_id": None,
            "market": None,
            "point": None,
            "outcome": None,
        }

        if not selection_id:
            return result

        parts = selection_id.split("-")
        # Skip prefix parts: s, m, f
        # Find the event ID — it's after 'f' and before the market template
        # Strategy: scan from the end to find known market template, then everything
        # between 'f' and the template is the event ID

        # Find index of 'f' prefix
        try:
            f_idx = parts.index("f")
        except ValueError:
            logger.debug(f"No 'f' prefix in selectionId: {selection_id}")
            return result

        # Everything after 'f' needs to be split into: eventId, template, [line], outcome
        remaining = parts[f_idx + 1:]
        if not remaining:
            return result

        # Scan from the end to find the market template
        template_idx = None
        for i, part in enumerate(remaining):
            if part.upper() in _MARKET_TEMPLATE_MAP:
                template_idx = i
                break

        if template_idx is None:
            # No recognized template — event ID is everything, no market info
            result["event_id"] = "-".join(remaining)
            return result

        # Event ID = parts before template (may contain hyphens)
        result["event_id"] = "-".join(remaining[:template_idx]) if template_idx > 0 else None

        template = remaining[template_idx].upper()
        result["market"] = _MARKET_TEMPLATE_MAP.get(template)

        after_template = remaining[template_idx + 1:]

        if not after_template:
            return result

        # If market is spread/total, next part might be a line value
        if result["market"] in ("total", "spread") and after_template:
            try:
                result["point"] = float(after_template[0])
                after_template = after_template[1:]
            except (ValueError, IndexError):
                pass

        # Remaining part is the outcome
        if after_template:
            outcome_raw = after_template[-1].lower()
            result["outcome"] = _OUTCOME_MAP.get(outcome_raw, outcome_raw)

        return result
