"""Analyze today's live signals."""
import json
import sys
from pathlib import Path

signals_file = Path("/app/data/rl/signals/2026-04-08.jsonl")
if not signals_file.exists():
    print("No signals file")
    sys.exit()

signals = [json.loads(l) for l in open(signals_file)]
spec = [s for s in signals if s.get("model_type") == "specialists"]
print("Total: %d, Specialist: %d" % (len(signals), len(spec)))

if not spec:
    sys.exit()

# Action counts
actions = {}
for s in spec:
    a = s.get("action", "?")
    actions[a] = actions.get(a, 0) + 1
for a, c in sorted(actions.items()):
    print("  %s: %d (%.1f%%)" % (a, c, c / len(spec) * 100))

# Analyze SKIPs
skips = [s for s in spec if s.get("action") == "SKIP"]
conts = [s for s in spec if s.get("action") == "CONTINUATION"]
revs = [s for s in spec if s.get("action") == "REVERSAL"]

if skips:
    cp = [s.get("cont_p", 0) for s in skips]
    rp = [s.get("rev_p", 0) for s in skips]
    ce = [s.get("cont_ev", 0) for s in skips]
    re = [s.get("rev_ev", 0) for s in skips]

    print("\nSKIP analysis (%d):" % len(skips))
    print("  cont_p:  mean=%.3f min=%.3f max=%.3f" % (sum(cp)/len(cp), min(cp), max(cp)))
    print("  rev_p:   mean=%.3f min=%.3f max=%.3f" % (sum(rp)/len(rp), min(rp), max(rp)))
    print("  cont_ev: mean=%.3f min=%.3f max=%.3f" % (sum(ce)/len(ce), min(ce), max(ce)))
    print("  rev_ev:  mean=%.3f min=%.3f max=%.3f" % (sum(re)/len(re), min(re), max(re)))

    # Why skip? Thresholds: MIN_CONFIDENCE=0.55, MIN_EV=0.1
    below_conf = sum(1 for s in skips if max(s.get("cont_p", 0), s.get("rev_p", 0)) < 0.55)
    below_ev = sum(1 for s in skips if max(s.get("cont_ev", 0), s.get("rev_ev", 0)) < 0.1)
    conf_pass_ev_fail = sum(1 for s in skips if max(s.get("cont_p", 0), s.get("rev_p", 0)) >= 0.55 and max(s.get("cont_ev", 0), s.get("rev_ev", 0)) < 0.1)
    print("\n  Skip reasons:")
    print("    Below confidence (0.55): %d (%.1f%%)" % (below_conf, below_conf / len(skips) * 100))
    print("    Below EV (0.1):          %d (%.1f%%)" % (below_ev, below_ev / len(skips) * 100))
    print("    Conf OK but EV low:      %d (%.1f%%)" % (conf_pass_ev_fail, conf_pass_ev_fail / len(skips) * 100))

    # Distribution of max(cont_p, rev_p) for skips
    max_p = [max(s.get("cont_p", 0), s.get("rev_p", 0)) for s in skips]
    print("\n  Max probability distribution (skipped):")
    for thresh in [0.3, 0.4, 0.45, 0.5, 0.55]:
        count = sum(1 for p in max_p if p >= thresh)
        print("    >= %.2f: %d (%.1f%%)" % (thresh, count, count / len(skips) * 100))

if conts:
    cp = [s.get("cont_p", 0) for s in conts]
    ce = [s.get("cont_ev", 0) for s in conts]
    print("\nCONT signals (%d):" % len(conts))
    print("  cont_p: mean=%.3f min=%.3f max=%.3f" % (sum(cp)/len(cp), min(cp), max(cp)))
    print("  cont_ev: mean=%.3f min=%.3f max=%.3f" % (sum(ce)/len(ce), min(ce), max(ce)))

if revs:
    rp = [s.get("rev_p", 0) for s in revs]
    re = [s.get("rev_ev", 0) for s in revs]
    print("\nREV signals (%d):" % len(revs))
    print("  rev_p: mean=%.3f min=%.3f max=%.3f" % (sum(rp)/len(rp), min(rp), max(rp)))
    print("  rev_ev: mean=%.3f min=%.3f max=%.3f" % (sum(re)/len(re), min(re), max(re)))

# Per-zone breakdown
zones = set(s.get("zone_center", 0) for s in spec)
print("\nPer zone (%d zones):" % len(zones))
for z in sorted(zones):
    zs = [s for s in spec if s.get("zone_center") == z]
    acts = {}
    for s in zs:
        a = s.get("action")
        acts[a] = acts.get(a, 0) + 1
    print("  %.0f: n=%d %s" % (z, len(zs), dict(acts)))
