/**
 * Risk Management Hook
 *
 * Manages risk state with auto-refresh, exposing:
 * - Provider risk profiles
 * - Risk summary across all providers
 * - Risk configuration
 * - Methods to update config and manage cooldowns
 */

import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import type {
  ProviderRiskProfile,
  RiskSummary,
  RiskConfig,
  RiskConfigUpdate,
  RiskAwareStake,
  SelectOpportunityResponse,
  OpportunityInput,
} from '@/types';

interface UseRiskOptions {
  autoRefresh?: boolean;
  refreshInterval?: number; // ms
}

interface UseRiskReturn {
  // State
  profiles: Record<string, ProviderRiskProfile>;
  summary: RiskSummary | null;
  config: RiskConfig | null;
  loading: boolean;
  error: string | null;

  // Actions
  refresh: () => Promise<void>;
  getProviderRisk: (providerId: string) => Promise<ProviderRiskProfile>;
  updateConfig: (update: RiskConfigUpdate) => Promise<RiskConfig>;
  setCooldown: (providerId: string, hours: number, reason?: string) => Promise<void>;
  clearCooldown: (providerId: string) => Promise<void>;
  calculateStake: (
    odds: number,
    fairOdds: number,
    providerId: string,
    force?: boolean
  ) => Promise<RiskAwareStake>;
  selectOpportunity: (
    opportunities: OpportunityInput[],
    stake: number,
    options?: { temperature?: number; deterministic?: boolean }
  ) => Promise<SelectOpportunityResponse>;
}

export function useRisk(options: UseRiskOptions = {}): UseRiskReturn {
  const { autoRefresh = false, refreshInterval = 60000 } = options;

  const [profiles, setProfiles] = useState<Record<string, ProviderRiskProfile>>({});
  const [summary, setSummary] = useState<RiskSummary | null>(null);
  const [config, setConfig] = useState<RiskConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [riskData, configData] = await Promise.all([
        api.getAllRiskProfiles(),
        api.getRiskConfig(),
      ]);

      setProfiles(riskData.providers);
      setSummary(riskData.summary);
      setConfig(configData);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load risk data';
      setError(message);
      console.error('Risk data fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const getProviderRisk = useCallback(async (providerId: string): Promise<ProviderRiskProfile> => {
    const profile = await api.getProviderRisk(providerId);

    // Update local state
    setProfiles((prev) => ({
      ...prev,
      [providerId]: profile,
    }));

    return profile;
  }, []);

  const updateConfig = useCallback(async (update: RiskConfigUpdate): Promise<RiskConfig> => {
    const newConfig = await api.updateRiskConfig(update);
    setConfig(newConfig);
    return newConfig;
  }, []);

  const setCooldown = useCallback(
    async (providerId: string, hours: number, reason?: string): Promise<void> => {
      await api.setProviderCooldown(providerId, hours, reason);

      // Refresh the provider's risk profile
      await getProviderRisk(providerId);
    },
    [getProviderRisk]
  );

  const clearCooldown = useCallback(
    async (providerId: string): Promise<void> => {
      await api.clearProviderCooldown(providerId);

      // Refresh the provider's risk profile
      await getProviderRisk(providerId);
    },
    [getProviderRisk]
  );

  const calculateStake = useCallback(
    async (
      odds: number,
      fairOdds: number,
      providerId: string,
      force = false
    ): Promise<RiskAwareStake> => {
      return api.calculateRiskAwareStake(odds, fairOdds, providerId, force);
    },
    []
  );

  const selectOpportunity = useCallback(
    async (
      opportunities: OpportunityInput[],
      stake: number,
      options?: { temperature?: number; deterministic?: boolean }
    ): Promise<SelectOpportunityResponse> => {
      return api.selectOpportunity(opportunities, stake, options);
    },
    []
  );

  // Initial load
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresh
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(refresh, refreshInterval);
    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, refresh]);

  return {
    profiles,
    summary,
    config,
    loading,
    error,
    refresh,
    getProviderRisk,
    updateConfig,
    setCooldown,
    clearCooldown,
    calculateStake,
    selectOpportunity,
  };
}

/**
 * Get risk level color for UI display
 */
export function getRiskLevelColor(level: string): string {
  switch (level) {
    case 'low':
      return 'text-green-500';
    case 'medium':
      return 'text-yellow-500';
    case 'high':
      return 'text-orange-500';
    case 'critical':
      return 'text-red-500';
    default:
      return 'text-gray-500';
  }
}

/**
 * Get risk level background color for UI display
 */
export function getRiskLevelBgColor(level: string): string {
  switch (level) {
    case 'low':
      return 'bg-green-500/20';
    case 'medium':
      return 'bg-yellow-500/20';
    case 'high':
      return 'bg-orange-500/20';
    case 'critical':
      return 'bg-red-500/20';
    default:
      return 'bg-gray-500/20';
  }
}

/**
 * Format risk score as percentage
 */
export function formatRiskScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

/**
 * Get recommendation priority (higher = more urgent)
 */
export function getRecommendationPriority(recommendation: string): number {
  if (recommendation.startsWith('ALERT:')) return 3;
  if (recommendation.includes('cooldown')) return 2;
  return 1;
}
