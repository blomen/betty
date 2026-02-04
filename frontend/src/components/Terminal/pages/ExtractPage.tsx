import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { Provider, ExtractionStatus } from '@/types';

interface ExtractPageProps {
  providers: Provider[];
  onRefresh: () => void;
}

export function ExtractPage({ providers, onRefresh: _onRefresh }: ExtractPageProps) {
  // Note: onRefresh available for future use when extraction completes
  void _onRefresh;
  const [extractionStatus, setExtractionStatus] = useState<ExtractionStatus | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [isRunning, setIsRunning] = useState(false);

  // Poll extraction status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const status = await api.getExtractionProgress();
        setExtractionStatus(status);
        setIsRunning(status.running);
      } catch (err) {
        console.error('Failed to fetch extraction status:', err);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 1000);
    return () => clearInterval(interval);
  }, []);

  const toggleProvider = useCallback((providerId: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(providerId)) {
        next.delete(providerId);
      } else {
        next.add(providerId);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedProviders(new Set(providers.filter(p => p.is_enabled).map(p => p.id)));
  }, [providers]);

  const selectNone = useCallback(() => {
    setSelectedProviders(new Set());
  }, []);

  const runExtraction = useCallback(async () => {
    if (isRunning) return;
    setIsRunning(true);
    try {
      const providerList = selectedProviders.size > 0
        ? Array.from(selectedProviders).join(',')
        : undefined;
      await api.runExtraction(providerList);
    } catch (err) {
      console.error('Extraction failed:', err);
    }
  }, [selectedProviders, isRunning]);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'pending': return '\u25CB';
      case 'running': return '\u25D0';
      case 'completed': return '\u25CF';
      case 'failed': return '\u2715';
      default: return '?';
    }
  };

  const generateProgressBar = (percent: number) => {
    const filled = Math.floor(percent / 5);
    const empty = 20 - filled;
    return `[${'='.repeat(filled)}${'-'.repeat(empty)}]`;
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabExtract" />
        Extraction
      </h2>

      {/* Status Card */}
      {extractionStatus && (
        <Card title="Status">
          <div className="space-y-3">
            <div className="flex items-center gap-4">
              <span className="text-accent font-mono text-sm">
                {generateProgressBar(extractionStatus.progress_pct)} {Math.round(extractionStatus.progress_pct)}%
              </span>
              {extractionStatus.running && extractionStatus.current_provider && (
                <span className="text-muted text-sm">
                  Processing: {formatProviderName(extractionStatus.current_provider)}
                </span>
              )}
            </div>
            <div className="flex gap-6 text-sm text-muted">
              <span>Providers: {extractionStatus.completed_providers}/{extractionStatus.total_providers}</span>
              <span>Events: {extractionStatus.total_events}</span>
              <span>Odds: {extractionStatus.total_odds}</span>
              <span>Time: {Math.floor(extractionStatus.elapsed_seconds)}s</span>
            </div>
          </div>
        </Card>
      )}

      {/* Provider Selection */}
      <Card
        title="Providers"
        headerRight={
          <div className="flex gap-2">
            <button
              onClick={selectAll}
              className="text-xs text-muted hover:text-text"
            >
              All
            </button>
            <button
              onClick={selectNone}
              className="text-xs text-muted hover:text-text"
            >
              None
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 mb-4">
          {providers.filter(p => p.is_enabled).map(provider => {
            const isSelected = selectedProviders.has(provider.id);
            const providerStatus = extractionStatus?.providers[provider.id];
            return (
              <button
                key={provider.id}
                onClick={() => toggleProvider(provider.id)}
                disabled={isRunning}
                className={`
                  px-3 py-2 rounded border text-sm text-left transition-colors
                  ${isSelected
                    ? 'border-accent bg-accentBg text-text'
                    : 'border-border hover:border-muted2 text-muted'
                  }
                  ${isRunning ? 'opacity-50 cursor-not-allowed' : ''}
                `}
              >
                <div className="flex items-center gap-2">
                  {providerStatus && (
                    <span className={
                      providerStatus.status === 'completed' ? 'text-success' :
                      providerStatus.status === 'running' ? 'text-accent' :
                      providerStatus.status === 'failed' ? 'text-error' : 'text-muted'
                    }>
                      {getStatusIcon(providerStatus.status)}
                    </span>
                  )}
                  <span>{formatProviderName(provider.name)}</span>
                </div>
                {providerStatus && providerStatus.status !== 'pending' && (
                  <div className="text-xs text-muted mt-1">
                    {providerStatus.events} events, {providerStatus.odds} odds
                  </div>
                )}
              </button>
            );
          })}
        </div>

        <button
          onClick={runExtraction}
          disabled={isRunning}
          className={`
            px-4 py-2 rounded font-medium text-sm transition-colors
            ${isRunning
              ? 'bg-panel2 text-muted cursor-not-allowed'
              : 'bg-tabExtract text-bg hover:opacity-90'
            }
          `}
        >
          {isRunning ? 'Running...' : 'Run Extraction'}
        </button>
      </Card>

      {/* Provider Details (when running or recently completed) */}
      {extractionStatus && Object.keys(extractionStatus.providers).length > 0 && (
        <Card title="Provider Details">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left">
                  <th className="py-2 pr-4">Provider</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Events</th>
                  <th className="py-2 pr-4">Odds</th>
                  <th className="py-2 pr-4">Sports</th>
                  <th className="py-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(extractionStatus.providers).map(([id, provider]) => (
                  <tr key={id} className="border-t border-border">
                    <td className="py-2 pr-4 text-text">{formatProviderName(id)}</td>
                    <td className="py-2 pr-4">
                      <span className={
                        provider.status === 'completed' ? 'text-success' :
                        provider.status === 'running' ? 'text-accent' :
                        provider.status === 'failed' ? 'text-error' : 'text-muted'
                      }>
                        {getStatusIcon(provider.status)} {provider.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-text">{provider.events}</td>
                    <td className="py-2 pr-4 text-text">{provider.odds}</td>
                    <td className="py-2 pr-4 text-muted">
                      {provider.sports_completed}/{provider.sports_total}
                    </td>
                    <td className="py-2 text-muted">
                      {provider.duration_seconds > 0 ? `${provider.duration_seconds.toFixed(1)}s` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
