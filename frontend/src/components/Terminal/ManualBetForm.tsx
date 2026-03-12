import { useState, useMemo } from 'react';
import { api } from '@/services/api';
import type { Provider } from '@/types';

const ACCENT_STYLES: Record<string, { checkbox: string; button: string }> = {
  tabValue: { checkbox: 'accent-tabValue', button: 'bg-tabValue' },
  tabPolymarket: { checkbox: 'accent-tabPolymarket', button: 'bg-tabPolymarket' },
  tabReverse: { checkbox: 'accent-tabReverse', button: 'bg-tabReverse' },
};

interface ManualBetFormProps {
  providers: Provider[];
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
  providerFilter?: (p: Provider) => boolean;
  accentColor?: string;
  betType?: string;
}

export function ManualBetForm({ providers, onSuccess, onError, providerFilter, accentColor = 'tabValue', betType = 'manual' }: ManualBetFormProps) {
  const styles = ACCENT_STYLES[accentColor] ?? ACCENT_STYLES.tabValue;
  const [providerId, setProviderId] = useState('');
  const [description, setDescription] = useState('');
  const [odds, setOdds] = useState('');
  const [stake, setStake] = useState('');
  const [isFreebet, setIsFreebet] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!providerId || !description || !odds || !stake) return;
    const oddsNum = parseFloat(odds);
    const stakeNum = parseFloat(stake);
    if (isNaN(oddsNum) || isNaN(stakeNum) || oddsNum <= 1 || stakeNum <= 0) {
      onError('Invalid odds or stake');
      return;
    }
    setIsSubmitting(true);
    try {
      await api.createBet({
        provider_id: providerId,
        outcome: description,
        odds: oddsNum,
        stake: stakeNum,
        bet_type: betType,
        is_bonus: isFreebet,
        bonus_type: isFreebet ? 'free_bet' : undefined,
      });
      onSuccess(`Manual bet logged: ${description} @ ${oddsNum} — ${stakeNum} kr`);
      setDescription('');
      setOdds('');
      setStake('');
      setIsFreebet(false);
    } catch (e: any) {
      onError(e.message || 'Failed to create bet');
    } finally {
      setIsSubmitting(false);
    }
  };

  const sortedProviders = useMemo(() => {
    let filtered = [...providers].filter(p => p.is_enabled);
    if (providerFilter) filtered = filtered.filter(providerFilter);
    return filtered.sort((a, b) => a.id.localeCompare(b.id));
  }, [providers, providerFilter]);

  return (
    <div className="p-4 max-w-md space-y-3">
      <div>
        <label className="block text-xs text-muted mb-1">Provider</label>
        <select
          value={providerId}
          onChange={e => setProviderId(e.target.value)}
          className="w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text"
        >
          <option value="">Select provider...</option>
          {sortedProviders.map(p => (
            <option key={p.id} value={p.id}>{p.id}</option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-xs text-muted mb-1">Description</label>
        <input
          type="text"
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="e.g. Den Helder Suns ML"
          className="w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text"
        />
      </div>
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs text-muted mb-1">Odds</label>
          <input
            type="number"
            step="0.01"
            value={odds}
            onChange={e => setOdds(e.target.value)}
            placeholder="2.50"
            className="w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text"
          />
        </div>
        <div className="flex-1">
          <label className="block text-xs text-muted mb-1">Stake (kr)</label>
          <input
            type="number"
            step="1"
            value={stake}
            onChange={e => setStake(e.target.value)}
            placeholder="500"
            className="w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm text-text"
          />
        </div>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
        <input type="checkbox" checked={isFreebet} onChange={e => setIsFreebet(e.target.checked)} className={styles.checkbox} />
        Freebet
      </label>
      <button
        onClick={handleSubmit}
        disabled={isSubmitting || !providerId || !description || !odds || !stake}
        className={`px-4 py-1.5 text-sm font-medium ${styles.button} text-black rounded hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed`}
      >
        {isSubmitting ? 'Logging...' : 'Log Bet'}
      </button>
    </div>
  );
}
