import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Trade } from '@/types/trading';

const STATE_COLORS: Record<string, string> = {
  created: 'bg-muted/20 text-muted',
  armed: 'bg-yellow/20 text-yellow',
  triggered: 'bg-warning/20 text-warning',
  open: 'bg-success/20 text-success',
  managed: 'bg-accent/20 text-accent',
  closed: 'bg-muted2/20 text-muted2',
  reviewed: 'bg-tabTradingJournal/20 text-tabTradingJournal',
};

const ACTIVE_STATES = ['created', 'armed', 'triggered', 'open', 'managed'];

export function TradingTradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filterState, setFilterState] = useState<string>('all');
  const [filterInstrument, setFilterInstrument] = useState<string>('all');

  // Action modals
  const [partialExitId, setPartialExitId] = useState<number | null>(null);
  const [partialContracts, setPartialContracts] = useState('');
  const [partialPrice, setPartialPrice] = useState('');
  const [trailStopId, setTrailStopId] = useState<number | null>(null);
  const [trailPrice, setTrailPrice] = useState('');
  const [addPosId, setAddPosId] = useState<number | null>(null);
  const [addContracts, setAddContracts] = useState('');
  const [addPrice, setAddPrice] = useState('');
  const [closeId, setCloseId] = useState<number | null>(null);
  const [closePrice, setClosePrice] = useState('');

  const fetchTrades = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await api.getTrades();
      setTrades(res.trades);
    } catch (err) {
      console.error('Failed to fetch trades:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchTrades(); }, [fetchTrades]);

  const activeTrades = useMemo(() => trades.filter(t => ACTIVE_STATES.includes(t.state)), [trades]);
  const historyTrades = useMemo(() => {
    let list = trades.filter(t => !ACTIVE_STATES.includes(t.state));
    if (filterState !== 'all') list = list.filter(t => t.state === filterState);
    if (filterInstrument !== 'all') list = list.filter(t => t.instrument === filterInstrument);
    return list;
  }, [trades, filterState, filterInstrument]);

  const instruments = useMemo(() => [...new Set(trades.map(t => t.instrument))], [trades]);

  const transition = async (id: number, toState: string) => {
    await api.transitionTrade(id, toState);
    fetchTrades();
  };

  const handlePartialExit = async () => {
    if (!partialExitId) return;
    await api.partialExitTrade(partialExitId, parseInt(partialContracts), parseFloat(partialPrice));
    setPartialExitId(null);
    setPartialContracts('');
    setPartialPrice('');
    fetchTrades();
  };

  const handleMoveToBE = async (id: number) => {
    await api.moveToBE(id);
    fetchTrades();
  };

  const handleTrailStop = async () => {
    if (!trailStopId) return;
    await api.trailStop(trailStopId, parseFloat(trailPrice));
    setTrailStopId(null);
    setTrailPrice('');
    fetchTrades();
  };

  const handleAddPosition = async () => {
    if (!addPosId) return;
    await api.addPosition(addPosId, parseInt(addContracts), parseFloat(addPrice));
    setAddPosId(null);
    setAddContracts('');
    setAddPrice('');
    fetchTrades();
  };

  const handleClose = async () => {
    if (!closeId) return;
    // Commission is auto-calculated from config when 0
    await api.closeTrade(closeId, parseFloat(closePrice) || 0, 0);
    setCloseId(null);
    setClosePrice('');
    fetchTrades();
  };

  const pnlColor = (v: number | null) => v == null ? 'text-muted' : v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-muted';
  const fmt = (v: number | null) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  if (isLoading) return <div className="text-muted text-sm">Loading trades...</div>;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="tradingTrades" color={TAB_COLORS.tradingTrades} />
        Trades
      </h2>

      {/* Active Trades */}
      {activeTrades.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-muted">Active ({activeTrades.length})</h3>
          {activeTrades.map(trade => (
            <div key={trade.id} className="border border-border bg-panel rounded p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className={`text-xs px-2 py-0.5 rounded ${STATE_COLORS[trade.state] || ''}`}>{trade.state}</span>
                  <span className="text-text font-semibold">{trade.instrument}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${trade.direction === 'long' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
                    {trade.direction.toUpperCase()}
                  </span>
                  <span className="text-xs text-muted">{trade.setup_type}</span>
                  <span className="text-xs text-muted">#{trade.id}</span>
                </div>
                <div className="flex items-center gap-2">
                  {trade.rr_ratio != null && <span className="text-xs text-muted">R:R {trade.rr_ratio.toFixed(1)}</span>}
                  <span className="text-xs text-muted">{trade.contracts} ct</span>
                </div>
              </div>

              {/* Levels */}
              <div className="flex gap-4 text-xs mb-3">
                <span className="text-muted">Entry: <span className="text-text font-mono">{trade.entry_price ?? '—'}</span></span>
                <span className="text-muted">Stop: <span className="text-error font-mono">{trade.stop_price ?? '—'}</span></span>
                {trade.be_price != null && <span className="text-muted">BE: <span className="text-warning font-mono">{trade.be_price}</span></span>}
                {trade.targets && trade.targets.length > 0 && (
                  <span className="text-muted">Targets: <span className="text-success font-mono">{trade.targets.map(t => t.price).join(', ')}</span></span>
                )}
                {trade.risk_amount != null && <span className="text-muted">Risk: <span className="text-error font-mono">${trade.risk_amount.toFixed(2)}</span></span>}
              </div>

              {/* Action buttons */}
              <div className="flex gap-2 flex-wrap">
                {trade.state === 'created' && (
                  <>
                    <button onClick={() => transition(trade.id, 'armed')} className="text-xs px-3 py-1 bg-yellow/20 text-yellow rounded hover:bg-yellow/30">Arm</button>
                    <button onClick={() => transition(trade.id, 'closed')} className="text-xs px-3 py-1 bg-error/20 text-error rounded hover:bg-error/30">Cancel</button>
                  </>
                )}
                {trade.state === 'armed' && (
                  <>
                    <button onClick={() => transition(trade.id, 'triggered')} className="text-xs px-3 py-1 bg-warning/20 text-warning rounded hover:bg-warning/30">Triggered</button>
                    <button onClick={() => transition(trade.id, 'closed')} className="text-xs px-3 py-1 bg-error/20 text-error rounded hover:bg-error/30">Cancel</button>
                  </>
                )}
                {trade.state === 'triggered' && (
                  <>
                    <button onClick={() => transition(trade.id, 'open')} className="text-xs px-3 py-1 bg-success/20 text-success rounded hover:bg-success/30">Open</button>
                    <button onClick={() => transition(trade.id, 'closed')} className="text-xs px-3 py-1 bg-error/20 text-error rounded hover:bg-error/30">Cancel</button>
                  </>
                )}
                {trade.state === 'open' && (
                  <>
                    <button onClick={() => setPartialExitId(partialExitId === trade.id ? null : trade.id)} className="text-xs px-3 py-1 bg-accent/20 text-accent rounded hover:bg-accent/30">Partial Exit</button>
                    <button onClick={() => handleMoveToBE(trade.id)} className="text-xs px-3 py-1 bg-warning/20 text-warning rounded hover:bg-warning/30">Move to BE</button>
                    <button onClick={() => setTrailStopId(trailStopId === trade.id ? null : trade.id)} className="text-xs px-3 py-1 bg-yellow/20 text-yellow rounded hover:bg-yellow/30">Trail Stop</button>
                    <button onClick={() => setAddPosId(addPosId === trade.id ? null : trade.id)} className="text-xs px-3 py-1 bg-tabTradingTrades/20 text-tabTradingTrades rounded hover:bg-tabTradingTrades/30">Add</button>
                    <button onClick={() => transition(trade.id, 'managed')} className="text-xs px-3 py-1 bg-muted/20 text-muted rounded hover:bg-muted/30">Manage</button>
                    <button onClick={() => setCloseId(closeId === trade.id ? null : trade.id)} className="text-xs px-3 py-1 bg-error/20 text-error rounded hover:bg-error/30">Close</button>
                  </>
                )}
                {trade.state === 'managed' && (
                  <button onClick={() => setCloseId(closeId === trade.id ? null : trade.id)} className="text-xs px-3 py-1 bg-error/20 text-error rounded hover:bg-error/30">Close</button>
                )}
              </div>

              {/* Inline action forms */}
              {partialExitId === trade.id && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                  <input type="number" value={partialContracts} onChange={e => setPartialContracts(e.target.value)} placeholder="Contracts" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-24 font-mono" />
                  <input type="number" step="0.25" value={partialPrice} onChange={e => setPartialPrice(e.target.value)} placeholder="Exit price" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-28 font-mono" />
                  <button onClick={handlePartialExit} className="text-xs bg-accent/20 text-accent px-3 py-1 rounded">Confirm</button>
                </div>
              )}
              {trailStopId === trade.id && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                  <input type="number" step="0.25" value={trailPrice} onChange={e => setTrailPrice(e.target.value)} placeholder="New stop price" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-32 font-mono" />
                  <button onClick={handleTrailStop} className="text-xs bg-yellow/20 text-yellow px-3 py-1 rounded">Confirm</button>
                </div>
              )}
              {addPosId === trade.id && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                  <input type="number" value={addContracts} onChange={e => setAddContracts(e.target.value)} placeholder="Contracts" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-24 font-mono" />
                  <input type="number" step="0.25" value={addPrice} onChange={e => setAddPrice(e.target.value)} placeholder="Entry price" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-28 font-mono" />
                  <button onClick={handleAddPosition} className="text-xs bg-tabTradingTrades/20 text-tabTradingTrades px-3 py-1 rounded">Confirm</button>
                </div>
              )}
              {closeId === trade.id && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                  <input type="number" step="0.25" value={closePrice} onChange={e => setClosePrice(e.target.value)} placeholder="Exit price" className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-32 font-mono" />
                  <span className="text-xs text-muted">Commission: auto</span>
                  <button onClick={handleClose} className="text-xs bg-error/20 text-error px-3 py-1 rounded">Confirm Close</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* History */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-muted">History ({historyTrades.length})</h3>
          <div className="flex gap-2">
            <select value={filterState} onChange={e => setFilterState(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1 text-xs text-text">
              <option value="all">All states</option>
              <option value="closed">Closed</option>
              <option value="reviewed">Reviewed</option>
            </select>
            <select value={filterInstrument} onChange={e => setFilterInstrument(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1 text-xs text-text">
              <option value="all">All instruments</option>
              {instruments.map(i => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
        </div>

        {historyTrades.length === 0 ? (
          <div className="text-muted text-xs py-4 text-center border border-border bg-panel rounded">No closed trades yet</div>
        ) : (
          <div className="border border-border rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-panel2 text-xs text-muted border-b border-border">
                  <th className="text-left px-3 py-2">#</th>
                  <th className="text-left px-3 py-2">Instrument</th>
                  <th className="text-left px-3 py-2">Dir</th>
                  <th className="text-left px-3 py-2">Setup</th>
                  <th className="text-right px-3 py-2">Contracts</th>
                  <th className="text-right px-3 py-2">Entry</th>
                  <th className="text-right px-3 py-2">P&L</th>
                  <th className="text-right px-3 py-2">R</th>
                  <th className="text-left px-3 py-2">State</th>
                  <th className="text-left px-3 py-2">Closed</th>
                </tr>
              </thead>
              <tbody>
                {historyTrades.map(trade => (
                  <>
                    <tr
                      key={trade.id}
                      onClick={() => setExpandedId(expandedId === trade.id ? null : trade.id)}
                      className="border-b border-border hover:bg-panel2/50 cursor-pointer"
                    >
                      <td className="px-3 py-2 text-muted font-mono">{trade.id}</td>
                      <td className="px-3 py-2 text-text">{trade.instrument}</td>
                      <td className="px-3 py-2">
                        <span className={trade.direction === 'long' ? 'text-success' : 'text-error'}>{trade.direction.toUpperCase()}</span>
                      </td>
                      <td className="px-3 py-2 text-muted">{trade.setup_type}</td>
                      <td className="px-3 py-2 text-right text-text font-mono">{trade.contracts}</td>
                      <td className="px-3 py-2 text-right text-text font-mono">{trade.entry_price ?? '—'}</td>
                      <td className={`px-3 py-2 text-right font-mono ${pnlColor(trade.realized_pnl)}`}>{fmt(trade.realized_pnl)}</td>
                      <td className={`px-3 py-2 text-right font-mono ${pnlColor(trade.r_multiple)}`}>{trade.r_multiple != null ? `${trade.r_multiple.toFixed(1)}R` : '—'}</td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${STATE_COLORS[trade.state] || ''}`}>{trade.state}</span>
                      </td>
                      <td className="px-3 py-2 text-xs text-muted">{trade.closed_at ? new Date(trade.closed_at).toLocaleDateString() : '—'}</td>
                    </tr>
                    {expandedId === trade.id && (
                      <tr key={`${trade.id}-detail`} className="bg-panel2/30">
                        <td colSpan={10} className="px-4 py-3">
                          <div className="space-y-3">
                            {/* Levels detail */}
                            <div className="flex gap-6 text-xs">
                              <span className="text-muted">Stop: <span className="text-error font-mono">{trade.stop_price ?? '—'}</span></span>
                              {trade.be_price != null && <span className="text-muted">BE: <span className="text-warning font-mono">{trade.be_price}</span></span>}
                              {trade.targets && trade.targets.length > 0 && (
                                <span className="text-muted">Targets: <span className="text-success font-mono">{trade.targets.map(t => t.price).join(', ')}</span></span>
                              )}
                              {trade.risk_amount != null && <span className="text-muted">Risk: <span className="text-error font-mono">${trade.risk_amount.toFixed(2)}</span></span>}
                              {trade.rr_ratio != null && <span className="text-muted">R:R: <span className="text-text font-mono">{trade.rr_ratio.toFixed(1)}</span></span>}
                              <span className="text-muted">Commission: <span className="text-text font-mono">${trade.commission.toFixed(2)}</span></span>
                            </div>

                            {/* Confirmations */}
                            {trade.confirmations && Object.keys(trade.confirmations).length > 0 && (
                              <div>
                                <div className="text-xs text-muted mb-1">Confirmations:</div>
                                <div className="flex flex-wrap gap-2">
                                  {Object.entries(trade.confirmations).map(([key, val]) => (
                                    <span key={key} className={`text-xs px-2 py-0.5 rounded ${val ? 'bg-success/10 text-success' : 'bg-error/10 text-error'}`}>
                                      {val ? '\u2713' : '\u2717'} {key}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Event timeline */}
                            {trade.events && trade.events.length > 0 && (
                              <div>
                                <div className="text-xs text-muted mb-1">Timeline:</div>
                                <div className="space-y-1">
                                  {trade.events.map(ev => (
                                    <div key={ev.id} className="flex items-center gap-2 text-xs">
                                      <span className="text-muted2 font-mono w-36">{ev.timestamp ? new Date(ev.timestamp).toLocaleString() : '—'}</span>
                                      <span className="text-text">{ev.event_type}</span>
                                      {ev.from_state && ev.to_state && <span className="text-muted">{ev.from_state} → {ev.to_state}</span>}
                                      {ev.notes && <span className="text-muted2">— {ev.notes}</span>}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Review summary */}
                            {trade.review && (
                              <div className="border-t border-border pt-2">
                                <div className="text-xs text-muted mb-1">Review (Grade: {trade.review.grade}/5)</div>
                                {trade.review.thesis_recap && <div className="text-xs text-text">{trade.review.thesis_recap}</div>}
                                {trade.review.followed_rules !== null && (
                                  <div className={`text-xs ${trade.review.followed_rules ? 'text-success' : 'text-error'}`}>
                                    {trade.review.followed_rules ? 'Followed rules' : 'Rules broken'}
                                  </div>
                                )}
                                {trade.review.what_to_improve && <div className="text-xs text-muted2 mt-1">{trade.review.what_to_improve}</div>}
                              </div>
                            )}

                            {/* Notes */}
                            {trade.notes && (
                              <div className="text-xs text-muted2 italic">{trade.notes}</div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
