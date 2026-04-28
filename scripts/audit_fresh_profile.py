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

# Mirror of arnold/frontend/src/pages/PlayPage.tsx:6 — keep in sync.
UNLIMITED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet", "kalshi"}

# Mirror of arnold/frontend/src/pages/PlayPage.tsx:24 — keep in sync.
SOFT_CLUSTER_MEMBERS = {
    "kambi": ["unibet", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
    "spectate": ["888sport", "mrgreen"],
    "altenar_main": ["betinia", "campobet", "lodur", "quickcasino", "swiper", "dbet"],
    "gecko_betsson": ["betsson", "nordicbet", "betsafe", "spelklubben"],
    "comeon_group": ["comeon", "lyllo", "hajper", "snabbare"],
}
SOFT_STANDALONES = {"interwetten", "vbet", "10bet", "tipwin", "coolbet", "bethard"}

# Signal-only providers expected to NOT appear on the arb page (no bet placement).
SIGNAL_ONLY_PROVIDERS = {"stake", "marathon", "consensus"}


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


def build_provider_coverage(yaml_doc: dict, bankroll: dict) -> tuple[list[str], int]:
    """Verify every active yaml provider appears in /api/bankroll with balance=0."""
    yaml_active = set(yaml_doc.get("active_providers", []))
    bankroll_ids = {p["id"] for p in bankroll["providers"]}
    bankroll_by_id = {p["id"]: p for p in bankroll["providers"]}

    lines = ["## Provider coverage", ""]
    flags = 0

    missing = sorted(yaml_active - bankroll_ids)
    for pid in missing:
        lines.append(f"- [!] missing-from-bankroll: `{pid}` is active in yaml but absent from `/api/bankroll`")
        flags += 1

    nonzero = [p for p in bankroll["providers"]
               if p["id"] in yaml_active and (p["balance"] or 0) > 0]
    for p in nonzero:
        lines.append(f"- [!] non-zero-balance: `{p['id']}` has balance={p['balance']} on the Audit profile")
        flags += 1

    if flags == 0:
        lines.append(f"- [ok] all {len(yaml_active)} active providers present with balance=0")
    lines.append("")
    return lines, flags


def build_bonus_coverage(yaml_bonuses: dict, bankroll: dict) -> tuple[list[str], int]:
    """Verify every yaml bonus block surfaces a non-null bonus_trigger_amount."""
    by_id = {p["id"]: p for p in bankroll["providers"]}
    lines = ["## Bonus coverage", ""]
    flags = 0

    for pid, cfg in sorted(yaml_bonuses.items()):
        amount = cfg.get("amount", 0) or 0
        if amount <= 0:
            lines.append(f"- [~] zero-amount: `{pid}` yaml bonus has `amount={amount}`, no trigger surfaced")
            continue
        provider_row = by_id.get(pid)
        if provider_row is None:
            lines.append(f"- [!] yaml-orphan: `{pid}` has yaml bonus but provider not in `/api/bankroll`")
            flags += 1
            continue
        trigger = provider_row.get("bonus_trigger_amount")
        if trigger is None:
            lines.append(
                f"- [!] bonus-not-actionable: `{pid}` yaml has amount={amount} but `/api/bankroll` returned `bonus_trigger_amount=null`"
            )
            flags += 1
        else:
            lines.append(f"- [ok] `{pid}`: deposit {int(trigger)} {provider_row.get('bonus_currency', 'SEK')} ({cfg.get('type')})")

    lines.append("")
    return lines, flags


def build_arb_page_sanity(yaml_doc: dict) -> tuple[list[str], int]:
    """Verify every active yaml provider is reachable through PlayPage's cluster map."""
    yaml_active = set(yaml_doc.get("active_providers", []))
    reachable = set(UNLIMITED_PROVIDERS) | set(SOFT_STANDALONES)
    for members in SOFT_CLUSTER_MEMBERS.values():
        reachable.update(members)

    lines = ["## Arb-page sanity", ""]
    flags = 0

    orphans = sorted(yaml_active - reachable - SIGNAL_ONLY_PROVIDERS)
    for pid in orphans:
        lines.append(f"- [~] not-on-arb-page: `{pid}` is active but missing from PlayPage cluster map (add to SOFT_STANDALONES or a cluster)")

    expected_signal = sorted(yaml_active & SIGNAL_ONLY_PROVIDERS)
    for pid in expected_signal:
        lines.append(f"- [ok] `{pid}` correctly excluded from arb page (signal-only)")

    if not orphans:
        lines.append(f"- [ok] all {len(yaml_active)} providers reachable through cluster map (or signal-only)")
    lines.append("")
    return lines, flags


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

    sections: list[str] = [
        f"# Fresh-Profile Audit — {dt.date.today().isoformat()}",
        "",
        f"**Profile id:** {profile_id} (`Audit`)",
        f"**API:** {args.api}",
        f"**Providers in `/api/bankroll`:** {len(bankroll['providers'])}",
        f"**Yaml bonus blocks:** {len(bonuses_yaml)}",
        "",
    ]
    total_critical = 0

    for builder in (
        lambda: build_provider_coverage(yaml_doc, bankroll),
        lambda: build_bonus_coverage(bonuses_yaml, bankroll),
        lambda: build_arb_page_sanity(yaml_doc),
    ):
        section_lines, flags = builder()
        sections.extend(section_lines)
        total_critical += flags

    sections.append("## Deposit recommendation")
    sections.append("")
    sections.append("_Filled in by Task 8._")
    sections.append("")

    sections.append(f"## Verdict: {'PASS' if total_critical == 0 else f'FAIL ({total_critical} critical flags)'}")
    sections.append("")

    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"[audit] {total_critical} critical flag(s); report written")
    return 0 if total_critical == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
