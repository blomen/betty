"""Specials (odds boosts) and bonus validation API routes."""

import sys
from pathlib import Path
from fastapi import APIRouter

# Add backend root to path for script import
_backend_root = Path(__file__).parent.parent.parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

router = APIRouter(prefix="/api/specials", tags=["specials"])


@router.get("")
async def get_specials():
    """Get current odds boosts and specials from cached JSON."""
    from scripts.scrape_specials import load_specials, DATA_DIR

    specials = load_specials()

    # Check freshness
    import json
    specials_path = DATA_DIR / "specials.json"
    scraped_at = None
    if specials_path.exists():
        try:
            with open(specials_path, encoding="utf-8") as f:
                data = json.load(f)
            scraped_at = data.get("scraped_at")
        except Exception:
            pass

    return {
        "specials": specials,
        "count": len(specials),
        "scraped_at": scraped_at,
    }


@router.post("/scrape")
async def scrape_specials():
    """Run the specials scraper AND bonus validation, return fresh results."""
    from scripts.scrape_specials import scrape_all, save_specials
    from scripts.scrape_bonuses import (
        scrape_all_bonuses, validate_bonuses,
        save_bonus_validation, load_bonus_validation,
    )
    import asyncio
    from dataclasses import asdict

    loop = asyncio.get_event_loop()

    # Run both scrapers in parallel
    specials_future = loop.run_in_executor(None, lambda: scrape_all(verbose=False))
    bonuses_future = loop.run_in_executor(None, lambda: scrape_all_bonuses(verbose=False))

    specials = await specials_future
    scraped_bonuses = await bonuses_future

    # Save specials
    save_specials(specials)

    # Validate bonuses against providers.yaml and detect changes
    validation = await loop.run_in_executor(
        None, lambda: validate_bonuses(scraped_bonuses)
    )

    # Load previous validation to detect new changes
    prev_validation = load_bonus_validation()
    new_alerts = _detect_new_alerts(prev_validation, validation)

    # Save current validation
    save_bonus_validation(validation)

    return {
        "specials": [asdict(s) for s in specials],
        "count": len(specials),
        "scraped_at": specials[0].scraped_at if specials else None,
        "bonus_validation": {
            "validated_at": validation["validated_at"],
            "providers_checked": validation["providers_checked"],
            "matches": validation["matches"],
            "mismatches": validation["mismatches"],
            "changes": validation["changes"],
            "missing_from_yaml": validation["missing_from_yaml"],
            "missing_from_scrape": validation["missing_from_scrape"],
            "alerts": new_alerts,
        },
    }


@router.get("/bonus-status")
async def get_bonus_status():
    """Get latest bonus validation report from cache."""
    from scripts.scrape_bonuses import load_bonus_validation

    report = load_bonus_validation()
    if not report:
        return {
            "status": "no_data",
            "message": "No bonus validation has been run yet. Click Refresh to scrape.",
        }

    return {
        "status": "ok",
        "validated_at": report.get("validated_at"),
        "providers_checked": report.get("providers_checked", 0),
        "matches": report.get("matches", 0),
        "mismatches": report.get("mismatches", 0),
        "changes": report.get("changes", []),
        "missing_from_yaml": report.get("missing_from_yaml", []),
        "missing_from_scrape": report.get("missing_from_scrape", []),
        "provider_status": report.get("provider_status", {}),
    }


def _detect_new_alerts(
    prev: dict | None,
    current: dict,
) -> list[dict]:
    """
    Compare previous and current validation to find NEW changes.

    Returns list of alert dicts for the frontend to display.
    """
    alerts = []

    # If no previous validation, all current mismatches are new
    prev_changes = {}
    if prev:
        for change in prev.get("changes", []):
            prev_changes[change["provider_id"]] = change

    for change in current.get("changes", []):
        pid = change["provider_id"]
        if pid not in prev_changes:
            # Brand new mismatch
            for diff in change.get("diffs", []):
                alerts.append({
                    "type": "bonus_changed",
                    "provider_id": pid,
                    "field": diff["field"],
                    "old_value": diff["yaml"],
                    "new_value": diff["scraped"],
                    "message": f"{pid}: {diff['field']} changed from {diff['yaml']} to {diff['scraped']}",
                })
        else:
            # Check if any diffs are different from last time
            prev_diffs = {d["field"]: d for d in prev_changes[pid].get("diffs", [])}
            for diff in change.get("diffs", []):
                prev_diff = prev_diffs.get(diff["field"])
                if not prev_diff or prev_diff.get("scraped") != diff["scraped"]:
                    alerts.append({
                        "type": "bonus_changed",
                        "provider_id": pid,
                        "field": diff["field"],
                        "old_value": diff["yaml"],
                        "new_value": diff["scraped"],
                        "message": f"{pid}: {diff['field']} changed from {diff['yaml']} to {diff['scraped']}",
                    })

    # New providers found in scrape but not in YAML
    prev_missing_yaml = set()
    if prev:
        prev_missing_yaml = {m["provider_id"] for m in prev.get("missing_from_yaml", [])}

    for missing in current.get("missing_from_yaml", []):
        if missing["provider_id"] not in prev_missing_yaml:
            alerts.append({
                "type": "new_bonus_available",
                "provider_id": missing["provider_id"],
                "message": f"New bonus found for {missing['provider_id']} (not in providers.yaml)",
                "bonus": missing.get("scraped_bonus"),
            })

    return alerts
