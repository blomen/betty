import { useState, useEffect, useMemo } from 'react';
import { useProfiles } from '@/hooks/useProfiles';
import type { ProfileCreate } from '@/types';

const GREETINGS = [
  "Who's ready to grill?",
  "Let's fire up the smoker",
  "Time to turn up the heat",
  "The coals are hot",
  "Smoke 'em if you got 'em",
  "Let's get this brisket started",
  "The grill master has arrived",
  "Ready to sear some edges?",
  "Low and slow, that's the way to go",
  "Time to check the rub",
];

function getTimeGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

interface WelcomePageProps {
  onProfileSelected: () => void;
}

export function WelcomePage({ onProfileSelected }: WelcomePageProps) {
  const {
    profiles,
    isLoading,
    activateProfile,
    createProfile,
    refresh,
  } = useProfiles();

  const [activating, setActivating] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');

  const subtitle = useMemo(
    () => GREETINGS[Math.floor(Math.random() * GREETINGS.length)],
    []
  );

  // Clear error after 5s
  useEffect(() => {
    if (error) {
      const t = setTimeout(() => setError(null), 5000);
      return () => clearTimeout(t);
    }
  }, [error]);

  const handleSelect = async (id: number) => {
    setActivating(id);
    setError(null);
    try {
      await activateProfile(id);
      // Small delay to let Chrome start launching
      await new Promise((r) => setTimeout(r, 500));
      sessionStorage.setItem('bbq_session_active', '1');
      onProfileSelected();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to activate');
      setActivating(null);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      const data: ProfileCreate = {
        name: newName.trim(),
        kelly_fraction: 0.25,
        max_stake_pct: 5.0,
        min_edge_pct: 2.0,
      };
      const profile = await createProfile(data);
      setShowCreate(false);
      setNewName('');
      await refresh();
      // Auto-activate the new profile
      handleSelect(profile.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create profile');
    }
  };

  return (
    <div className="h-full flex items-center justify-center bg-bg">
      <div className="text-center max-w-lg w-full px-6">
        {/* Greeting */}
        <h1 className="text-3xl font-bold text-text mb-2">
          {getTimeGreeting()}
        </h1>
        <p className="text-muted text-lg mb-10">{subtitle}</p>

        {/* Profile cards */}
        {isLoading ? (
          <p className="text-muted text-sm">Loading profiles...</p>
        ) : profiles.length === 0 && !showCreate ? (
          <div>
            <p className="text-muted text-sm mb-4">No profiles yet</p>
            <button
              onClick={() => setShowCreate(true)}
              className="px-6 py-3 bg-panel2 border border-border text-text hover:bg-panel2/80 transition-colors text-sm"
            >
              Create your first profile
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {profiles.map((profile) => {
              const color = profile.color || '#e74c3c';
              const isActivating = activating === profile.id;

              return (
                <button
                  key={profile.id}
                  onClick={() => !activating && handleSelect(profile.id)}
                  disabled={!!activating}
                  className={`w-full p-4 border border-border bg-panel hover:bg-panel2 transition-all text-left flex items-center gap-4 group ${
                    isActivating ? 'opacity-70 cursor-wait' : activating ? 'opacity-40 cursor-wait' : 'cursor-pointer'
                  }`}
                  style={{
                    borderLeftWidth: 4,
                    borderLeftColor: isActivating ? color : '#3a3a3a',
                  }}
                >
                  <span
                    className="w-3 h-3 rounded-full flex-shrink-0 transition-colors"
                    style={{
                      backgroundColor: isActivating ? color : 'transparent',
                      border: `2px solid ${isActivating ? color : '#4a4a4a'}`,
                    }}
                  />
                  <span className={`font-medium flex-1 transition-colors ${isActivating ? 'text-text' : 'text-muted group-hover:text-text'}`}>
                    {profile.name}
                  </span>
                  {isActivating ? (
                    <span className="text-xs text-muted">Launching...</span>
                  ) : (
                    <span className="text-xs text-muted opacity-0 group-hover:opacity-100 transition-opacity">
                      Select
                    </span>
                  )}
                </button>
              );
            })}

            {/* New profile button */}
            {!showCreate && (
              <button
                onClick={() => setShowCreate(true)}
                disabled={!!activating}
                className="w-full p-3 border border-dashed border-border text-muted hover:text-text hover:border-border/80 transition-colors text-sm"
              >
                + New Profile
              </button>
            )}
          </div>
        )}

        {/* Create form */}
        {showCreate && (
          <div className="mt-4 p-4 border border-border bg-panel text-left">
            <label className="block text-xs text-muted mb-1">Profile Name</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                placeholder="Enter name..."
                className="flex-1 px-3 py-2 bg-panel2 border border-border text-text text-sm focus:outline-none focus:border-muted"
                autoFocus
              />
              <button
                onClick={handleCreate}
                className="px-4 py-2 text-sm bg-panel2 border border-border text-text hover:bg-panel2/80"
              >
                Create
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewName(''); }}
                className="px-3 py-2 text-sm text-muted hover:text-text"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mt-4 text-sm p-3 bg-error/10 text-error border border-error/20">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
