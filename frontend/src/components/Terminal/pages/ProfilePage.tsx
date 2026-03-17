import { useState, useEffect } from 'react';
import { Card } from './Card';
import { useProfiles } from '@/hooks/useProfiles';
import { TabIcon } from '../TabBar';
import type { Profile, ProfileCreate, ProfileUpdate } from '@/types';

interface ProfilePageProps {
  onRefresh?: () => void;
}

export function ProfilePage({ onRefresh }: ProfilePageProps) {
  const {
    profiles,
    isLoading,
    error,
    refresh: refreshProfiles,
    createProfile,
    updateProfile,
    activateProfile,
    deleteProfile,
  } = useProfiles();

  const [isCreating, setIsCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [nameInput, setNameInput] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Clear messages after 5 seconds
  useEffect(() => {
    if (actionError || actionSuccess) {
      const timer = setTimeout(() => {
        setActionError(null);
        setActionSuccess(null);
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [actionError, actionSuccess]);

  const handleCreate = async () => {
    if (!nameInput.trim()) {
      setActionError('Profile name is required');
      return;
    }

    try {
      const data: ProfileCreate = {
        name: nameInput.trim(),
        kelly_fraction: 0.25,
        max_stake_pct: 5.0,
        min_edge_pct: 2.0,
      };
      await createProfile(data);
      setIsCreating(false);
      setNameInput('');
      setActionSuccess(`Profile "${data.name}" created`);
      onRefresh?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to create profile');
    }
  };

  const handleUpdate = async (id: number) => {
    try {
      const data: ProfileUpdate = {
        name: nameInput.trim() || undefined,
      };
      await updateProfile(id, data);
      setEditingId(null);
      setNameInput('');
      setActionSuccess('Profile updated');
      onRefresh?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update profile');
    }
  };

  const handleActivate = async (id: number) => {
    try {
      await activateProfile(id);
      setActionSuccess('Profile activated');
      await refreshProfiles();
      onRefresh?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to activate profile');
      // Refresh to sync with server state even on error
      await refreshProfiles();
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete profile "${name}"? This cannot be undone.`)) {
      return;
    }

    try {
      await deleteProfile(id);
      setActionSuccess(`Profile "${name}" deleted`);
      onRefresh?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to delete profile');
    }
  };

  const startEditing = (profile: Profile) => {
    setEditingId(profile.id);
    setNameInput(profile.name);
    setIsCreating(false);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setIsCreating(false);
    setNameInput('');
  };

  const startCreating = () => {
    setIsCreating(true);
    setEditingId(null);
    setNameInput('');
  };

  if (isLoading) {
    return (
      <div className="space-y-4 overflow-y-auto">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="profiles" color="#9AA0A6" size={16} />
          Profiles
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="profiles" color="#9AA0A6" size={16} />
          Profiles
        </h2>
        {!isCreating && !editingId && (
          <button
            onClick={startCreating}
            className="px-3 py-1.5 text-xs bg-muted/15 text-muted hover:bg-muted/25 hover:text-text transition-colors"
          >
            + New Profile
          </button>
        )}
      </div>

      {/* Error/Success Messages */}
      {(actionError || error) && (
        <div className="text-sm p-3 bg-error/10 text-error border border-error/20">
          {actionError || error}
        </div>
      )}
      {actionSuccess && (
        <div className="text-sm p-3 bg-success/10 text-success border border-success/20">
          {actionSuccess}
        </div>
      )}

      {/* Create Form */}
      {isCreating && (
        <Card title="Create Profile">
          <div className="space-y-4">
            <div className="max-w-sm">
              <label className="block text-xs text-muted mb-1">Profile Name</label>
              <input
                type="text"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                placeholder="My Profile"
                className="w-full px-3 py-2 bg-panel2 border border-border text-text text-sm focus:outline-none focus:border-muted"
                autoFocus
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCreate}
                className="px-4 py-2 text-sm bg-muted/20 text-text hover:bg-muted/30 transition-colors"
              >
                Create
              </button>
              <button
                onClick={cancelEdit}
                className="px-4 py-2 text-sm text-muted hover:text-text transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </Card>
      )}

      {/* Profile List */}
      <div className="space-y-0">
        {profiles.map((profile) => {
          const isActive = profile.is_active;
          const isEditing = editingId === profile.id;
          const profileColor = profile.color || '#e74c3c';

          if (isEditing) {
            return (
              <Card key={profile.id} title={`Edit: ${profile.name}`}>
                <div className="space-y-4">
                  <div className="max-w-sm">
                    <label className="block text-xs text-muted mb-1">Profile Name</label>
                    <input
                      type="text"
                      value={nameInput}
                      onChange={(e) => setNameInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleUpdate(profile.id)}
                      className="w-full px-3 py-2 bg-panel2 border border-border text-text text-sm focus:outline-none focus:border-muted"
                      autoFocus
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleUpdate(profile.id)}
                      className="px-4 py-2 text-sm bg-muted/20 text-text hover:bg-muted/30 transition-colors"
                    >
                      Save
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="px-4 py-2 text-sm text-muted hover:text-text transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </Card>
            );
          }

          return (
            <div
              key={profile.id}
              className={`p-4 border border-border ${
                isActive ? 'bg-panel2/30' : 'bg-panel'
              } transition-colors`}
              style={{ borderLeftWidth: 3, borderLeftColor: isActive ? profileColor : '#3a3a3a' }}
            >
              {/* Top row: name + actions */}
              <div className="flex items-center justify-between">
                <div
                  className={`flex items-center gap-3 ${!isActive ? 'cursor-pointer hover:opacity-80' : ''}`}
                  onClick={() => !isActive && handleActivate(profile.id)}
                >
                  <span
                    className="w-3 h-3 rounded-full flex-shrink-0"
                    style={{
                      backgroundColor: isActive ? profileColor : 'transparent',
                      border: `2px solid ${isActive ? profileColor : '#4a4a4a'}`,
                    }}
                  />
                  <div className="flex items-center gap-2">
                    <span className={`font-medium ${isActive ? 'text-text' : 'text-muted2'}`}>
                      {profile.name}
                    </span>
                    {isActive && (
                      <span
                        className="text-xs px-1.5 py-0.5"
                        style={{ backgroundColor: profileColor + '33', color: profileColor }}
                      >
                        Active
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => startEditing(profile)}
                    className="px-2 py-1 text-xs text-muted hover:text-text hover:bg-panel2 transition-colors"
                  >
                    Edit
                  </button>
                  {!isActive && (
                    <button
                      onClick={() => handleDelete(profile.id, profile.name)}
                      className="px-2 py-1 text-xs text-error/70 hover:text-error hover:bg-error/10 transition-colors"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

            </div>
          );
        })}
      </div>

      {profiles.length === 0 && !isCreating && (
        <div className="text-center py-8 text-muted">
          <p className="mb-2">No profiles yet</p>
          <button
            onClick={startCreating}
            className="text-muted hover:text-text hover:underline"
          >
            Create your first profile
          </button>
        </div>
      )}
    </div>
  );
}
