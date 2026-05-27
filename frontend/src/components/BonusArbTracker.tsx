import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { displayTeamName } from '@/utils/formatters';
import type { BonusArbGroup, BonusArbSummary, BonusArbDaily } from '@/types';

type Window = 'today' | 'week' | '30d';

const SOFT_PROVIDERS = ['lodur', 'betinia', 'swiper'] as const;

function fmtSek(v: number | null | undefined, signed = false): string {
  if (v == null) return '-';
  const sign = signed && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(0)} kr`;
}

function fmtPct(v: number | null | undefined, signed = true): string {
  if (v == null) return '-';
  const sign = signed && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function statusColor(status: BonusArbGroup['status']): string {
  if (status === 'settled') return 'text-text';
  if (status === 'partial') return 'text-warning';
  return 'text-muted';
}

function resultPill(result: string): { text: string; cls: string } {
  switch (result) {
    case 'won': return { text: 'W', cls: 'bg-success/15 text-success' };
    case 'lost': return { text: 'L', cls: 'bg-error/15 text-error' };
    case 'void': return { text: 'V', cls: 'bg-muted/15 text-muted' };
    default: return { text: '…', cls: 'bg-accent/15 text-accent' };
  }
}

function SummaryTiles({ label, s }: { label: string; s: BonusArbSummary }) {
  const roi = s.settled > 0 && s.stake_sek > 0
    ? (s.pnl_sek / s.stake_sek) * 100
    : null;
  return (
    <div>
      <div className="text-[10px] text-muted uppercase tracking-wider mb-1">{label}</div>
      <div className="grid grid-cols-4 gap-px bg-border border border-border">
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Arbs</div>
          <div className="text-text text-lg font-semibold">{s.arbs}</div>
          <div className="text-[10px] text-muted">{s.settled} settled</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Stake</div>
          <div className="text-text text-lg font-semibold">{fmtSek(s.stake_sek)}</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">P&L</div>
          <div className={`text-lg font-semibold ${s.pnl_sek >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtSek(s.pnl_sek, true)}
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
          <div className={`text-lg font-semibold ${roi == null ? 'text-muted' : roi >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtPct(roi)}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 px-3 py-1.5 bg-panel2 border border-border border-t-0 text-[10px] text-muted">
        <span>displayed <span className="text-text">{fmtPct(s.avg_displayed_pct)}</span></span>
        <span>realized <span className="text-text">{fmtPct(s.avg_realized_pct)}</span></span>
        <span>anchor CLV <span className="text-text">{fmtPct(s.anchor_clv_avg)}</span></span>
        <span>counter CLV <span className="text-text">{fmtPct(s.counter_clv_avg)}</span></span>
        {s.counter_provider_clv_avg != null && (
          <span>same-mkt CLV <span className="text-text">{fmtPct(s.counter_provider_clv_avg)}</span></span>
        )}
      </div>
    </div>
  );
}

function DailyBars({ daily }: { daily: BonusArbDaily[] }) {
  const maxAbs = useMemo(
    () => Math.max(1, ...daily.map(d => Math.abs(d.pnl_sek))),
    [daily],
  );
  const W = 600, H = 80, PL = 8, PR = 8, PT = 8, PB = 18;
  const barW = (W - PL - PR) / daily.length;
  const zeroY = PT + (H - PT - PB) / 2;
  const yScale = (H - PT - PB) / 2 / maxAbs;
  const totalPnl = daily.reduce((s, d) => s + d.pnl_sek, 0);

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">
          Daily P&L (last 30 days)
        </span>
        <span className={`text-sm font-semibold ${totalPnl >= 0 ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
          {fmtSek(totalPnl, true)} total
        </span>
      </div>
      <div className="relative" style={{ paddingBottom: '20%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
          {daily.map((d, i) => {
            const x = PL + i * barW + 0.5;
            const h = Math.abs(d.pnl_sek) * yScale;
            const y = d.pnl_sek >= 0 ? zeroY - h : zeroY;
            const color = d.pnl_sek > 0 ? '#3fb950' : d.pnl_sek < 0 ? '#f85149' : '#30363d';
            return (
              <rect key={d.date} x={x} y={y} width={Math.max(0.5, barW - 1)} height={Math.max(0.5, h)}
                    fill={color} vectorEffect="non-scaling-stroke">
                <title>{`${d.date}: ${d.arbs} arbs (${d.settled} settled), ${fmtSek(d.stake_sek)} staked, ${fmtSek(d.pnl_sek, true)} P&L`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function GroupRow({ g, isExpanded, onToggle }: { g: BonusArbGroup; isExpanded: boolean; onToggle: () => void }) {
  const time = new Date(g.placed_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  const eventName = g.event
    ? `${displayTeamName(g.event.home_team ?? '', g.event.display_home)} vs ${displayTeamName(g.event.away_team ?? '', g.event.display_away)}`
    : (g.boost_event ?? '-');
  const sport = g.event?.sport ?? '';

  const aPill = resultPill(g.anchor.result);
  const cPill = g.counter ? resultPill(g.counter.result) : null;
  const yieldColor = g.realized_yield_pct == null
    ? 'text-muted'
    : g.realized_yield_pct >= 0 ? 'text-success' : 'text-error';

  return (
    <>
      <tr className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`} onClick={onToggle}>
        <td className="text-muted text-[11px] whitespace-nowrap">{time}</td>
        <td className="text-text text-sm">
          <div>{eventName}</div>
          {sport && <div className="text-muted2 text-[10px]">{sport}</div>}
        </td>
        <td className="text-text text-sm">
          <div className="flex items-center gap-1.5">
            <ProviderName name={g.anchor.provider_id} />
            <span className="font-medium">{g.anchor.odds.toFixed(2)}</span>
            <span className={`text-[10px] px-1 ${aPill.cls}`}>{aPill.text}</span>
            {g.anchor.is_bonus && <span className="text-[9px] px-1 bg-warning/15 text-warning">BONUS</span>}
          </div>
          <div className={`text-[10px] ${g.anchor.profit_sek == null ? 'text-muted' : g.anchor.profit_sek >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtSek(g.anchor.profit_sek, true)}
          </div>
        </td>
        <td className="text-text text-sm">
          {g.counter ? (
            <>
              <div className="flex items-center gap-1.5">
                <ProviderName name={g.counter.provider_id} />
                <span className="font-medium">{g.counter.odds.toFixed(2)}</span>
                <span className={`text-[10px] px-1 ${cPill!.cls}`}>{cPill!.text}</span>
              </div>
              <div className={`text-[10px] ${g.counter.profit_sek == null ? 'text-muted' : g.counter.profit_sek >= 0 ? 'text-success' : 'text-error'}`}>
                {fmtSek(g.counter.profit_sek, true)}
              </div>
            </>
          ) : (
            <span className="text-warning text-[11px]">unpaired</span>
          )}
        </td>
        <td className="text-right text-text text-sm">{fmtSek(g.total_stake_sek)}</td>
        <td className="text-right text-sm text-muted">{fmtPct(g.displayed_yield_pct)}</td>
        <td className={`text-right text-sm font-medium ${yieldColor}`}>{fmtPct(g.realized_yield_pct)}</td>
        <td className="text-right text-sm">
          <span className={(g.anchor.clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}>
            {fmtPct(g.anchor.clv_pct)}
          </span>
        </td>
        <td className="text-right text-sm">
          {g.counter && (
            <span className={(g.counter.clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}>
              {fmtPct(g.counter.clv_pct)}
            </span>
          )}
        </td>
        <td className={`text-right text-sm capitalize ${statusColor(g.status)}`}>{g.status}</td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={10} className="!p-0">
            <div className="px-3 py-2 bg-panel text-[11px] text-muted grid grid-cols-2 gap-4">
              <div>
                <div className="text-muted2 uppercase tracking-wider mb-1">Anchor</div>
                <div>Provider: <span className="text-text"><ProviderName name={g.anchor.provider_id} /></span></div>
                <div>Market: <span className="text-text">{g.anchor.market} / {g.anchor.outcome}{g.anchor.point != null ? ` ${g.anchor.point}` : ''}</span></div>
                <div>Stake: <span className="text-text">{g.anchor.stake_native.toFixed(2)} {g.anchor.currency} ({fmtSek(g.anchor.stake_sek)})</span></div>
                <div>Fair odds: <span className="text-text">{g.anchor.fair_odds_at_placement?.toFixed(3) ?? '-'}</span></div>
                <div>CLV: <span className="text-text">{fmtPct(g.anchor.clv_pct)}</span></div>
              </div>
              <div>
                <div className="text-muted2 uppercase tracking-wider mb-1">Counter</div>
                {g.counter ? (
                  <>
                    <div>Provider: <span className="text-text"><ProviderName name={g.counter.provider_id} /></span></div>
                    <div>Market: <span className="text-text">{g.counter.market} / {g.counter.outcome}{g.counter.point != null ? ` ${g.counter.point}` : ''}</span></div>
                    <div>Stake: <span className="text-text">{g.counter.stake_native.toFixed(2)} {g.counter.currency} ({fmtSek(g.counter.stake_sek)})</span></div>
                    <div>CLV (Pinnacle): <span className="text-text">{fmtPct(g.counter.clv_pct)}</span></div>
                    {g.counter.provider_clv_pct != null && (
                      <div>CLV (same-market): <span className="text-text">{fmtPct(g.counter.provider_clv_pct)}</span></div>
                    )}
                  </>
                ) : (
                  <div className="text-warning">No counter leg linked yet. Run correlate_arbs or check arb_runner placement.</div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function BonusArbTracker() {
  const [windowSel, setWindowSel] = useState<Window>('week');
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['bonus-arbs', windowSel],
    queryFn: () => api.getBonusArbs(windowSel),
    staleTime: 30_000,
  });

  return (
    <div className="border-l-2 border-tabBets">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs text-muted uppercase tracking-wider font-semibold">
          Bonus-Arb Tracker
        </h3>
        <div className="flex items-center gap-3">
          <div className="text-[10px] text-muted2">
            providers: {SOFT_PROVIDERS.join(' · ')}
          </div>
          <div className="flex gap-1">
            {(['today', 'week', '30d'] as Window[]).map(w => (
              <button
                key={w}
                onClick={() => setWindowSel(w)}
                className={`px-2 py-0.5 text-[10px] rounded border ${
                  windowSel === w
                    ? 'bg-tabBets/20 text-tabBets border-tabBets/40'
                    : 'bg-panel2 text-muted border-border hover:text-text'
                }`}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading && !data ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : !data ? null : (
        <>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <SummaryTiles label="Today" s={data.summary.today} />
            <SummaryTiles label="This Week" s={data.summary.week} />
          </div>

          <div className="mb-3">
            <DailyBars daily={data.daily} />
          </div>

          {data.groups.length === 0 ? (
            <div className="text-muted text-sm py-6 text-center border border-border bg-panel">
              No arbs in this window yet.
            </div>
          ) : (
            <div className="border border-border">
              <table className="sq">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Anchor</th>
                    <th>Counter</th>
                    <th className="text-right">Stake</th>
                    <th className="text-right">Displ.</th>
                    <th className="text-right">Realized</th>
                    <th className="text-right">Anchor CLV</th>
                    <th className="text-right">Counter CLV</th>
                    <th className="text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.groups.map(g => {
                    const key = `${g.arb_group_id ?? g.anchor.id}`;
                    return (
                      <GroupRow
                        key={key}
                        g={g}
                        isExpanded={expanded === key}
                        onToggle={() => setExpanded(expanded === key ? null : key)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
