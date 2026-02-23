import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { DailyRoutine } from '@/types/trading';

export function TradingTodayPage() {
  const [routine, setRoutine] = useState<DailyRoutine | null>(null);
  const [routineConfig, setRoutineConfig] = useState<{ macro_items: string[]; session_items: string[]; psych_threshold: number } | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [r, cfg] = await Promise.all([api.getTodayRoutine(), api.getRoutineConfig()]);
      setRoutine(r);
      setRoutineConfig(cfg);
    } catch (err) {
      console.error('Failed to fetch routine:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const save = async (field: string, value: unknown) => {
    if (!routine) return;
    try {
      const res = await api.updateRoutine(routine.date, { [field]: value });
      if (res.success) setRoutine(res.routine);
    } catch (err) {
      console.error('Failed to save:', err);
    }
  };

  const toggleChecklist = (key: string) => {
    if (!routine) return;
    const current = routine.checklist_completion || {};
    const updated = { ...current, [key]: !current[key] };
    save('checklist_completion', updated);
  };

  const psychAvg = routine ? (() => {
    const scores = [routine.sleep_score, routine.focus_score, routine.emotional_score].filter((s): s is number => s !== null);
    return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  })() : null;

  const psychBelow = psychAvg !== null && routineConfig ? psychAvg < routineConfig.psych_threshold : false;

  if (isLoading) return <div className="text-muted text-sm">Loading today's routine...</div>;
  if (!routine || !routineConfig) return <div className="text-muted text-sm">No routine data</div>;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="tradingToday" color={TAB_COLORS.tradingToday} />
        Today — {routine.date}
        {routine.is_complete && <span className="text-success text-xs ml-2">Complete</span>}
      </h2>

      {/* Macro Scan */}
      <div className="border border-border bg-panel rounded p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Macro Scan</h3>
        <div className="space-y-2">
          {routineConfig.macro_items.map((item, i) => {
            const key = `macro_${i}`;
            const checked = routine.checklist_completion?.[key] ?? false;
            return (
              <label key={key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={checked} onChange={() => toggleChecklist(key)} className="accent-tabTradingToday" />
                <span className={checked ? 'text-muted line-through' : 'text-text'}>{item}</span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Session Context */}
      <div className="border border-border bg-panel rounded p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Session Context</h3>
        <div className="space-y-2">
          {routineConfig.session_items.map((item, i) => {
            const key = `session_${i}`;
            const checked = routine.checklist_completion?.[key] ?? false;
            return (
              <label key={key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={checked} onChange={() => toggleChecklist(key)} className="accent-tabTradingToday" />
                <span className={checked ? 'text-muted line-through' : 'text-text'}>{item}</span>
              </label>
            );
          })}
        </div>

        <div className="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-border">
          <div>
            <label className="text-xs text-muted block mb-1">Overnight High</label>
            <input
              type="number"
              defaultValue={routine.overnight_high ?? ''}
              onBlur={e => save('overnight_high', parseFloat(e.target.value) || null)}
              className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-full font-mono"
            />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Overnight Low</label>
            <input
              type="number"
              defaultValue={routine.overnight_low ?? ''}
              onBlur={e => save('overnight_low', parseFloat(e.target.value) || null)}
              className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-full font-mono"
            />
          </div>
        </div>
      </div>

      {/* Bias Hypothesis */}
      <div className="border border-border bg-panel rounded p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Bias Hypothesis</h3>
        <textarea
          defaultValue={routine.bias_text ?? ''}
          onBlur={e => save('bias_text', e.target.value || null)}
          placeholder="What is your market thesis for today?"
          className="bg-panel2 border border-border rounded px-3 py-2 text-sm text-text w-full font-mono resize-none h-16"
        />
        <div className="flex items-center gap-3 mt-3">
          <span className="text-xs text-muted">Direction:</span>
          {(['bullish', 'bearish', 'neutral'] as const).map(dir => (
            <button
              key={dir}
              onClick={() => save('bias_direction', dir)}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                routine.bias_direction === dir
                  ? dir === 'bullish' ? 'bg-success/20 border-success text-success'
                  : dir === 'bearish' ? 'bg-error/20 border-error text-error'
                  : 'bg-yellow/20 border-yellow text-yellow'
                  : 'border-border text-muted hover:text-text'
              }`}
            >
              {dir}
            </button>
          ))}
          <span className="text-xs text-muted ml-4">Confidence:</span>
          {[1, 2, 3, 4, 5].map(n => (
            <button
              key={n}
              onClick={() => save('bias_confidence', n)}
              className={`w-7 h-7 text-xs rounded border transition-colors ${
                routine.bias_confidence === n
                  ? 'bg-tabTradingToday/20 border-tabTradingToday text-tabTradingToday'
                  : 'border-border text-muted hover:text-text'
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {/* Psych Gate */}
      <div className={`border rounded p-4 ${psychBelow && !routine.psych_override ? 'border-error bg-error/5' : 'border-border bg-panel'}`}>
        <h3 className="text-sm font-semibold text-text mb-3">Psych Gate</h3>
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: 'Sleep', field: 'sleep_score' as const, value: routine.sleep_score },
            { label: 'Focus', field: 'focus_score' as const, value: routine.focus_score },
            { label: 'Emotional', field: 'emotional_score' as const, value: routine.emotional_score },
          ].map(item => (
            <div key={item.field}>
              <label className="text-xs text-muted block mb-2">{item.label}: {item.value ?? '—'}/10</label>
              <input
                type="range"
                min="1"
                max="10"
                value={item.value ?? 5}
                onChange={e => save(item.field, parseInt(e.target.value))}
                className="w-full accent-tabTradingToday"
              />
            </div>
          ))}
        </div>

        {psychAvg !== null && (
          <div className="mt-3 flex items-center gap-3">
            <span className={`text-sm font-mono ${psychBelow ? 'text-error' : 'text-success'}`}>
              Average: {psychAvg.toFixed(1)} / {routineConfig.psych_threshold}
            </span>
            {psychBelow && !routine.psych_override && (
              <span className="text-xs text-error">Below threshold — override required to trade</span>
            )}
            {psychBelow && routine.psych_override && (
              <span className="text-xs text-warning">Overridden: {routine.psych_override}</span>
            )}
          </div>
        )}

        {psychBelow && !routine.psych_override && (
          <div className="mt-3">
            <input
              type="text"
              placeholder="Type override reason to proceed..."
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  save('psych_override', (e.target as HTMLInputElement).value);
                }
              }}
              className="bg-panel2 border border-error/50 rounded px-3 py-1.5 text-sm text-text w-full font-mono"
            />
          </div>
        )}
      </div>
    </div>
  );
}
