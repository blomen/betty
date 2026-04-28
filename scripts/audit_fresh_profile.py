# scripts/audit_fresh_profile.py
"""One-shot audit: create the Audit profile, verify provider/bonus coverage,
compute deposit recommendation, write report to docs/audits/.

Usage:
    python scripts/audit_fresh_profile.py [--api http://localhost:8000]

Pre-req: arnold.bat is running so /api/* routes through the SSH tunnel to
the production server.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx
import yaml


def find_or_create_audit_profile(api: httpx.Client) -> int:
    """Get the Audit profile id (create one if it doesn't exist)."""
    profiles = api.get("/api/profiles").raise_for_status().json()["profiles"]
    for p in profiles:
        if p["name"] == "Audit":
            return p["id"]
    created = api.post("/api/profiles", json={"name": "Audit"}).raise_for_status().json()
    return created["profile"]["id"]


def setup_audit_profile(api: httpx.Client, profile_id: int) -> None:
    api.post(f"/api/profiles/{profile_id}/seed-bonuses").raise_for_status()
    api.post(f"/api/profiles/{profile_id}/activate").raise_for_status()


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--repo-root", default=str(Path(__file__).parent.parent))
    parser.add_argument("--out", default=None,
                        help="Output report path (defaults to docs/audits/YYYY-MM-DD-fresh-profile-audit.md)")
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    out_path = Path(args.out) if args.out else (
        repo_root / "docs" / "audits" /
        f"{dt.date.today().isoformat()}-fresh-profile-audit.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.api, timeout=30.0) as api:
        profile_id = find_or_create_audit_profile(api)
        print(f"[audit] using Audit profile id={profile_id}")
        setup_audit_profile(api, profile_id)

        bankroll = api.get("/api/bankroll").raise_for_status().json()
        bonuses_yaml = api.get("/api/bankroll/bonuses").raise_for_status().json()

    yaml_path = repo_root / "backend" / "src" / "config" / "providers.yaml"
    yaml_doc = load_yaml(yaml_path)

    print(f"[audit] {len(bankroll['providers'])} providers in /api/bankroll")
    print(f"[audit] {len(bonuses_yaml)} yaml bonus blocks")
    print(f"[audit] report → {out_path}")

    # Subsequent tasks fill in real report sections.
    out_path.write_text(
        f"# Fresh-Profile Audit — {dt.date.today().isoformat()}\n\n"
        f"Profile id: {profile_id}\n"
        f"Providers in bankroll: {len(bankroll['providers'])}\n"
        f"Yaml bonus blocks: {len(bonuses_yaml)}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
