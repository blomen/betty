import { useEffect } from 'react';
import { X, AlertTriangle } from 'lucide-react';
import type { BankrollExposure } from '@/types';

interface BalanceBreakdownModalProps {
  exposure: BankrollExposure;
  isOpen: boolean;
  onClose: () => void;
}

export function BalanceBreakdownModal({ exposure, isOpen, onClose }: BalanceBreakdownModalProps) {
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      window.addEventListener('keydown', handleEsc);
      return () => window.removeEventListener('keydown', handleEsc);
    }
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-[#0a0a0a] border border-[#00ff00]/30 rounded-lg max-w-2xl w-full max-h-[80vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#00ff00]/20">
          <h2 className="text-lg font-mono text-[#00ff00]">Balance Breakdown</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[#00ff00]/10 rounded transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-[#00ff00]" />
          </button>
        </div>

        {/* Summary */}
        <div className="p-4 border-b border-[#00ff00]/20">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <div className="text-xs text-[#00ff00]/60 font-mono mb-1">TOTAL BALANCE</div>
              <div className="text-xl font-mono text-[#00ff00]">
                ${exposure.total_balance.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-yellow-500/60 font-mono mb-1">PENDING</div>
              <div className="text-xl font-mono text-yellow-500">
                ${exposure.total_pending.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-[#00ff00]/60 font-mono mb-1">AVAILABLE</div>
              <div className="text-xl font-mono text-[#00ff00]">
                ${exposure.total_available.toFixed(2)}
              </div>
            </div>
          </div>
        </div>

        {/* Per-Provider Breakdown */}
        <div className="p-4 space-y-3">
          {exposure.providers.map((provider) => {
            const isLowBalance = provider.available < 10;
            const hasPending = provider.pending_bets_count > 0;

            return (
              <div
                key={provider.provider_id}
                className="border border-[#00ff00]/20 rounded p-3 hover:border-[#00ff00]/40 transition-colors"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="font-mono text-[#00ff00]">{provider.provider_name}</h3>
                    {isLowBalance && (
                      <AlertTriangle className="w-4 h-4 text-yellow-500" />
                    )}
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-mono text-[#00ff00]">
                      ${provider.total_balance.toFixed(2)}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-xs text-[#00ff00]/60 font-mono">Available</div>
                    <div className="font-mono text-[#00ff00]">${provider.available.toFixed(2)}</div>
                  </div>
                  {hasPending && (
                    <div>
                      <div className="text-xs text-yellow-500/60 font-mono">
                        Pending ({provider.pending_bets_count} bets)
                      </div>
                      <div className="font-mono text-yellow-500">
                        ${provider.pending_exposure.toFixed(2)}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-[#00ff00]/20 text-center">
          <p className="text-xs text-[#00ff00]/60 font-mono">
            Press ESC to close
          </p>
        </div>
      </div>
    </div>
  );
}
