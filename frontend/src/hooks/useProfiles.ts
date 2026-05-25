import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { Profile, ProfileCreate } from '@/types';

export function useProfiles() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.getProfiles(),
    staleTime: 60_000,
  });

  const activate = useMutation({
    mutationFn: (id: number) => api.activateProfile(id),
    onSuccess: () => {
      // Profile switch changes every profile-scoped view. Nuclear invalidate
      // is semantically correct and catches any key we forgot to enumerate.
      queryClient.invalidateQueries();
    },
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
