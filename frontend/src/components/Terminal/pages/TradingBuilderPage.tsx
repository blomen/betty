import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { TradingAccount, TradingConfig, TradeValidation, DailyRoutine } from '@/types/trading';

export function TradingBuilderPage() {
  const [accounts, setAccounts] = useState<TradingAccount[]>([]);
  const [config, setConfig] = useState<TradingConfig | null>(null);
  const [routine, setRoutine] = useState<DailyRoutine | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Form state
  const [accountId, setAccountId] = useState<number | null>(null);
  const [instrument, setInstrument] = useState('');
  const [direction, setDirection] = useState<'long' | 'short'>('long');
  const [setupType, setSetupType] = useState('');
  const [entryPrice, setEntryPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [targets, setTargets] = useState<string[]>(['']);
  const [contracts, setContracts] = useState('');
  const [confirmations, setConfirmations] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState('');

  // Validation
  const [validation, setValidation] = useState<TradeValidation | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [acctRes, cfg, routineRes] = await Promise.all([
        api.getTradingAccounts(),
        api.getTradingConfig(),
        api.getTodayRoutine(),
      ]);
      setAccounts(acctRes.accounts);
      setConfig(cfg);
      setRoutine(routineRes);
      if (acctRes.accounts.length && !accountId) setAccountId(acctRes.accounts[0].id);
      if (cfg && !instrument) {
        const keys = Object.keys(cfg.instruments);
        if (keys.length) setInstrument(keys[0]);
      }
    } catch (err) {
      console.error('Failed to fetch config:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Reset confirmations when setup changes
  useEffect(() => {
    if (!config || !setupType) { setConfirmations({}); return; }
    const setup = config.setups[setupType];
    if (!setup) { setConfirmations({}); return; }
    const init: Record<string, boolean> = {};
    setup.confirmations.forEach(c => { init[c] = false; });
    setConfirmations(init);
  }, [setupType, config]);

  const setupGroups = useMemo(() => {
    if (!config) return {};
    const groups: Record<string, { key: string; name: string }[]> = {};
    Object.entries(config.setups).forEach(([key, setup]) => {
      const cat = setup.category || 'other';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push({ key, name: setup.name });
    });
    return groups;
  }, [config]);

  const dryRun = async () => {
    if (!accountId) return;
    const data = buildPayload(true);
    const res = await api.createTrade(data);
    setValidation(res);
  };

  const handleSubmit = async () => {
    if (!accountId) return;
    setSubmitting(true);
    setSuccessMsg('');
    try {
      const data = buildPayload(false);
      const res = await api.createTrade(data);
      if (res.success) {
        setSuccessMsg(`Trade #${res.trade_id} created`);
        resetForm();
      }
      setValidation(res);
    } finally {
      setSubmitting(false);
    }
  };

  const buildPayload = (dryRun: boolean) => ({
    account_id: accountId!,
    instrument,
    direction,
    setup_type: setupType,
    entry_price: entryPrice ? parseFloat(entryPrice) : null,
    stop_price: stopPrice ? parseFloat(stopPrice) : null,
    targets: targets.filter(t => t).map(t => ({ price: parseFloat(t) })),
    contracts: contracts ? parseInt(contracts) : 1,
    confirmations,
    notes: notes || null,
    dry_run: dryRun,
  });

  const resetForm = () => {
    setEntryPrice('');
    setStopPrice('');
    setTargets(['']);
    setContracts('');
    setConfirmations({});
    setNotes('');
    setValidation(null);
  };

  if (isLoading || !config) return <div className="text-muted text-sm">Loading builder...</div>;

  const hasErrors = validation?.errors && validation.errors.length > 0;
  const hasWarnings = validation?.warnings && validation.warnings.length > 0;
  const routineIncomplete = !routine || !routine.is_complete;

  return (
    <div className="space-y-4 max-w-2xl">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="tradingBuilder" color={TAB_COLORS.tradingBuilder} />
        Trade Builder
      </h2>

      {routineIncomplete && (
        <div className="bg-error/10 border border-error text-error text-sm p-3 rounded flex items-center gap-2">
          <span className="text-lg">🚫</span>
          <div>
            <div className="font-semibold">Daily routine not completed</div>
            <div className="text-xs text-error/70">Go to the Today tab and complete your pre-market checklist before creating trades.</div>
          </div>
        </div>
      )}

      {successMsg && (
        <div className="bg-success/10 border border-success text-success text-sm p-3 rounded">{successMsg}</div>
      )}

      {/* Step 1: Account + Instrument + Direction + Setup */}
      <div className="border border-border bg-panel rounded p-4 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">Account</label>
            <div className="flex gap-1">
              {accounts.map(a => (
                <button
                  key={a.id}
                  onClick={() => setAccountId(a.id)}
                  className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                    accountId === a.id ? 'bg-tabTradingBuilder/20 border-tabTradingBuilder text-tabTradingBuilder' : 'border-border text-muted hover:text-text'
                  }`}
                >
                  {a.name}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Direction</label>
            <div className="flex gap-1">
              {(['long', 'short'] as const).map(d => (
                <button
                  key={d}
                  onClick={() => setDirection(d)}
                  className={`text-xs px-4 py-1.5 rounded border transition-colors ${
                    direction === d
                      ? d === 'long' ? 'bg-success/20 border-success text-success' : 'bg-error/20 border-error text-error'
                      : 'border-border text-muted hover:text-text'
                  }`}
                >
                  {d.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">Instrument</label>
            <select value={instrument} onChange={e => setInstrument(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text w-full">
              {Object.entries(config.instruments).map(([key, inst]) => (
                <option key={key} value={key}>{key} — {inst.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Setup</label>
            <select value={setupType} onChange={e => setSetupType(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text w-full">
              <option value="">Select setup...</option>
              {Object.entries(setupGroups).map(([cat, setups]) => (
                <optgroup key={cat} label={cat.replace('_', ' ').toUpperCase()}>
                  {setups.map(s => <option key={s.key} value={s.key}>{s.name}</option>)}
                </optgroup>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Step 2: Levels */}
      <div className="border border-border bg-panel rounded p-4 space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">Entry Price</label>
            <input type="number" step="0.25" value={entryPrice} onChange={e => setEntryPrice(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text w-full font-mono" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Stop Price</label>
            <input type="number" step="0.25" value={stopPrice} onChange={e => setStopPrice(e.target.value)} className="bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text w-full font-mono" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Contracts</label>
            <input type="number" value={contracts} onChange={e => setContracts(e.target.value)} placeholder="Auto" className="bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text w-full font-mono" />
          </div>
        </div>

        <div>
          <label className="text-xs text-muted block mb-1">Targets</label>
          {targets.map((t, i) => (
            <div key={i} className="flex gap-2 mb-1">
              <input
                type="number"
                step="0.25"
                value={t}
                onChange={e => { const u = [...targets]; u[i] = e.target.value; setTargets(u); }}
                placeholder={`Target ${i + 1}`}
                className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text flex-1 font-mono"
              />
              {targets.length > 1 && (
                <button onClick={() => setTargets(targets.filter((_, j) => j !== i))} className="text-xs text-error px-2">Remove</button>
              )}
            </div>
          ))}
          <button onClick={() => setTargets([...targets, ''])} className="text-xs text-tabTradingBuilder hover:underline mt-1">+ Add target</button>
        </div>
      </div>

      {/* Step 3: Confirmations */}
      {setupType && config.setups[setupType] && (
        <div className="border border-border bg-panel rounded p-4">
          <h3 className="text-sm font-semibold text-text mb-2">Confirmations — {config.setups[setupType].name}</h3>
          <p className="text-xs text-muted mb-3">{config.setups[setupType].description}</p>
          <div className="space-y-2">
            {config.setups[setupType].confirmations.map(c => (
              <label key={c} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={confirmations[c] ?? false} onChange={() => setConfirmations({ ...confirmations, [c]: !confirmations[c] })} className="accent-tabTradingBuilder" />
                <span className={confirmations[c] ? 'text-muted line-through' : 'text-text'}>{c}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Notes */}
      <div className="border border-border bg-panel rounded p-4">
        <label className="text-xs text-muted block mb-1">Notes</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Optional trade notes..." className="bg-panel2 border border-border rounded px-3 py-2 text-sm text-text w-full font-mono resize-none h-12" />
      </div>

      {/* Sizing panel */}
      {validation?.sizing && Object.keys(validation.sizing).length > 0 && (
        <div className="border border-border bg-panel rounded p-4">
          <h3 className="text-sm font-semibold text-text mb-2">Position Sizing</h3>
          <div className="grid grid-cols-5 gap-3 text-center">
            {validation.sizing.suggested_contracts != null && <div><div className="text-xs text-muted">Suggested</div><div className="text-sm font-mono text-text">{validation.sizing.suggested_contracts} ct</div></div>}
            {validation.sizing.risk_per_contract != null && <div><div className="text-xs text-muted">Risk/ct</div><div className="text-sm font-mono text-text">${validation.sizing.risk_per_contract}</div></div>}
            {validation.sizing.total_risk != null && <div><div className="text-xs text-muted">Total Risk</div><div className="text-sm font-mono text-error">${validation.sizing.total_risk}</div></div>}
            {validation.sizing.max_risk_dollars != null && <div><div className="text-xs text-muted">Max Risk</div><div className="text-sm font-mono text-text">${validation.sizing.max_risk_dollars}</div></div>}
            {validation.sizing.rr_ratio != null && <div><div className="text-xs text-muted">R:R</div><div className="text-sm font-mono text-success">{validation.sizing.rr_ratio}</div></div>}
          </div>
        </div>
      )}

      {/* Errors / Warnings */}
      {hasErrors && (
        <div className="bg-error/10 border border-error rounded p-3 space-y-1">
          {validation!.errors.map((e, i) => <div key={i} className="text-sm text-error">{e}</div>)}
        </div>
      )}
      {hasWarnings && (
        <div className="bg-warning/10 border border-warning rounded p-3 space-y-1">
          {validation!.warnings.map((w, i) => <div key={i} className="text-sm text-warning">{w}</div>)}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button onClick={dryRun} className="text-sm px-4 py-2 border border-border text-muted hover:text-text rounded">
          Validate
        </button>
        <button
          onClick={handleSubmit}
          disabled={submitting || !accountId || !instrument || !setupType || routineIncomplete}
          className="text-sm px-6 py-2 bg-tabTradingBuilder/20 border border-tabTradingBuilder text-tabTradingBuilder rounded hover:bg-tabTradingBuilder/30 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {submitting ? 'Creating...' : routineIncomplete ? 'Complete Routine First' : 'Create Trade'}
        </button>
      </div>
    </div>
  );
}
