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
        return 'bg-terminal-cyan/20 text-terminal-cyan border-terminal-cyan/30';
      case 'value':
        return 'bg-terminal-green/20 text-terminal-green border-terminal-green/30';
      case 'bonus':
        return 'bg-terminal-purple/20 text-terminal-purple border-terminal-purple/30';
      default:
        return 'bg-terminal-muted/20 text-terminal-muted border-terminal-muted/30';
    }
  };

  return (
    <div className="fixed inset-0 bg-terminal-bg/95 flex flex-col z-50">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-terminal-accent/30">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-mono text-terminal-accent">Opportunities</h2>
          <span className="text-sm font-mono text-terminal-muted">({count} found)</span>
        </div>
        <button
          onClick={onClose}
          className="p-2 hover:bg-terminal-accent/10 rounded transition-colors"
          aria-label="Close"
        >
          <X className="w-5 h-5 text-terminal-accent" />
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 p-4 border-b border-terminal-accent/20 bg-terminal-bg">
        <Filter className="w-4 h-4 text-terminal-muted" />

        {/* Type Filter */}
        <select
          value={typeFilter || ''}
          onChange={(e) => setTypeFilter(e.target.value as any || undefined)}
          className="bg-terminal-bg border border-terminal-accent/30 text-terminal-accent px-3 py-1 rounded font-mono text-sm focus:outline-none focus:border-terminal-accent"
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
          className="bg-terminal-bg border border-terminal-accent/30 text-terminal-accent px-3 py-1 rounded font-mono text-sm focus:outline-none focus:border-terminal-accent placeholder-terminal-muted/50"
        />

        {/* Min Value Filter */}
        <input
          type="number"
          placeholder="Min %"
          value={minValueFilter || ''}
          onChange={(e) => setMinValueFilter(parseFloat(e.target.value) || 0)}
          className="bg-terminal-bg border border-terminal-accent/30 text-terminal-accent px-3 py-1 rounded font-mono text-sm w-24 focus:outline-none focus:border-terminal-accent placeholder-terminal-muted/50"
        />
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="text-center py-8">
            <div className="text-terminal-muted font-mono">Loading opportunities...</div>
          </div>
        ) : error ? (
          <div className="text-center py-8">
            <div className="text-red-500 font-mono">{error}</div>
          </div>
        ) : opportunities.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-terminal-muted font-mono">No opportunities found</div>
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
                  className="border border-terminal-accent/30 rounded p-4 hover:border-terminal-accent hover:bg-terminal-accent/5 transition-all text-left"
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
                        <span className="text-lg font-mono text-terminal-accent">
                          {valueLabel}: {value.toFixed(2)}%
                        </span>
                      )}
                    </div>
                  </div>

                  {opp.event && (
                    <div className="mb-3">
                      <div className="text-sm font-mono text-terminal-accent">
                        {opp.event.home_team} vs {opp.event.away_team}
                      </div>
                      <div className="text-xs text-terminal-muted font-mono mt-1">
                        {opp.event.sport} - {opp.event.league}
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <div className="text-xs text-terminal-muted font-mono">Provider 1</div>
                      <div className="font-mono text-terminal-accent">
                        {opp.provider1}: {opp.outcome1} @ {opp.odds1.toFixed(2)}
                      </div>
                    </div>
                    {opp.provider2 && (
                      <div>
                        <div className="text-xs text-terminal-muted font-mono">Provider 2</div>
                        <div className="font-mono text-terminal-accent">
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
      <div className="p-4 border-t border-terminal-accent/30 text-center bg-terminal-bg">
        <p className="text-xs text-terminal-muted font-mono">
          ESC to close | Ctrl+O to reopen | Auto-refresh every 10s
        </p>
      </div>
    </div>
  );
}
