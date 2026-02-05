import { useState, useEffect } from 'react';
import { Card } from './Card';
import { useProfiles } from '@/hooks/useProfiles';
import type { Profile, ProfileCreate, ProfileUpdate } from '@/types';

interface ProfilePageProps {
  onRefresh: () => void;
}

const KELLY_PRESETS = [
  { value: 0.1, label: '10% (Very Conservative)' },
  { value: 0.25, label: '25% (Quarter Kelly)' },
  { value: 0.5, label: '50% (Half Kelly)' },
  { value: 1.0, label: '100% (Full Kelly)' },
];

interface ProfileFormData {
  name: string;
  kelly_fraction: string;
  min_edge_pct: string;
  min_arb_pct: string;
  max_stake_pct: string;
  min_retention_pct: string;
  bonus_enabled: boolean;
}

const defaultFormData: ProfileFormData = {
  name: '',
  kelly_fraction: '0.25',
  min_edge_pct: '2.0',
  min_arb_pct: '0.5',
  max_stake_pct: '5.0',
  min_retention_pct: '80',
  bonus_enabled: true,
};

function profileToFormData(profile: Profile): ProfileFormData {
  return {
    name: profile.name,
    kelly_fraction: profile.kelly_fraction.toString(),
    min_edge_pct: profile.min_edge_pct.toString(),
    min_arb_pct: profile.min_arb_pct.toString(),
    max_stake_pct: profile.max_stake_pct.toString(),
    min_retention_pct: profile.min_retention_pct.toString(),
    bonus_enabled: profile.bonus_enabled,
  };
}

