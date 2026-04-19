import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { Profile, ProfileCreate } from '@/types';

// Query keys that must refresh when the active profile changes.
// React Query partial-match catches nested keys like ['bankroll', 'allocate', null].
const PROFILE_SCOPED_KEYS = [
  ['profiles'],
  ['bankroll'],
  ['bets'],
  ['opportunities'],
  ['providers'],
] as const;

export function useProfiles() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.getProfiles(),
    staleTime: 60_000,
  });

  const invalidateProfileScoped = () => {
    for (const key of PROFILE_SCOPED_KEYS) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const activate = useMutation({
    mutationFn: (id: number) => api.activateProfile(id),
    onSuccess: invalidateProfileScoped,
  });

  const create = useMutation({
    mutationFn: (data: ProfileCreate) => api.createProfile(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteProfile(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  });

  return {
    profiles: (query.data?.profiles ?? []) as Profile[],
    activeProfile: (query.data?.active ?? null) as Profile | null,
    isLoading: query.isLoading,
    error: query.error,
    activate,
    create,
    remove,
  };
}
