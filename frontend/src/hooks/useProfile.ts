import { useState, useEffect, useCallback } from 'react';
import type { Profile } from '@/types';
import { api } from '@/services/api';

const DEFAULT_PROFILE: Profile = {
  id: 0,
  name: 'default',
  bankroll: 1000.0,
  currency: 'USD',
  kelly_fraction: 0.25,
  min_edge_pct: 2.0,
  min_arb_pct: 0.5,
  max_stake_pct: 5.0,
  min_retention_pct: 80.0,
  preferred_counterparts: [],
  bonus_enabled: true,
  is_active: true,
  created_at: null,
};

export function useProfile() {
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getActiveProfile();
      setProfile(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profile');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const updateProfileSettings = useCallback(
    async (updates: {
      kelly_fraction?: number;
      min_edge_pct?: number;
      min_arb_pct?: number;
      max_stake_pct?: number;
      min_retention_pct?: number;
      preferred_counterparts?: string[];
      bonus_enabled?: boolean;
    }) => {
      try {
        if (!profile.id) {
          throw new Error('No active profile');
        }
        await api.updateProfile(profile.id, updates);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to update profile');
      }
    },
    [profile.id, refresh]
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    profile,
    isLoading,
    error,
    refresh,
    updateProfile: updateProfileSettings,
  };
}
