import { useState } from 'react';
import type { Profile, ProfileCreate } from '@/types';

interface ProfileSelectorProps {
  profiles: Profile[];
  activeProfile: Profile | null;
  onActivate: (id: number) => Promise<void>;
  onCreate: (data: ProfileCreate) => Promise<Profile>;
  onUpdate: (id: number, data: Partial<Profile>) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

export function ProfileSelector({
  profiles,
  activeProfile,
  onActivate,
  onCreate,
  onUpdate: _onUpdate,
  onDelete,
}: ProfileSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!newName.trim()) {
      setError('Name is required');
      return;
    }
    try {
      await onCreate({
        name: newName.trim(),
      });
      setNewName('');
      setIsCreating(false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create');
    }
  };

  const handleActivate = async (id: number) => {
    try {
      await onActivate(id);
      setIsOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to activate');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await onDelete(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete');
    }
  };

  return (
    <div className="relative">
      {/* Trigger button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-2 py-1 rounded text-xs
                   bg-terminal-surface border border-terminal-border
                   hover:border-terminal-accent transition-colors"
      >
        <span className="text-terminal-accent">[$]</span>
        <span className="text-terminal-text">
          {activeProfile?.name || 'No profile'}
        </span>
        <span className="text-terminal-muted">{isOpen ? '[-]' : '[+]'}</span>
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-72 z-50
                        bg-terminal-surface border border-terminal-border rounded
                        shadow-lg">
          {/* Profile list */}
          <div className="max-h-64 overflow-y-auto">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className={`flex items-center justify-between p-2 border-b border-terminal-border/50
                           hover:bg-terminal-bg transition-colors
                           ${profile.is_active ? 'bg-terminal-bg' : ''}`}
              >
                <button
                  onClick={() => handleActivate(profile.id)}
                  className="flex-1 text-left flex items-center gap-2"
                >
                  <span className={profile.is_active ? 'text-terminal-accent' : 'text-terminal-muted'}>
                    {profile.is_active ? '[*]' : '[ ]'}
                  </span>
                  <span className="text-terminal-text">{profile.name}</span>
                </button>

                {!profile.is_active && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(profile.id);
                    }}
                    className="px-1.5 py-0.5 text-xs text-terminal-muted hover:text-terminal-red"
                    title="Delete profile"
                  >
                    [x]
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Create new */}
          {isCreating ? (
            <div className="p-2 border-t border-terminal-border">
              <div className="flex gap-2 mb-2">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCreate();
                    else if (e.key === 'Escape') {
                      setIsCreating(false);
                      setError(null);
                    }
                  }}
                  placeholder="Profile name"
                  className="flex-1 px-2 py-1 text-xs bg-terminal-bg border border-terminal-border
                             rounded text-terminal-text placeholder-terminal-muted/50
                             outline-none focus:border-terminal-accent"
                  autoFocus
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleCreate}
                  className="flex-1 px-2 py-1 text-xs bg-terminal-accent/20 text-terminal-accent
                             rounded hover:bg-terminal-accent/30"
                >
                  [create]
                </button>
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setError(null);
                  }}
                  className="px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text"
                >
                  [cancel]
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setIsCreating(true)}
              className="w-full p-2 text-xs text-terminal-muted hover:text-terminal-accent
                         hover:bg-terminal-bg border-t border-terminal-border transition-colors"
            >
              [+] New profile
            </button>
          )}

          {/* Error */}
          {error && (
            <div className="p-2 text-xs text-terminal-red border-t border-terminal-border">
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