export function ProfilePage({ onRefresh }: ProfilePageProps) {
  const {
    profiles,
    isLoading,
    error,
    createProfile,
    updateProfile,
    activateProfile,
    deleteProfile,
  } = useProfiles();

  const [isCreating, setIsCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formData, setFormData] = useState<ProfileFormData>(defaultFormData);
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
    if (!formData.name.trim()) {
      setActionError('Profile name is required');
      return;
    }

    try {
      const data: ProfileCreate = {
        name: formData.name.trim(),
        kelly_fraction: parseFloat(formData.kelly_fraction) || 0.25,
        min_edge_pct: parseFloat(formData.min_edge_pct) || 2.0,
        min_arb_pct: parseFloat(formData.min_arb_pct) || 0.5,
        max_stake_pct: parseFloat(formData.max_stake_pct) || 5.0,
      };
      await createProfile(data);
      setIsCreating(false);
      setFormData(defaultFormData);
      setActionSuccess(`Profile "${data.name}" created`);
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to create profile');
    }
  };

  const handleUpdate = async (id: number) => {
    try {
      const data: ProfileUpdate = {
        name: formData.name.trim() || undefined,
        kelly_fraction: parseFloat(formData.kelly_fraction) || undefined,
        min_edge_pct: parseFloat(formData.min_edge_pct) || undefined,
        min_arb_pct: parseFloat(formData.min_arb_pct) || undefined,
        max_stake_pct: parseFloat(formData.max_stake_pct) || undefined,
        min_retention_pct: parseFloat(formData.min_retention_pct) || undefined,
        bonus_enabled: formData.bonus_enabled,
      };
      await updateProfile(id, data);
      setEditingId(null);
      setFormData(defaultFormData);
      setActionSuccess('Profile updated');
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update profile');
    }
  };

  const handleActivate = async (id: number) => {
    try {
      await activateProfile(id);
      setActionSuccess('Profile activated');
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to activate profile');
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete profile "${name}"? This cannot be undone.`)) {
      return;
    }

    try {
      await deleteProfile(id);
      setActionSuccess(`Profile "${name}" deleted`);
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to delete profile');
    }
  };

  const startEditing = (profile: Profile) => {
    setEditingId(profile.id);
    setFormData(profileToFormData(profile));
    setIsCreating(false);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setIsCreating(false);
    setFormData(defaultFormData);
  };

  const startCreating = () => {
    setIsCreating(true);
    setEditingId(null);
    setFormData(defaultFormData);
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabProfiles" />
          Profiles
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabProfiles" />
          Profiles
        </h2>
        {!isCreating && !editingId && (
          <button
            onClick={startCreating}
            className="px-3 py-1.5 text-xs bg-tabProfiles/20 text-tabProfiles rounded hover:bg-tabProfiles/30 transition-colors"
          >
            + New Profile
          </button>
        )}
      </div>

      {/* Error/Success Messages */}
      {(actionError || error) && (
        <div className="text-sm p-3 rounded bg-error/10 text-error border border-error/20">
          {actionError || error}
        </div>
      )}
      {actionSuccess && (
        <div className="text-sm p-3 rounded bg-success/10 text-success border border-success/20">
          {actionSuccess}
        </div>
      )}

      {/* Create Form */}
      {isCreating && (
        <Card title="Create Profile">
          <ProfileForm
            formData={formData}
            setFormData={setFormData}
            onSubmit={handleCreate}
            onCancel={cancelEdit}
            submitLabel="Create"
          />
        </Card>
      )}

      {/* Profile List */}
      <div className="space-y-3">
        {profiles.map((profile) => {
          const isActive = profile.is_active;
          const isEditing = editingId === profile.id;

          if (isEditing) {
            return (
              <Card key={profile.id} title={`Edit: ${profile.name}`}>
                <ProfileForm
                  formData={formData}
                  setFormData={setFormData}
                  onSubmit={() => handleUpdate(profile.id)}
                  onCancel={cancelEdit}
                  submitLabel="Save"
                />
              </Card>
            );
          }

          return (
            <div
              key={profile.id}
              className={`p-4 rounded-lg border ${
                isActive
                  ? 'bg-tabProfiles/5 border-tabProfiles/30'
                  : 'bg-panel border-border'
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className={`w-3 h-3 rounded-full border-2 ${
                      isActive
                        ? 'bg-tabProfiles border-tabProfiles'
                        : 'border-muted'
                    }`}
                  />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`font-medium ${isActive ? 'text-text' : 'text-muted'}`}>
                        {profile.name}
                      </span>
                      {isActive && (
                        <span className="text-xs px-1.5 py-0.5 bg-tabProfiles/20 text-tabProfiles rounded">
                          Active
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted mt-1 flex flex-wrap gap-x-4 gap-y-1">
                      <span>Bankroll: {profile.bankroll.toLocaleString()} {profile.currency}</span>
                      <span>Kelly: {(profile.kelly_fraction * 100).toFixed(0)}%</span>
                      <span>Min Edge: {profile.min_edge_pct}%</span>
                      <span>Max Stake: {profile.max_stake_pct}%</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {!isActive && (
                    <button
                      onClick={() => handleActivate(profile.id)}
                      className="px-2 py-1 text-xs text-tabProfiles hover:bg-tabProfiles/10 rounded transition-colors"
                    >
                      Activate
                    </button>
                  )}
                  <button
                    onClick={() => startEditing(profile)}
                    className="px-2 py-1 text-xs text-muted hover:text-text hover:bg-panel2 rounded transition-colors"
                  >
                    Edit
                  </button>
                  {!isActive && (
                    <button
                      onClick={() => handleDelete(profile.id, profile.name)}
                      className="px-2 py-1 text-xs text-error/70 hover:text-error hover:bg-error/10 rounded transition-colors"
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
            className="text-tabProfiles hover:underline"
          >
            Create your first profile
          </button>
        </div>
      )}
    </div>
  );
}

interface ProfileFormProps {
  formData: ProfileFormData;
  setFormData: (data: ProfileFormData) => void;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
}

function ProfileForm({ formData, setFormData, onSubmit, onCancel, submitLabel }: ProfileFormProps) {
  const handleChange = (field: keyof ProfileFormData, value: string | boolean) => {
    setFormData({ ...formData, [field]: value });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Name */}
        <div>
          <label className="block text-xs text-muted mb-1">Profile Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={(e) => handleChange('name', e.target.value)}
            placeholder="My Profile"
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          />
        </div>

        {/* Kelly Fraction */}
        <div>
          <label className="block text-xs text-muted mb-1">Kelly Fraction</label>
          <select
            value={formData.kelly_fraction}
            onChange={(e) => handleChange('kelly_fraction', e.target.value)}
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          >
            {KELLY_PRESETS.map((preset) => (
              <option key={preset.value} value={preset.value}>
                {preset.label}
              </option>
            ))}
          </select>
        </div>

        {/* Min Edge */}
        <div>
          <label className="block text-xs text-muted mb-1">Min Edge (%)</label>
          <input
            type="number"
            step="0.1"
            value={formData.min_edge_pct}
            onChange={(e) => handleChange('min_edge_pct', e.target.value)}
            placeholder="2.0"
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          />
        </div>

        {/* Min Arb */}
        <div>
          <label className="block text-xs text-muted mb-1">Min Arb Profit (%)</label>
          <input
            type="number"
            step="0.1"
            value={formData.min_arb_pct}
            onChange={(e) => handleChange('min_arb_pct', e.target.value)}
            placeholder="0.5"
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          />
        </div>

        {/* Max Stake */}
        <div>
          <label className="block text-xs text-muted mb-1">Max Stake (% of bankroll)</label>
          <input
            type="number"
            step="0.1"
            value={formData.max_stake_pct}
            onChange={(e) => handleChange('max_stake_pct', e.target.value)}
            placeholder="5.0"
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          />
        </div>

        {/* Min Retention */}
        <div>
          <label className="block text-xs text-muted mb-1">Min Retention (%)</label>
          <input
            type="number"
            step="1"
            value={formData.min_retention_pct}
            onChange={(e) => handleChange('min_retention_pct', e.target.value)}
            placeholder="80"
            className="w-full px-3 py-2 bg-panel2 border border-border rounded text-text text-sm focus:outline-none focus:border-tabProfiles"
          />
        </div>

        {/* Bonus Enabled */}
        <div className="flex items-center gap-2 pt-5">
          <input
            type="checkbox"
            id="bonus_enabled"
            checked={formData.bonus_enabled}
            onChange={(e) => handleChange('bonus_enabled', e.target.checked)}
            className="w-4 h-4 rounded border-border bg-panel2 text-tabProfiles focus:ring-tabProfiles"
          />
          <label htmlFor="bonus_enabled" className="text-sm text-text">
            Bonus features enabled
          </label>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={onSubmit}
          className="px-4 py-2 text-sm bg-tabProfiles text-white rounded hover:bg-tabProfiles/90 transition-colors"
        >
          {submitLabel}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm text-muted hover:text-text transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
