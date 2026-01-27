import { useState, useEffect } from 'react';
import { X, TrendingUp, ArrowLeftRight, Gift, Filter } from 'lucide-react';
import { useOpportunities } from '@/hooks/useOpportunities';
import type { OpportunityWithEvent } from '@/types';

interface OpportunitiesOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectOpportunity: (opportunity: OpportunityWithEvent) => void;
}

export function OpportunitiesOverlay({
  isOpen,
  onClose,
  onSelectOpportunity,
}: OpportunitiesOverlayProps) {
  const [typeFilter, setTypeFilter] = useState<'arbitrage' | 'value' | 'bonus' | undefined>();
  const [sportFilter, setSportFilter] = useState<string>('');
  const [minValueFilter, setMinValueFilter] = useState<number>(0);

  const { opportunities, count, isLoading, error } = useOpportunities({
    type: typeFilter,
    sport: sportFilter || undefined,
    minValue: minValueFilter > 0 ? minValueFilter : undefined,
  });

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

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'arbitrage':
        return <ArrowLeftRight className="w-4 h-4" />;
      case 'value':
        return <TrendingUp className="w-4 h-4" />;
      case 'bonus':
        return <Gift className="w-4 h-4" />;
      default:
        return null;
    }
  };

  const getTypeBadgeColor = (type: string) => {
    switch (type) {
      case 'arbitrage':
        return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
      case 'value':
        return 'bg-[#00ff00]/20 text-[#00ff00] border-[#00ff00]/30';
      case 'bonus':
        return 'bg-purple-500/20 text-purple-400 border-purple-500/30';
      default:
        return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
    }
  };

  return (
    <div className="fixed inset-0 bg-black/90 flex flex-col z-50">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-[#00ff00]/30">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-mono text-[#00ff00]">Opportunities</h2>
          <span className="text-sm font-mono text-[#00ff00]/60">({count} found)</span>
        </div>
        <button
          onClick={onClose}
          className="p-2 hover:bg-[#00ff00]/10 rounded transition-colors"
          aria-label="Close"
        >
          <X className="w-5 h-5 text-[#00ff00]" />
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 p-4 border-b border-[#00ff00]/20 bg-[#0a0a0a]">
        <Filter className="w-4 h-4 text-[#00ff00]/60" />

        {/* Type Filter */}
        <select
          value={typeFilter || ''}
          onChange={(e) => setTypeFilter(e.target.value as any || undefined)}
          className="bg-black border border-[#00ff00]/30 text-[#00ff00] px-3 py-1 rounded font-mono text-sm focus:outline-none focus:border-[#00ff00]"
        >
          <option value="">All Types</option>
          <option value="arbitrage">Arbitrage</option>
          <option value="value">Value</option>
          <option value="bonus">Bonus</option>
        </select>

        {/* Sport Filter */}
        <input
          type="text"
          placeholder="Sport (e.g., football)"
          value={sportFilter}
          onChange={(e) => setSportFilter(e.target.value)}
          className="bg-black border border-[#00ff00]/30 text-[#00ff00] px-3 py-1 rounded font-mono text-sm focus:outline-none focus:border-[#00ff00] placeholder-[#00ff00]/40"
        />

        {/* Min Value Filter */}
        <input
          type="number"
          placeholder="Min %"
          value={minValueFilter || ''}
          onChange={(e) => setMinValueFilter(parseFloat(e.target.value) || 0)}
          className="bg-black border border-[#00ff00]/30 text-[#00ff00] px-3 py-1 rounded font-mono text-sm w-24 focus:outline-none focus:border-[#00ff00] placeholder-[#00ff00]/40"
        />
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="text-center py-8">
            <div className="text-[#00ff00]/60 font-mono">Loading opportunities...</div>
          </div>
        ) : error ? (
          <div className="text-center py-8">
            <div className="text-red-500 font-mono">{error}</div>
          </div>
        ) : opportunities.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-[#00ff00]/60 font-mono">No opportunities found</div>
          </div>
        ) : (
          <div className="grid gap-3 max-w-4xl mx-auto">
            {opportunities.map((opp) => {
              const value =
                opp.type === 'arbitrage' ? opp.profit_pct : opp.edge_pct;
              const valueLabel = opp.type === 'arbitrage' ? 'Profit' : 'Edge';

              return (
                <button
                  key={opp.id}
                  onClick={() => onSelectOpportunity(opp)}
                  className="border border-[#00ff00]/30 rounded p-4 hover:border-[#00ff00] hover:bg-[#00ff00]/5 transition-all text-left"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2 py-1 rounded border text-xs font-mono flex items-center gap-1 ${getTypeBadgeColor(
                          opp.type
                        )}`}
                      >
                        {getTypeIcon(opp.type)}
                        {opp.type.toUpperCase()}
                      </span>
                      {value !== null && value !== undefined && (
                        <span className="text-lg font-mono text-[#00ff00]">
                          {valueLabel}: {value.toFixed(2)}%
                        </span>
                      )}
                    </div>
                  </div>

                  {opp.event && (
                    <div className="mb-3">
                      <div className="text-sm font-mono text-[#00ff00]">
                        {opp.event.home_team} vs {opp.event.away_team}
                      </div>
                      <div className="text-xs text-[#00ff00]/60 font-mono mt-1">
                        {opp.event.sport} - {opp.event.league}
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <div className="text-xs text-[#00ff00]/60 font-mono">Provider 1</div>
                      <div className="font-mono text-[#00ff00]">
                        {opp.provider1}: {opp.outcome1} @ {opp.odds1.toFixed(2)}
                      </div>
                    </div>
                    {opp.provider2 && (
                      <div>
                        <div className="text-xs text-[#00ff00]/60 font-mono">Provider 2</div>
                        <div className="font-mono text-[#00ff00]">
                          {opp.provider2}: {opp.outcome2} @ {opp.odds2?.toFixed(2)}
                        </div>
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-[#00ff00]/30 text-center bg-[#0a0a0a]">
        <p className="text-xs text-[#00ff00]/60 font-mono">
          ESC to close | Ctrl+O to reopen | Auto-refresh every 10s
        </p>
      </div>
    </div>
  );
}
