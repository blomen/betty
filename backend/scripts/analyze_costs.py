"""Analyze zone gaps vs trading costs to determine minimum viable trades."""
import json
import urllib.request

data = json.loads(urllib.request.urlopen("http://localhost:8000/debug/zones").read())
zones = sorted(data["zones"], key=lambda z: z["center"])
price = data["last_price"]

print("NQ: %.2f, Zones: %d" % (price, len(zones)))

# NQ cost structure (per round-trip)
TICK = 0.25
SPREAD_TICKS = 1          # typical NQ spread
SLIPPAGE_TICKS = 1        # 1 tick each way
COMMISSION_PER_SIDE = 1.04  # dollars
TICK_VALUE = 5.0           # $5 per tick for NQ

cost_pts = (SPREAD_TICKS + SLIPPAGE_TICKS * 2) * TICK + (COMMISSION_PER_SIDE * 2) / (TICK_VALUE / TICK)
print("\nCost per RT: %.2f pts (%.0f ticks)" % (cost_pts, cost_pts / TICK))
print("  Spread: %.2f pts" % (SPREAD_TICKS * TICK))
print("  Slippage: %.2f pts" % (SLIPPAGE_TICKS * 2 * TICK))
print("  Commission: $%.2f = %.2f pts" % (COMMISSION_PER_SIDE * 2, COMMISSION_PER_SIDE * 2 / (TICK_VALUE / TICK)))

# Zone gaps
print("\nZone gap analysis:")
print("%-10s %-10s %8s %8s %6s %s" % ("From", "To", "Gap", "Net", "R", "Viable"))
for i in range(len(zones) - 1):
    gap = zones[i + 1]["center"] - zones[i]["center"]
    net = gap - cost_pts
    r = net / (10 * TICK)  # 10 tick stop
    viable = "YES" if r >= 0.5 else "MARGINAL" if r > 0 else "NO"
    print("%-10.0f %-10.0f %7.1f %7.1f %5.2fR  %s" % (
        zones[i]["center"], zones[i + 1]["center"], gap, net, r, viable))

# For each zone near price: what's the nearest target?
print("\nFrom current price (%.0f):" % price)
for z in zones:
    dist = abs(z["center"] - price)
    if dist > 200:
        continue
    # Find nearest zone in each direction
    above = [z2 for z2 in zones if z2["center"] > z["center"]]
    below = [z2 for z2 in zones if z2["center"] < z["center"]]
    next_up = above[0]["center"] - z["center"] if above else 0
    next_dn = z["center"] - below[-1]["center"] if below else 0
    r_up = (next_up - cost_pts) / (10 * TICK) if next_up > 0 else 0
    r_dn = (next_dn - cost_pts) / (10 * TICK) if next_dn > 0 else 0
    print("  Zone %.0f: dist=%+.0f  next_up=%.0f(%.1fR) next_dn=%.0f(%.1fR)  m=%d" % (
        z["center"], z["center"] - price, next_up, r_up, next_dn, r_dn, z["members"]))
