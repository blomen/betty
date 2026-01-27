import { useState, useEffect } from 'react';
import { X, AlertCircle } from 'lucide-react';
import type { OpportunityWithEvent } from '@/types';
import { useBankroll } from '@/hooks/useBankroll';
import { useBets } from '@/hooks/useBets';

interface BetPlacementModalProps {
  opportunity: OpportunityWithEvent | null;
  isOpen: boolean;
  onClose: () => void;
}

export function BetPlacementModal({
  opportunity,
  isOpen,
  onClose,
}: BetPlacementModalProps) {
  const { exposure, refresh: refreshBankroll } = useBankroll(0);
  const { createBet } = useBets();

  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [stake, setStake] = useState<number>(0);
  const [isBonus, setIsBonus] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && opportunity) {
      setSelectedProvider(opportunity.provider1);
      setStake(0);
      setIsBonus(false);
      setError(null);

      // Auto-calculate recommended Kelly stake for value bets
      if (opportunity.type === 'value' && opportunity.edge_pct) {
        const providerExposure = exposure.providers.find(
          (p) => p.provider_id === opportunity.provider1
        );
        if (providerExposure) {
          // Simple Kelly: (edge% / 100) * bankroll
          const recommendedStake = (opportunity.edge_pct / 100) * providerExposure.available;
          setStake(Math.max(1, Math.min(recommendedStake, providerExposure.available)));
        }
      }
    }
  }, [isOpen, opportunity, exposure]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      window.addEventListener('keydown', handleEsc);
      return () => window.removeEventListener('keydown', handleEsc);
    }
  }, [isOpen, onClose]);

  if (!isOpen || !opportunity) return null;

  const providerExposure = exposure.providers.find((p) => p.provider_id === selectedProvider);
  const selectedOdds =
    selectedProvider === opportunity.provider1 ? opportunity.odds1 : opportunity.odds2 || 0;
  const selectedOutcome =
    selectedProvider === opportunity.provider1 ? opportunity.outcome1 : opportunity.outcome2 || '';

  const potentialReturn = stake * selectedOdds;
  const potentialProfit = potentialReturn - stake;

  const hasInsufficientBalance = !isBonus && providerExposure && stake > providerExposure.available;

  const handleConfirm = async () => {
    if (!providerExposure) {
      setError('Provider not found');
      return;
    }

    if (hasInsufficientBalance) {
      setError(`Insufficient balance: ${providerExposure.available.toFixed(2)} available`);
      return;
    }

    if (stake <= 0) {
      setError('Stake must be greater than 0');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await createBet({
        event_id: opportunity.event_id,
        provider_id: selectedProvider,
        market: opportunity.market,
        outcome: selectedOutcome,
        odds: selectedOdds,
        stake,
        is_bonus: isBonus,
      });

      await refreshBankroll();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to place bet');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-[#0a0a0a] border border-[#00ff00]/30 rounded-lg max-w-lg w-full">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#00ff00]/20">
          <h2 className="text-lg font-mono text-[#00ff00]">Place Bet</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[#00ff00]/10 rounded transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-[#00ff00]" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Event Details */}
          {opportunity.event && (
            <div className="border border-[#00ff00]/20 rounded p-3">
              <div className="text-sm font-mono text-[#00ff00] mb-1">
                {opportunity.event.home_team} vs {opportunity.event.away_team}
              </div>
              <div className="text-xs text-[#00ff00]/60 font-mono">
                {opportunity.event.sport} - {opportunity.event.league}
              </div>
            </div>
          )}

          {/* Provider Selection */}
          <div>
            <label className="block text-xs text-[#00ff00]/60 font-mono mb-1">Provider</label>
            <select
              value={selectedProvider}
              onChange={(e) => setSelectedProvider(e.target.value)}
              className="w-full bg-terminal-bg border border-[#00ff00]/30 text-[#00ff00] px-3 py-2 rounded font-mono focus:outline-none focus:border-[#00ff00]"
            >
              <option value={opportunity.provider1}>{opportunity.provider1}</option>
              {opportunity.provider2 && (
                <option value={opportunity.provider2}>{opportunity.provider2}</option>
              )}
            </select>
          </div>

          {/* Outcome & Odds */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-[#00ff00]/60 font-mono mb-1">Outcome</label>
              <div className="bg-terminal-bg border border-[#00ff00]/30 text-[#00ff00] px-3 py-2 rounded font-mono">
                {selectedOutcome}
              </div>
            </div>
            <div>
              <label className="block text-xs text-[#00ff00]/60 font-mono mb-1">Odds</label>
              <div className="bg-terminal-bg border border-[#00ff00]/30 text-[#00ff00] px-3 py-2 rounded font-mono">
                {selectedOdds.toFixed(2)}
              </div>
            </div>
          </div>

          {/* Stake Input */}
          <div>
            <label className="block text-xs text-[#00ff00]/60 font-mono mb-1">
              Stake
              {providerExposure && (
                <span className="ml-2">
                  (Available: ${providerExposure.available.toFixed(2)})
                </span>
              )}
            </label>
            <input
              type="number"
              value={stake || ''}
              onChange={(e) => setStake(parseFloat(e.target.value) || 0)}
              step="0.01"
              min="0"
              className={`w-full bg-terminal-bg border px-3 py-2 rounded font-mono focus:outline-none ${
                hasInsufficientBalance
                  ? 'border-red-500 text-terminal-red'
                  : 'border-[#00ff00]/30 text-[#00ff00] focus:border-[#00ff00]'
              }`}
            />
            {hasInsufficientBalance && (
              <div className="flex items-center gap-2 mt-1 text-xs text-terminal-red font-mono">
                <AlertCircle className="w-3 h-3" />
                Insufficient balance
              </div>
            )}
          </div>

          {/* Bonus Bet Checkbox */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isBonus}
              onChange={(e) => setIsBonus(e.target.checked)}
              className="w-4 h-4"
            />
            <span className="text-sm text-[#00ff00] font-mono">Free Bet / Bonus Bet</span>
          </label>

          {/* Potential Return */}
          {stake > 0 && (
            <div className="border border-[#00ff00]/20 rounded p-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-xs text-[#00ff00]/60 font-mono">Potential Return</div>
                  <div className="font-mono text-[#00ff00]">${potentialReturn.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-xs text-[#00ff00]/60 font-mono">Potential Profit</div>
                  <div className="font-mono text-[#00ff00]">${potentialProfit.toFixed(2)}</div>
                </div>
              </div>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className="flex items-center gap-2 p-3 bg-terminal-red/100/10 border border-red-500/30 rounded">
              <AlertCircle className="w-4 h-4 text-terminal-red" />
              <span className="text-sm text-terminal-red font-mono">{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-[#00ff00]/20 flex items-center justify-between">
          <p className="text-xs text-[#00ff00]/60 font-mono">
            Place bet manually at bookmaker, then confirm
          </p>
          <button
            onClick={handleConfirm}
            disabled={isSubmitting || hasInsufficientBalance || stake <= 0}
            className="px-4 py-2 bg-[#00ff00] text-black font-mono rounded hover:bg-[#00ff00]/90 disabled:bg-[#00ff00]/30 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? 'Confirming...' : 'Confirm Bet'}
          </button>
        </div>
      </div>
    </div>
  );
}
