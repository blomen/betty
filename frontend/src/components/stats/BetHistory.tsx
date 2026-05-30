import { Fragment, useState, useMemo } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useBetMutations } from '@/hooks/useBetMutations';
import { displayTeamName } from '@/utils/formatters';
import { resolveOutcome as resolveOutcomeBase, fmtAmount, fmtProfit } from '@/utils/betting';
import { ProviderName } from '@/components/ProviderName';
import type { Bet } from '@/types';

// ── Helpers ──────────────────────────────────────────────────────────

/** Time-to-kickoff in hours from PLACED time (for history CLV confidence) */
function getTTK(bet: Bet): number | null {
  if (!bet.start_time || !bet.placed_at) return null;
  const start = new Date(bet.start_time).getTime();
  const placed = new Date(bet.placed_at).getTime();
  return Math.max(0, (start - placed) / (1000 * 60 * 60));
}

function formatTTK(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

type TTKConfidence = 'high' | 'good' | 'medium' | 'low' | 'very_low' | 'unknown';

function getTTKTier(ttkHours: number | null): { label: string; color: string; confidence: TTKConfidence } {
  if (ttkHours === null) return { label: '-', color: 'text-muted', confidence: 'unknown' };
  if (ttkHours <= 6) return { label: formatTTK(ttkHours), color: 'text-success', confidence: 'high' };
  if (ttkHours <= 12) return { label: formatTTK(ttkHours), color: 'text-yellow', confidence: 'good' };
  if (ttkHours <= 24) return { label: formatTTK(ttkHours), color: 'text-warning', confidence: 'medium' };
  if (ttkHours <= 48) return { label: formatTTK(ttkHours), color: 'text-error', confidence: 'low' };
  return { label: formatTTK(ttkHours), color: 'text-muted2', confidence: 'very_low' };
}

const CLV_BADGE: Record<TTKConfidence, { text: string; cls: string }> = {
  high: { text: 'CLV HIGH', cls: 'bg-success/15 text-success' },
  good: { text: 'CLV GOOD', cls: 'bg-yellow/15 text-yellow' },
  medium: { text: 'CLV MED', cls: 'bg-warning/15 text-warning' },
  low: { text: 'CLV LOW', cls: 'bg-error/15 text-error' },
  very_low: { text: 'CLV ~', cls: 'bg-muted2/15 text-muted2' },
  unknown: { text: '-', cls: 'text-muted2' },
};

// ── Sort types ────────────────────────────────────────────────────────

type SortKey = 'date' | 'provider' | 'odds' | 'close' | 'clv' | 'edge' | 'stake' | 'profit' | 'prob' | 'ttk' | 'status';
export type SortDir = 'asc' | 'desc';

function getSortValue(bet: Bet, key: SortKey): number | string {
  switch (key) {
    case 'date': return new Date(bet.placed_at).getTime();
    case 'provider': return bet.provider;
    case 'odds': return bet.odds;
    case 'close': return bet.closing_odds ?? -9999;
    case 'clv': return bet.clv_pct ?? -9999;
    case 'edge': return bet.placed_edge_pct ?? -9999;
    case 'stake': return bet.stake;
    case 'profit': return bet.profit;
    case 'prob': return bet.selection_probability ?? -9999;
    case 'ttk': return getTTK(bet) ?? 99999;
    case 'status': {
      const order: Record<string, number> = { pending: 0, won: 1, lost: 2, void: 3 };
      return order[bet.result] ?? 4;
    }
    default: return 0;
  }
}

// ── Sortable header ──────────────────────────────────────────────────

function SortHeader({ label, sortKey, currentSort, onSort, align = 'left', title }: {
  label: string;
  sortKey: SortKey;
  currentSort: { key: SortKey; dir: SortDir } | null;
  onSort: (key: SortKey) => void;
  align?: 'left' | 'right';
  title?: string;
}) {
  const active = currentSort?.key === sortKey;
  const dir = active ? currentSort.dir : null;

  return (
    <th
      className={`cursor-pointer select-none hover:text-text transition-colors ${align === 'right' ? 'text-right' : ''}`}
      onClick={() => onSort(sortKey)}
      title={title}
    >
      <span className="inline-flex items-center gap-1">
        {align === 'right' && (
          <span className={`text-[8px] ${active ? 'text-accent' : 'text-muted2/50'}`}>
            {dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '⇅'}
          </span>
        )}
        {label}
        {align === 'left' && (
          <span className={`text-[8px] ${active ? 'text-accent' : 'text-muted2/50'}`}>
            {dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '⇅'}
          </span>
        )}
      </span>
    </th>
  );
}

// ── BetHistory component ─────────────────────────────────────────────

export function BetHistory({ bets, isLoading, refetch }: {
  bets: Bet[];
  isLoading: boolean;
  refetch: () => void;
}) {
  const { editBet } = useBetMutations();

  // Sort & search (persisted so state survives tab navigation)
  const [sort, setSort] = usePersistedState<{ key: SortKey; dir: SortDir } | null>('bbq_bets_sort', null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [search, setSearch] = usePersistedState('bbq_bets_search', '');

  // Inline editing state
  const [editingBetId, setEditingBetId] = useState<number | null>(null);
  const [editStake, setEditStake] = useState<string>('');
  const [editOdds, setEditOdds] = useState<string>('');
  const [editResult, setEditResult] = useState<string>('');

  // Cashout state
  const [cashoutBetId, setCashoutBetId] = useState<number | null>(null);
  const [cashoutAmount, setCashoutAmount] = useState<string>('');

  // Collapsed state
  const [historyCollapsed, setHistoryCollapsed] = usePersistedState('bbq_bets_historyCollapsed', true);

  // ── Derived: filtered + sorted history ────────────────────────────
  const historyBets = useMemo(() => {
    let result = bets.slice();

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(b =>
        (b.home_team && b.home_team.toLowerCase().includes(q)) ||
        (b.away_team && b.away_team.toLowerCase().includes(q)) ||
        (b.display_home && b.display_home.toLowerCase().includes(q)) ||
        (b.display_away && b.display_away.toLowerCase().includes(q)) ||
        b.provider.toLowerCase().includes(q) ||
        (b.sport && b.sport.toLowerCase().includes(q)) ||
        (b.league && b.league.toLowerCase().includes(q))
      );
    }

    if (sort) {
      result = [...result].sort((a, b) => {
        const va = getSortValue(a, sort.key);
        const vb = getSortValue(b, sort.key);
        let cmp = 0;
        if (typeof va === 'string' && typeof vb === 'string') cmp = va.localeCompare(vb);
        else cmp = (va as number) - (vb as number);
        return sort.dir === 'desc' ? -cmp : cmp;
      });
    } else {
      result = [...result].sort((a, b) => new Date(b.placed_at).getTime() - new Date(a.placed_at).getTime());
    }

    return result;
  }, [bets, sort, search]);

  // ── Handlers ──────────────────────────────────────────────────────

  const handleSort = (key: SortKey) => {
    setSort(prev => {
      if (prev?.key === key) {
        if (prev.dir === 'asc') return { key, dir: 'desc' };
        if (prev.dir === 'desc') return null;
        return { key, dir: 'asc' };
      }
      const textCols: SortKey[] = ['provider', 'status', 'date'];
      return { key, dir: textCols.includes(key) ? 'asc' : 'desc' };
    });
    setExpandedIdx(null);
  };

  const formatDate = (dateStr: string | null | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return '-';
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const getStatusColor = (result: Bet['result']) => {
    switch (result) {
      case 'won': return 'text-success';
      case 'lost': return 'text-error';
      case 'void': return 'text-muted';
      default: return 'text-accent';
    }
  };

  const resolveOutcome = (bet: Bet): string =>
    resolveOutcomeBase(bet.outcome || '-', bet, bet.point);

  const startEditing = (bet: Bet) => {
    setEditingBetId(bet.id);
    setEditStake(bet.stake.toFixed(0));
    setEditOdds(bet.odds.toFixed(2));
    setEditResult(bet.result);
  };

  const cancelEditing = () => {
    setEditingBetId(null);
    setEditStake('');
    setEditOdds('');
    setEditResult('');
  };

  const saveEdit = async (betId: number) => {
    const original = bets.find(b => b.id === betId);
    if (!original) return;

    const changes: { stake?: number; odds?: number; result?: string } = {};
    const newStake = parseFloat(editStake);
    const newOdds = parseFloat(editOdds);
    if (!isNaN(newStake) && newStake !== original.stake) changes.stake = newStake;
    if (!isNaN(newOdds) && newOdds !== original.odds) changes.odds = newOdds;
    if (editResult && editResult !== original.result) changes.result = editResult;

    if (Object.keys(changes).length === 0) {
      cancelEditing();
      return;
    }

    try {
      await editBet.mutateAsync({ betId, data: changes });
      cancelEditing();
      refetch();
    } catch (err) {
      console.error('Edit bet failed:', err);
    }
  };

  const startCashout = (bet: Bet) => {
    setCashoutBetId(bet.id);
    setCashoutAmount('');
  };

  const cancelCashout = () => {
    setCashoutBetId(null);
    setCashoutAmount('');
  };

  const confirmCashout = async (betId: number) => {
    const amount = parseFloat(cashoutAmount);
    if (isNaN(amount) || amount < 0) return;
    try {
      await editBet.mutateAsync({ betId, data: { result: 'void', payout: amount } });
      cancelCashout();
      refetch();
    } catch (err) {
      console.error('Cashout failed:', err);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <>
      {/* Bet History collapsible header */}
      <div className="flex items-center gap-3">
        <button
          className="flex items-center gap-2 text-left cursor-pointer group flex-1 min-w-0"
          onClick={() => setHistoryCollapsed(c => !c)}
        >
          <span className={`text-[10px] text-muted2 transition-transform ${historyCollapsed ? '' : 'rotate-90'}`}>▶</span>
          <h3 className="text-xs text-muted uppercase tracking-wider font-semibold group-hover:text-text transition-colors">
            Bet History <span className="text-muted2">{historyBets.length}</span>
          </h3>
        </button>
        {!historyCollapsed && (
          <input
            type="text"
            placeholder="Search event, provider, sport..."
            className="px-2 py-1 text-xs bg-bg border border-border text-text placeholder:text-muted2 w-64 focus:border-tabBets focus:outline-none"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
      </div>
      {!historyCollapsed && (isLoading && bets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : historyBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {search.trim() ? 'No matching bets.' : 'No bets found.'}
        </div>
      ) : (
        <>
          <div className="border-l-2 border-tabBets">
          <table className="sq">
            <thead>
              <tr>
                <SortHeader label="Date" sortKey="date" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Provider" sortKey="provider" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Entry" sortKey="odds" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Close" sortKey="close" currentSort={sort} onSort={handleSort} align="right" title="Closing line. For Pinnacle bets this is the de-vigged soft-book consensus (the sharp benchmark for reverse-value), NOT Pinnacle's own price — so it can swing far from your entry. For soft-book bets it's Pinnacle's closing line." />
                <SortHeader label="CLV" sortKey="clv" currentSort={sort} onSort={handleSort} align="right" title="Closing Line Value = entry odds vs the Close. Pinnacle bets are benchmarked against the soft-book consensus (reverse-value), soft-book bets against Pinnacle. Positive = beat the close." />
                <SortHeader label="Est Edge" sortKey="edge" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Stake" sortKey="stake" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Profit" sortKey="profit" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Prob" sortKey="prob" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Entry TTK" sortKey="ttk" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Status" sortKey="status" currentSort={sort} onSort={handleSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {historyBets.map((bet) => {
                const isExpanded = expandedIdx === bet.id;
                const isEditing = editingBetId === bet.id;
                const isCashingOut = cashoutBetId === bet.id;
                const ttk = getTTK(bet);
                const tier = getTTKTier(ttk);
                return (
                  // Key on Fragment, not on <tr> — React requires the key on
                  // the direct child of .map. With a keyless <>, an inline
                  // edit's state could attach to the wrong row when the sort
                  // order changes.
                  <Fragment key={bet.id}>
                    <tr
                      className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => { if (!isEditing) setExpandedIdx(isExpanded ? null : bet.id); }}
                    >
                      <td className="text-muted text-[11px] whitespace-nowrap">{formatDate(bet.placed_at)}</td>
                      <td className="text-text text-sm"><ProviderName name={bet.provider} /></td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                      <td className="text-right">
                        {bet.closing_odds != null ? (
                          <span
                            className={`text-sm ${bet.closing_odds > bet.odds ? 'text-success' : bet.closing_odds < bet.odds ? 'text-error' : 'text-text'}`}
                            title={bet.provider === 'pinnacle'
                              ? `Soft-book consensus close (de-vigged) — the reverse-value benchmark, not Pinnacle's own ${bet.odds.toFixed(2)} line`
                              : `Pinnacle closing line vs your ${bet.odds.toFixed(2)} entry`}
                          >
                            {bet.closing_odds.toFixed(2)}
                          </span>
                        ) : (
                          <span
                            className="text-sm text-muted"
                            title={bet.result === 'pending'
                              ? "No close yet — captured once the event kicks off (Pinnacle-only events with no soft consensus fall back to Pinnacle's own line)"
                              : 'No closing line was available for this market'}
                          >
                            -
                          </span>
                        )}
                      </td>
                      <td className="text-right">
                        {bet.clv_pct != null ? (
                          <span className={`text-sm font-medium ${bet.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.clv_pct >= 0 ? '+' : ''}{bet.clv_pct.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        {bet.placed_edge_pct != null ? (
                          <span className={`text-sm font-medium ${bet.placed_edge_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.placed_edge_pct >= 0 ? '+' : ''}{bet.placed_edge_pct.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right text-text text-sm">{fmtAmount(bet.stake, bet.currency)}</td>
                      <td className="text-right">
                        <span className={`text-sm font-medium ${bet.profit >= 0 ? 'text-success' : 'text-error'}`}>
                          {fmtProfit(bet.profit, bet.currency)}
                        </span>
                      </td>
                      <td className="text-right">
                        {bet.selection_probability != null ? (
                          <span className="text-sm text-text">{(bet.selection_probability * 100).toFixed(0)}%</span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        <span className={`text-sm ${tier.color}`}>{tier.label}</span>
                      </td>
                      <td className="text-right">
                        <span className={`text-sm capitalize ${getStatusColor(bet.result)}`}>{bet.result}</span>
                      </td>
                    </tr>
                    {isExpanded && (() => {
                      const badge = CLV_BADGE[tier.confidence];
                      return (
                      <tr key={`${bet.id}-expanded`}>
                        <td colSpan={11} className="!p-0" onClick={e => e.stopPropagation()}>
                          <div className="px-3 py-2 bg-panel space-y-2">
                            <div className="flex items-center gap-6 text-xs text-muted">
                              {bet.home_team && bet.away_team && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Event: </span>
                                  <span className="text-text">{displayTeamName(bet.home_team, bet.display_home)} vs {displayTeamName(bet.away_team, bet.display_away)}</span>
                                  {bet.sport && <span className="text-muted2 ml-1">({bet.sport})</span>}
                                </div>
                              )}
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Selection: </span>
                                <span className="text-text">{resolveOutcome(bet)}</span>
                                {bet.market && <span className="text-muted2 ml-1">({bet.market}{bet.point != null ? ` ${bet.point}` : ''})</span>}
                              </div>
                              {bet.fair_odds_at_placement != null && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Fair: </span>
                                  <span className="text-text">{bet.fair_odds_at_placement.toFixed(3)}</span>
                                </div>
                              )}
                              {ttk !== null && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">CLV Conf: </span>
                                  <span className={`text-[10px] px-1 py-0.5 ${badge.cls}`}>{badge.text}</span>
                                </div>
                              )}
                              {!isEditing && !isCashingOut && (
                                <div className="flex items-center gap-1.5 ml-auto">
                                  {bet.result === 'pending' && (
                                    <button
                                      className="text-[10px] px-1.5 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                      onClick={() => startCashout(bet)}
                                    >Cashout</button>
                                  )}
                                  <button
                                    className="text-[10px] px-1.5 py-0.5 bg-accent/15 text-accent hover:bg-accent/30 transition-colors"
                                    onClick={() => startEditing(bet)}
                                  >Edit</button>
                                </div>
                              )}
                            </div>
                            {isCashingOut && (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Cashout Amount:</span>
                                  <input
                                    type="number"
                                    step="1"
                                    className="w-24 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={cashoutAmount}
                                    onChange={e => setCashoutAmount(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') confirmCashout(bet.id); if (e.key === 'Escape') cancelCashout(); }}
                                    placeholder={fmtAmount(bet.stake, bet.currency)}
                                    autoFocus
                                  />
                                  <span className="text-muted2">{bet.currency === 'USD' || bet.currency === 'USDC' ? '$' : 'kr'}</span>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                  onClick={() => confirmCashout(bet.id)}
                                >Confirm</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelCashout}
                                >Cancel</button>
                              </div>
                            )}
                            {isEditing && (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Stake:</span>
                                  <input
                                    type="number"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editStake}
                                    onChange={e => setEditStake(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(bet.id); if (e.key === 'Escape') cancelEditing(); }}
                                    autoFocus
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Odds:</span>
                                  <input
                                    type="number"
                                    step="0.01"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editOdds}
                                    onChange={e => setEditOdds(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(bet.id); if (e.key === 'Escape') cancelEditing(); }}
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Result:</span>
                                  <select
                                    className="px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editResult}
                                    onChange={e => setEditResult(e.target.value)}
                                  >
                                    <option value="won">won</option>
                                    <option value="lost">lost</option>
                                    <option value="void">void</option>
                                  </select>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors"
                                  onClick={() => saveEdit(bet.id)}
                                >Save</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelEditing}
                                >Cancel</button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                      );
                    })()}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      ))}
    </>
  );
}
