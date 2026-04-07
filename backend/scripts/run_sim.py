"""Quick script to run session simulation on test set."""
import numpy as np

obs = np.load("/app/data/rl/episodes/observations.npy")
rc = np.load("/app/data/rl/episodes/rewards_cont.npy")
rr = np.load("/app/data/rl/episodes/rewards_rev.npy")

n = len(obs)
test_start = int(n * 0.83)
t_obs, t_rc, t_rr = obs[test_start:], rc[test_start:], rr[test_start:]

from src.rl.agent.specialists import SpecialistEnsemble
from src.rl.agent.session_simulator import SessionSimulator

ens = SpecialistEnsemble.load("/app/data/rl/models/specialists_v5.joblib")
sim = SessionSimulator(ens)

tn = len(t_obs)
print("Simulating %d test episodes..." % tn)
result = sim.run(t_obs, t_rc, t_rr)

print("\nTrades: %d" % result.total_trades)
print("Win rate: %.1f%%" % (result.win_rate * 100))
print("Avg R/trade: %+.3f" % result.avg_r_per_trade)
print("Total R: %+.1f" % result.total_r)
print("Profit factor: %.2f" % result.profit_factor)
print("Max drawdown: %.1fR" % result.max_drawdown_r)
print("Avg levels captured: %.1f" % result.avg_levels_captured)
print("Multi-level trades: %.1f%%" % (result.multi_level_pct * 100))
print("Avg hold: %.1f episodes" % result.avg_hold_episodes)
print("Signals: CONT=%d REV=%d SKIP=%d" % (result.cont_signals_total, result.rev_signals_total, result.skip_signals_total))

pnls = np.array([t.pnl_r for t in result.trades])
print("\nR dist: p10=%+.2f p25=%+.2f p50=%+.2f p75=%+.2f p90=%+.2f" % (
    np.percentile(pnls, 10), np.percentile(pnls, 25), np.percentile(pnls, 50),
    np.percentile(pnls, 75), np.percentile(pnls, 90)))

print("\nBy levels captured:")
for lc in range(8):
    tt = [t for t in result.trades if t.levels_captured == lc]
    if not tt:
        continue
    pp = np.array([t.pnl_r for t in tt])
    wr = (pp > 0).sum() / len(pp) * 100
    print("  %d levels: n=%d win=%.0f%% avg=%+.2f total=%+.0f" % (lc, len(tt), wr, pp.mean(), pp.sum()))

print("\nTop 5:")
for t in sorted(result.trades, key=lambda t: t.pnl_r, reverse=True)[:5]:
    print("  %5s %12s levels=%d hold=%d pnl=%+.2fR" % (t.direction, t.entry_signal, t.levels_captured, t.exit_idx - t.entry_idx, t.pnl_r))

print("\nWorst 5:")
for t in sorted(result.trades, key=lambda t: t.pnl_r)[:5]:
    print("  %5s %12s levels=%d hold=%d pnl=%+.2fR" % (t.direction, t.entry_signal, t.levels_captured, t.exit_idx - t.entry_idx, t.pnl_r))

actions, _, _ = ens.decide_batch(t_obs)
tm = actions != 2
indep_r = np.where(actions[tm] == 0, t_rc[tm], t_rr[tm])
print("\nIndependent: %d trades, total_R=%.0f" % (tm.sum(), indep_r.sum()))
print("Chained:     %d trades, total_R=%.0f" % (result.total_trades, result.total_r))
print("Multiplier:  %.2fx" % (result.total_r / max(indep_r.sum(), 1)))
