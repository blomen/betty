import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { MarketSession, TradingSignal, ConfirmationState } from '@/types/market';

type ConfirmationKey = 'macro' | 'span' | 'fair_value' | 'orderflow';

export function TradingIntradayPage() {
  const [session, setSession] = useState<MarketSession | null>(null);
  const [signals, setSignals] = useState<TradingSignal[]>([]);
  const [confirmations, setConfirmations] = useState<ConfirmationState | null>(null);
  const [overrides, setOverrides] = useState<Record<ConfirmationKey, boolean | null>>({
    macro: null, span: null, fair_value: null, orderflow: null,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isComputing, setIsComputing] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [threshold, setThreshold] = useState(70);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [takingTrade, setTakingTrade] = useState<number | null>(null);
  const [entryPrice, setEntryPrice] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [sessionRes, signalsRes, confirmRes] = await Promise.all([
        api.getMarketSession().catch(() => null),
        api.getMarketSignals().catch(() => ({ signals: [] })),
        api.getConfirmations().catch(() => null),
      ]);
      if (sessionRes && !sessionRes.status) setSession(sessionRes);
      setSignals(signalsRes.signals || []);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Failed to fetch market data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCompute = async () => {
    setIsComputing(true);
    try {
      const res = await api.triggerMarketCompute();
      if (res && !res.status) setSession(res);
      const confirmRes = await api.getConfirmations().catch(() => null);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Compute failed:', err);
    } finally {
      setIsComputing(false);
    }
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      const res = await api.triggerMarketScan(threshold);
      setSignals(res.signals || []);
      setLastScan(new Date().toLocaleTimeString());
      const confirmRes = await api.getConfirmations().catch(() => null);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Scan failed:', err);
    } finally {
      setIsScanning(false);
    }
  };

  const toggleOverride = (key: ConfirmationKey) => {
    setOverrides(prev => {
      const autoChecked = confirmations?.[key]?.checked ?? false;
      const current = prev[key];
      if (current === null) {
        return { ...prev, [key]: !autoChecked };
      }
      return { ...prev, [key]: null };
    });
  };

  const isChecked = (key: ConfirmationKey): boolean => {
    if (overrides[key] !== null) return overrides[key]!;
    return confirmations?.[key]?.checked ?? false;
  };

  const allConfirmed = (['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).every(isChecked);

  const handleTakeTrade = async (signal: TradingSignal) => {
    const price = parseFloat(entryPrice);
    if (!price || !signal) return;
    try {
      await api.createTrade({
        instrument: session?.symbol || 'NQ',
        direction: signal.direction,
        setup_type: signal.setup_type,
        entry_price: price,
        stop_price: signal.suggested_stop || 0,
        targets: signal.suggested_target ? [{ price: signal.suggested_target }] : [],
        contracts: 1,
        notes: `Scanner signal: ${signal.setup_name} (score: ${signal.score})`,
      });
      setTakingTrade(null);
      setEntryPrice('');
    } catch (err) {
      console.error('Failed to create trade:', err);
    }
  };

  if (isLoading) return <div className="text-muted text-sm">Loading scanner...</div>;

  const hasSession = session && session.poc;

  return (
    <div className="space-y-3 max-w-5xl">
      {/* A. FilterBar */}
      <div className="flex items-center gap-3 flex-wrap border-b border-border pb-2">
        <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={18} />
        <span className="text-sm font-semibold text-text">Intraday</span>
        <div className="flex-1" />
        <button
          onClick={handleCompute}
          disabled={isComputing}
          className="text-xs px-3 py-1 border border-tabTradingScanner/50 text-tabTradingScanner rounded hover:bg-tabTradingScanner/10 disabled:opacity-40"
        >
          {isComputing ? 'Computing...' : 'Compute'}
        </button>
        <button
          onClick={handleScan}
          disabled={isScanning || !hasSession}
          className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
        >
          {isScanning ? 'Scanning...' : 'Scan'}
        </button>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <label>Thr:</label>
          <input type="range" min={30} max={95} step={5} value={threshold}
            onChange={e => setThreshold(parseInt(e.target.value))}
            className="w-16 accent-[#06B6D4]" />
          <span className="font-mono text-text w-5">{threshold}</span>
        </div>
        {lastScan && <span className="text-[10px] text-muted">Last: {lastScan}</span>}
      </div>

      {/* B. Confirmation Strip */}
      <div className="grid grid-cols-4 gap-2">
        <ConfirmCard
          label="Macro"
          checked={isChecked('macro')}
          autoChecked={confirmations?.macro?.checked ?? false}
          overridden={overrides.macro !== null}
          onClick={() => toggleOverride('macro')}
          detail={confirmations?.macro?.regime === 'risk_on' ? 'RISK ON' :
                  confirmations?.macro?.regime === 'risk_off' ? 'RISK OFF' : 'MIXED'}
          subDetail={confirmations?.macro?.vix != null ? `VIX ${confirmations.macro.vix.toFixed(1)}` : undefined}
          detailColor={confirmations?.macro?.regime === 'risk_on' ? 'text-success' :
                       confirmations?.macro?.regime === 'risk_off' ? 'text-error' : 'text-yellow'}
        />
        <ConfirmCard
          label="Span"
          checked={isChecked('span')}
          autoChecked={confirmations?.span?.checked ?? false}
          overridden={overrides.span !== null}
          onClick={() => toggleOverride('span')}
          detail={confirmations?.span?.structure === 'bullish' ? 'Bullish structure' :
                  confirmations?.span?.structure === 'bearish' ? 'Bearish structure' : 'No clear structure'}
          detailColor={confirmations?.span?.checked ? 'text-success' : 'text-muted'}
        />
        <ConfirmCard
          label="Fair Value"
          checked={isChecked('fair_value')}
          autoChecked={confirmations?.fair_value?.checked ?? false}
          overridden={overrides.fair_value !== null}
          onClick={() => toggleOverride('fair_value')}
          detail={confirmations?.fair_value?.deviation_sd != null
            ? `${confirmations.fair_value.deviation_sd > 0 ? '+' : ''}${confirmations.fair_value.deviation_sd} SD`
            : confirmations?.fair_value?.price_vs_va || 'No data'}
          detailColor={confirmations?.fair_value?.checked ? 'text-tabTradingScanner' : 'text-muted'}
        />
        <ConfirmCard
          label="Orderflow"
          checked={isChecked('orderflow')}
          autoChecked={confirmations?.orderflow?.checked ?? false}
          overridden={overrides.orderflow !== null}
          onClick={() => toggleOverride('orderflow')}
          detail={confirmations?.orderflow?.delta != null
            ? `Delta ${confirmations.orderflow.delta > 0 ? '+' : ''}${confirmations.orderflow.delta.toLocaleString()}`
            : 'No data'}
          subDetail={confirmations?.orderflow?.divergence ? 'Divergence' : undefined}
          detailColor={confirmations?.orderflow?.checked ? 'text-success' : 'text-muted'}
        />
      </div>

      {/* C. Market State Row */}
      {hasSession && (
        <div className="flex items-center gap-3 text-xs font-mono flex-wrap px-1">
          {session.poc && <Badge label="POC" value={session.poc.toFixed(0)} />}
          {session.vah && session.val && <Badge label="VA" value={`${session.val.toFixed(0)}-${session.vah.toFixed(0)}`} />}
          {session.vwap && <Badge label="VWAP" value={session.vwap.toFixed(0)} color="text-warning" />}
          {session.ib_high && session.ib_low && <Badge label="IB" value={`${session.ib_low.toFixed(0)}-${session.ib_high.toFixed(0)}`} />}
          {session.overnight_high && session.overnight_low && <Badge label="ON" value={`${session.overnight_low.toFixed(0)}-${session.overnight_high.toFixed(0)}`} color="text-muted" />}
          {session.total_delta != null && (
            <Badge label="Delta"
              value={`${session.total_delta > 0 ? '+' : ''}${session.total_delta.toLocaleString()}`}
              color={session.total_delta > 0 ? 'text-success' : 'text-error'} />
          )}
          {session.last_price && <Badge label="Price" value={session.last_price.toFixed(2)} color="text-text" />}
        </div>
      )}

      {/* D. Gated Opportunity Table */}
      <div className="border border-border bg-panel rounded">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <h3 className="text-sm font-semibold text-text">
            Opportunities ({allConfirmed ? signals.length : 0})
          </h3>
          {!allConfirmed && (
            <span className="text-xs text-muted">
              {(['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).filter(k => !isChecked(k)).length} confirmation{(['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).filter(k => !isChecked(k)).length !== 1 ? 's' : ''} remaining
            </span>
          )}
        </div>

        {!allConfirmed ? (
          <div className="p-6 text-center text-muted text-sm">
            Waiting for confirmations...
          </div>
        ) : signals.length === 0 ? (
          <div className="p-4 text-center text-muted text-sm">
            {hasSession ? 'No signals above threshold.' : 'Compute session first, then scan.'}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {signals.map(sig => {
              const rr = sig.suggested_entry && sig.suggested_stop && sig.suggested_target
                ? Math.abs(sig.suggested_target - sig.suggested_entry) / Math.abs(sig.suggested_entry - sig.suggested_stop)
                : null;
              return (
                <div key={sig.id}>
                  <button
                    onClick={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-panel2/50 transition-colors"
                  >
                    <div className="w-10 flex-shrink-0">
                      <div className={`text-sm font-mono font-bold ${sig.score >= 80 ? 'text-success' : sig.score >= 70 ? 'text-tabTradingScanner' : 'text-warning'}`}>
                        {sig.score.toFixed(0)}
                      </div>
                      <div className="w-full bg-panel2 rounded-full h-1 mt-0.5">
                        <div className="h-1 rounded-full" style={{
                          width: `${sig.score}%`,
                          backgroundColor: sig.score >= 80 ? '#4CAF50' : sig.score >= 70 ? '#06B6D4' : '#FF9800'
                        }} />
                      </div>
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-text font-medium truncate">{sig.setup_name}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded border ${
                          sig.direction === 'long' ? 'border-success/50 text-success' : 'border-error/50 text-error'
                        }`}>
                          {sig.direction.toUpperCase()}
                        </span>
                      </div>
                    </div>

                    <div className="flex gap-3 text-xs text-muted flex-shrink-0">
                      {sig.suggested_entry && <span>E:<span className="font-mono text-text ml-0.5">{sig.suggested_entry.toFixed(0)}</span></span>}
                      {sig.suggested_stop && <span>S:<span className="font-mono text-error ml-0.5">{sig.suggested_stop.toFixed(0)}</span></span>}
                      {sig.suggested_target && <span>T:<span className="font-mono text-success ml-0.5">{sig.suggested_target.toFixed(0)}</span></span>}
                      {rr && <span>R:R <span className="font-mono text-tabTradingScanner">{rr.toFixed(1)}</span></span>}
                    </div>

                    <span className={`text-muted text-xs transition-transform ${expandedSignal === sig.id ? 'rotate-90' : ''}`}>▸</span>
                  </button>

                  {expandedSignal === sig.id && (
                    <div className="px-4 pb-3 space-y-2 bg-panel2/30">
                      {sig.conditions.map((c, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <div className="w-7 text-right font-mono text-muted">{Math.round(c.score * 100)}%</div>
                          <div className="w-16 bg-panel2 rounded-full h-1">
                            <div className="h-1 rounded-full" style={{
                              width: `${c.score * 100}%`,
                              backgroundColor: c.score >= 0.7 ? '#4CAF50' : c.score >= 0.4 ? '#FF9800' : '#EF5350'
                            }} />
                          </div>
                          <span className={c.is_auto ? 'text-text' : 'text-muted italic'}>{c.name}</span>
                          {!c.is_auto && <span className="text-[10px] text-muted/50">(manual)</span>}
                        </div>
                      ))}

                      {takingTrade === sig.id ? (
                        <div className="flex items-center gap-2 pt-2 border-t border-border">
                          <span className="text-xs text-muted">Fill price:</span>
                          <input
                            type="number"
                            step="0.25"
                            value={entryPrice}
                            onChange={e => setEntryPrice(e.target.value)}
                            placeholder={sig.suggested_entry?.toFixed(2) || ''}
                            className="bg-panel2 border border-border rounded px-2 py-1 text-sm font-mono text-text w-28"
                            autoFocus
                          />
                          <button
                            onClick={() => handleTakeTrade(sig)}
                            disabled={!entryPrice}
                            className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => { setTakingTrade(null); setEntryPrice(''); }}
                            className="text-xs px-2 py-1 text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="pt-2 border-t border-border">
                          <button
                            onClick={() => { setTakingTrade(sig.id); setEntryPrice(sig.suggested_entry?.toFixed(2) || ''); }}
                            className="text-xs px-4 py-1.5 bg-tabTradingScanner text-bg rounded hover:bg-tabTradingScanner/80 font-medium"
                          >
                            Take Trade
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function ConfirmCard({
  label, checked, autoChecked, overridden, onClick, detail, subDetail, detailColor,
}: {
  label: string;
  checked: boolean;
  autoChecked: boolean;
  overridden: boolean;
  onClick: () => void;
  detail: string;
  subDetail?: string;
  detailColor: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`border rounded p-2.5 text-left transition-colors ${
        checked
          ? 'border-tabTradingScanner/60 bg-tabTradingScanner/5'
          : 'border-border bg-panel hover:bg-panel2/50'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center text-[10px] ${
          checked ? 'border-tabTradingScanner bg-tabTradingScanner text-bg' : 'border-muted'
        }`}>
          {checked && '✓'}
        </div>
        <span className="text-xs font-medium text-text">{label}</span>
        {autoChecked && !overridden && (
          <span className="text-[9px] px-1 rounded bg-tabTradingScanner/20 text-tabTradingScanner ml-auto">auto</span>
        )}
        {overridden && (
          <span className="text-[9px] px-1 rounded bg-warning/20 text-warning ml-auto">override</span>
        )}
      </div>
      <div className={`text-xs font-mono ${detailColor}`}>{detail}</div>
      {subDetail && <div className="text-[10px] text-muted">{subDetail}</div>}
    </button>
  );
}

function Badge({ label, value, color = 'text-tabTradingScanner' }: { label: string; value: string; color?: string }) {
  return (
    <span className="text-muted">
      {label} <span className={`${color}`}>{value}</span>
    </span>
  );
}
