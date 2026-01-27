import { useState, useEffect, useCallback } from 'react';
import type { Profile, ProfileCreate, ProfileUpdate } from '@/types';
import { api } from '@/services/api';

export function useProfiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [activeProfile, setActiveProfile] = useState<Profile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await api.getProfiles();
      setProfiles(data.profiles);
      setActiveProfile(data.active);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profiles');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createProfile = useCallback(async (data: ProfileCreate) => {
    try {
      const result = await api.createProfile(data);
      setProfiles((prev) => [...prev, result.profile]);
      return result.profile;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create profile';
      setError(message);
      throw new Error(message);
    }
  }, []);

  const updateProfile = useCallback(async (id: number, data: ProfileUpdate) => {
    try {
      const result = await api.updateProfile(id, data);
      setProfiles((prev) =>
        prev.map((p) => (p.id === id ? result.profile : p))
      );
      if (activeProfile?.id === id) {
        setActiveProfile(result.profile);
      }
      return result.profile;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update profile';
      setError(message);
      throw new Error(message);
    }
  }, [activeProfile]);

  const activateProfile = useCallback(async (id: number) => {
    try {
      const result = await api.activateProfile(id);
      setProfiles((prev) =>
        prev.map((p) => ({ ...p, is_active: p.id === id }))
      );
      setActiveProfile(result.profile);
      return result.profile;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to activate profile';
      setError(message);
      throw new Error(message);
    }
  }, []);

  const deleteProfile = useCallback(async (id: number) => {
    try {
      await api.deleteProfile(id);
      setProfiles((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete profile';
      setError(message);
      throw new Error(message);
    }
  }, []);

  return {
    profiles,
    activeProfile,
    isLoading,
    error,
    refresh,
    createProfile,
    updateProfile,
    activateProfile,
    deleteProfile,
  };
}
