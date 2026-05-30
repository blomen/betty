import type { BankrollStats } from '@/types';

export function KpiBlock({ stats, bankrollSek }: { stats: BankrollStats; bankrollSek: number }) {
  // Bonus profit (Rule B): harvested from bonus-extraction campaigns, deliberately
  // excluded from Net Profit/ROI above. Shown only when non-zero so edge-only
  // profiles keep the standard 5-up layout. Both grid widths are spelled out
  // literally so Tailwind keeps the classes at build time.
  const showBonus = stats.bonus_profit !== 0;
  return (
    <div className="border-l-2 border-tabBets">
      <div className={`grid ${showBonus ? 'grid-cols-6' : 'grid-cols-5'} gap-px bg-border border border-border`}>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Net Profit</div>
          <div className={`text-lg font-semibold ${stats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
            {stats.total_profit >= 0 ? '+' : ''}{stats.total_profit.toFixed(0)} kr
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
          <div className={`text-lg font-semibold ${stats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
            {stats.roi_pct >= 0 ? '+' : ''}{stats.roi_pct.toFixed(1)}%
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bets</div>
          <div className="text-text text-lg font-semibold">{stats.total_bets}</div>
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-success">{stats.wins}W</span>
            <span className="text-error">{stats.losses}L</span>
            <span className="text-muted">{stats.voids}V</span>
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Avg CLV</div>
          {stats.clv_count > 0 ? (
            <>
              <div className={`text-lg font-semibold ${stats.avg_clv >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.avg_clv >= 0 ? '+' : ''}{stats.avg_clv.toFixed(1)}%
              </div>
              <div className="text-[10px] text-muted">{stats.clv_positive_pct.toFixed(0)}% beat close</div>
            </>
          ) : (
            <div className="text-lg font-semibold text-muted">-</div>
          )}
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bankroll</div>
          <div className="text-text text-lg font-semibold">{bankrollSek.toFixed(0)} kr</div>
        </div>
        {showBonus && (
          <div
            className="bg-panel2 px-3 py-2.5"
            title="Profit from bonus-extraction campaigns (both legs). Excluded from Net Profit and ROI (Rule B)."
          >
            <div className="text-[10px] text-amber-400/80 uppercase tracking-wider mb-0.5">Bonus profit</div>
            <div className="text-lg font-semibold text-amber-400">
              {stats.bonus_profit >= 0 ? '+' : ''}{stats.bonus_profit.toFixed(0)} kr
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
