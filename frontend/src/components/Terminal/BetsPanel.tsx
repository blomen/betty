import { useState, useEffect } from 'react';
import { X, Clock, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { useBets } from '@/hooks/useBets';
import type { Bet } from '@/types';

interface BetsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onSettleBet: (bet: Bet) => void;
}

export function BetsPanel({ isOpen, onClose, onSettleBet }: BetsPanelProps) {
  const [statusFilter, setStatusFilter] = useState<'pending' | 'won' | 'lost' | 'void' | undefined>();
  const { bets, count, isLoading, error } = useBets(statusFilter, 10000);

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

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'pending':
        return <Clock className="w-4 h-4 text-yellow-500" />;
      case 'won':
        return <CheckCircle className="w-4 h-4 text-[#00ff00]" />;
      case 'lost':
        return <XCircle className="w-4 h-4 text-red-500" />;
      case 'void':
        return <AlertCircle className="w-4 h-4 text-gray-500" />;
      default:
        return null;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'text-yellow-500';
      case 'won':
        return 'text-[#00ff00]';
      case 'lost':
        return 'text-red-500';
      case 'void':
        return 'text-gray-500';
      default:
        return 'text-[#00ff00]/60';
    }
  };

  return (
    <div className="fixed inset-0 bg-black/90 flex flex-col z-50">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-[#00ff00]/30">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-mono text-[#00ff00]">Bets</h2>
          <span className="text-sm font-mono text-[#00ff00]/60">({count} total)</span>
        </div>
        <button
          onClick={onClose}
          className="p-2 hover:bg-[#00ff00]/10 rounded transition-colors"
          aria-label="Close"
        >
          <X className="w-5 h-5 text-[#00ff00]" />
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-2 p-4 border-b border-[#00ff00]/20 bg-[#0a0a0a]">
        <button
          onClick={() => setStatusFilter(undefined)}
          className={`px-3 py-1 rounded font-mono text-sm transition-colors ${
            statusFilter === undefined
              ? 'bg-[#00ff00] text-black'
              : 'border border-[#00ff00]/30 text-[#00ff00] hover:bg-[#00ff00]/10'
          }`}
        >
          All
        </button>
        <button
          onClick={() => setStatusFilter('pending')}
          className={`px-3 py-1 rounded font-mono text-sm transition-colors ${
            statusFilter === 'pending'
              ? 'bg-yellow-500 text-black'
              : 'border border-yellow-500/30 text-yellow-500 hover:bg-yellow-500/10'
          }`}
        >
          Pending
        </button>
        <button
          onClick={() => setStatusFilter('won')}
          className={`px-3 py-1 rounded font-mono text-sm transition-colors ${
            statusFilter === 'won'
              ? 'bg-[#00ff00] text-black'
              : 'border border-[#00ff00]/30 text-[#00ff00] hover:bg-[#00ff00]/10'
          }`}
        >
          Won
        </button>
        <button
          onClick={() => setStatusFilter('lost')}
          className={`px-3 py-1 rounded font-mono text-sm transition-colors ${
            statusFilter === 'lost'
              ? 'bg-red-500 text-black'
              : 'border border-red-500/30 text-red-500 hover:bg-red-500/10'
          }`}
        >
          Lost
        </button>
        <button
          onClick={() => setStatusFilter('void')}
          className={`px-3 py-1 rounded font-mono text-sm transition-colors ${
            statusFilter === 'void'
              ? 'bg-gray-500 text-black'
              : 'border border-gray-500/30 text-gray-500 hover:bg-gray-500/10'
          }`}
        >
          Void
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="text-center py-8">
            <div className="text-[#00ff00]/60 font-mono">Loading bets...</div>
          </div>
        ) : error ? (
          <div className="text-center py-8">
            <div className="text-red-500 font-mono">{error}</div>
          </div>
        ) : bets.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-[#00ff00]/60 font-mono">No bets found</div>
          </div>
        ) : (
          <div className="grid gap-3 max-w-4xl mx-auto">
            {bets.map((bet) => {
              const isPending = bet.result === 'pending';

              return (
                <div
                  key={bet.id}
                  className="border border-[#00ff00]/30 rounded p-4 hover:border-[#00ff00] transition-colors"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      {getStatusIcon(bet.result)}
                      <span className={`text-sm font-mono uppercase ${getStatusColor(bet.result)}`}>
                        {bet.result}
                      </span>
                      {bet.is_bonus && (
                        <span className="px-2 py-0.5 bg-purple-500/20 text-purple-400 border border-purple-500/30 rounded text-xs font-mono">
                          BONUS
                        </span>
                      )}
                    </div>
                    {isPending && (
                      <button
                        onClick={() => onSettleBet(bet)}
                        className="px-3 py-1 bg-[#00ff00] text-black font-mono text-sm rounded hover:bg-[#00ff00]/90 transition-colors"
                      >
                        Settle
                      </button>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-sm mb-3">
                    <div>
                      <div className="text-xs text-[#00ff00]/60 font-mono">Provider</div>
                      <div className="font-mono text-[#00ff00]">{bet.provider}</div>
                    </div>
                    <div>
                      <div className="text-xs text-[#00ff00]/60 font-mono">Market</div>
                      <div className="font-mono text-[#00ff00]">{bet.market || 'N/A'}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-3 text-sm mb-3">
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

                  {!isPending && (
                    <div className="grid grid-cols-3 gap-3 text-sm mb-3 pt-3 border-t border-[#00ff00]/20">
                      <div>
                        <div className="text-xs text-[#00ff00]/60 font-mono">Payout</div>
                        <div className="font-mono text-[#00ff00]">${bet.payout.toFixed(2)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#00ff00]/60 font-mono">Profit</div>
                        <div
                          className={`font-mono ${
                            bet.profit > 0 ? 'text-[#00ff00]' : 'text-red-500'
                          }`}
                        >
                          {bet.profit >= 0 ? '+' : ''}${bet.profit.toFixed(2)}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-[#00ff00]/60 font-mono">ROI</div>
                        <div
                          className={`font-mono ${
                            bet.roi_pct > 0 ? 'text-[#00ff00]' : 'text-red-500'
                          }`}
                        >
                          {bet.roi_pct >= 0 ? '+' : ''}
                          {bet.roi_pct.toFixed(1)}%
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="text-xs text-[#00ff00]/40 font-mono">
                    Placed: {new Date(bet.placed_at).toLocaleString()}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-[#00ff00]/30 text-center bg-[#0a0a0a]">
        <p className="text-xs text-[#00ff00]/60 font-mono">
          Press ESC to close | Auto-refresh every 10s
        </p>
      </div>
    </div>
  );
}
