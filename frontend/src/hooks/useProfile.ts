import { useState, useEffect, useCallback } from 'react';
import type { Profile } from '@/types';
import { api } from '@/services/api';

const DEFAULT_PROFILE: Profile = {
  name: 'default',
  kelly_fraction: 0.25,
  min_edge_pct: 2.0,
  min_arb_pct: 0.5,
  max_stake_pct: 5.0,
  min_retention_pct: 80.0,
  preferred_counterparts: [],
  bonus_enabled: true,
};

export function useProfile() {
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getProfile();
      setProfile(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profile');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const updateProfile = useCallback(
    async (updates: {
      kelly_fraction?: number;
      min_edge_pct?: number;
      min_arb_pct?: number;
      max_stake_pct?: number;
    }) => {
      try {
        await api.updateProfile(updates);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to update profile');
      }
    },
    [refresh]
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    profile,
    isLoading,
    error,
    refresh,
    updateProfile,
  };
}
