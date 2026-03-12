"""CFTC Commitment of Traders report fetcher."""
import logging
from dataclasses import dataclass
from datetime import date

import httpx

logger = logging.getLogger(__name__)

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
NQ_CFTC_CODE = "209742"


@dataclass
class COTReport:
    report_date: date
    net_commercial: int
    net_non_commercial: int
    net_non_reportable: int
    open_interest: int


async def fetch_cot(cftc_code: str = NQ_CFTC_CODE, limit: int = 4) -> list[COTReport]:
    """Fetch latest COT reports for an instrument from CFTC Socrata API."""
    params = {
        "$where": f"cftc_contract_market_code='{cftc_code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(COT_URL, params=params)
            resp.raise_for_status()
            rows = resp.json()

        reports = []
        for row in rows:
            reports.append(COTReport(
                report_date=date.fromisoformat(row.get("report_date_as_yyyy_mm_dd", "")[:10]),
                net_commercial=int(row.get("comm_positions_long_all", 0)) - int(row.get("comm_positions_short_all", 0)),
                net_non_commercial=int(row.get("noncomm_positions_long_all", 0)) - int(row.get("noncomm_positions_short_all", 0)),
                net_non_reportable=int(row.get("nonrept_positions_long_all", 0)) - int(row.get("nonrept_positions_short_all", 0)),
                open_interest=int(row.get("open_interest_all", 0)),
            ))
        return reports
    except Exception as e:
        logger.error("COT fetch failed: %s", e)
        return []
