import { useState, useEffect } from 'react';
import { X, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import type { Bet } from '@/types';
import { useBets } from '@/hooks/useBets';
import { useBankroll } from '@/hooks/useBankroll';

interface SettleBetModalProps {
  bet: Bet | null;
  isOpen: boolean;
  onClose: () => void;
}

export function SettleBetModal({ bet, isOpen, onClose }: SettleBetModalProps) {
  const { settleBet } = useBets();
  const { refresh: refreshBankroll } = useBankroll(0);

  const [result, setResult] = useState<'won' | 'lost' | 'void' | null>(null);
  const [payout, setPayout] = useState<number>(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && bet) {
      setResult(null);
      setPayout(0);
      setError(null);
    }
  }, [isOpen, bet]);

  useEffect(() => {
    if (bet && result) {
      // Auto-fill payout based on result
      switch (result) {
        case 'won':
          setPayout(bet.stake * bet.odds);
          break;
        case 'lost':
          setPayout(0);
          break;
        case 'void':
          setPayout(bet.stake);
          break;
      }
    }
  }, [result, bet]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      window.addEventListener('keydown', handleEsc);
      return () => window.removeEventListener('keydown', handleEsc);
    }
  }, [isOpen, onClose]);

  if (!isOpen || !bet) return null;

  const profit = payout - bet.stake;
  const roiPct = bet.stake > 0 ? (profit / bet.stake) * 100 : 0;

  const handleConfirm = async () => {
    if (!result) {
      setError('Please select a result');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await settleBet(bet.id, result, payout);
      await refreshBankroll();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to settle bet');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-[#0a0a0a] border border-[#00ff00]/30 rounded-lg max-w-lg w-full">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#00ff00]/20">
          <h2 className="text-lg font-mono text-[#00ff00]">Settle Bet</h2>
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
          {/* Bet Summary */}
          <div className="border border-[#00ff00]/20 rounded p-3">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-[#00ff00]/60 font-mono">Provider</div>
                <div className="font-mono text-[#00ff00]">{bet.provider}</div>
              </div>
              <div>
                <div className="text-xs text-[#00ff00]/60 font-mono">Market</div>
                <div className="font-mono text-[#00ff00]">{bet.market || 'N/A'}</div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-sm mt-2">
              <div>
                <div className="text-xs text-[#00ff00]/60 font-mono">Outcome</div>
                <div className="font-mono text-[#00ff00]">{bet.outcome || 'N/A'}</div>
              </div>
              <div>
                <div className="text-xs text-[#00ff00]/60 font-mono">Odds</div>
                <div className="font-mono text-[#00ff00]">{bet.odds.toFixed(2)}</div>
              </div>
              <div>
                <div className="text-xs text-[#00ff00]/60 font-mono">Stake</div>
                <div className="font-mono text-[#00ff00]">${bet.stake.toFixed(2)}</div>
              </div>
            </div>
          </div>

          {/* Result Selection */}
          <div>
            <label className="block text-xs text-[#00ff00]/60 font-mono mb-2">Result</label>
            <div className="grid grid-cols-3 gap-2">
              <button
                onClick={() => setResult('won')}
                className={`py-2 px-3 rounded font-mono text-sm flex items-center justify-center gap-2 transition-colors ${
                  result === 'won'
                    ? 'bg-[#00ff00] text-black'
                    : 'border border-[#00ff00]/30 text-[#00ff00] hover:bg-[#00ff00]/10'
                }`}
              >
                <CheckCircle className="w-4 h-4" />
                Won
              </button>
              <button
                onClick={() => setResult('lost')}
                className={`py-2 px-3 rounded font-mono text-sm flex items-center justify-center gap-2 transition-colors ${
                  result === 'lost'
                    ? 'bg-terminal-red/100 text-black'
                    : 'border border-red-500/30 text-terminal-red hover:bg-terminal-red/100/10'
                }`}
              >
                <XCircle className="w-4 h-4" />
                Lost
              </button>
              <button
                onClick={() => setResult('void')}
                className={`py-2 px-3 rounded font-mono text-sm flex items-center justify-center gap-2 transition-colors ${
                  result === 'void'
                    ? 'bg-gray-500 text-black'
                    : 'border border-terminal-border/30 text-terminal-muted hover:bg-terminal-surface500/10'
                }`}
              >
                <AlertCircle className="w-4 h-4" />
                Void
              </button>
            </div>
          </div>

          {/* Payout Input */}
          {result && (
            <div>
              <label className="block text-xs text-[#00ff00]/60 font-mono mb-1">
                Payout (auto-filled, adjust if needed)
              </label>
              <input
                type="number"
                value={payout || ''}
                onChange={(e) => setPayout(parseFloat(e.target.value) || 0)}
                step="0.01"
                min="0"
                className="w-full bg-terminal-bg border border-[#00ff00]/30 text-[#00ff00] px-3 py-2 rounded font-mono focus:outline-none focus:border-[#00ff00]"
              />
            </div>
          )}

          {/* Profit Preview */}
          {result && (
            <div className="border border-[#00ff00]/20 rounded p-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-xs text-[#00ff00]/60 font-mono">Profit</div>
                  <div
                    className={`font-mono text-lg ${profit >= 0 ? 'text-[#00ff00]' : 'text-terminal-red'}`}
                  >
                    {profit >= 0 ? '+' : ''}${profit.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-[#00ff00]/60 font-mono">ROI</div>
                  <div
                    className={`font-mono text-lg ${roiPct >= 0 ? 'text-[#00ff00]' : 'text-terminal-red'}`}
                  >
                    {roiPct >= 0 ? '+' : ''}
                    {roiPct.toFixed(1)}%
                  </div>
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
            This will update your balance
          </p>
          <button
            onClick={handleConfirm}
            disabled={isSubmitting || !result}
            className="px-4 py-2 bg-[#00ff00] text-black font-mono rounded hover:bg-[#00ff00]/90 disabled:bg-[#00ff00]/30 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? 'Settling...' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}
