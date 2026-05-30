import { useEffect, useRef, useState } from 'react';
import { useProfiles } from '@/hooks/useProfiles';

export function ProfileSelector() {
  const { profiles, activeProfile, isLoading, activate, create, remove } = useProfiles();
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [createError, setCreateError] = useState<string | null>(null);
  // New-profile options (multi-profile sharp accounts).
  const [kind, setKind] = useState<'edge' | 'bonus'>('bonus');
  const [sharpMode, setSharpMode] = useState<'shared' | 'fresh'>('shared');
  const [freshLabel, setFreshLabel] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  const closeAndReset = () => {
    setOpen(false);
    activate.reset();
    create.reset();
    setCreateError(null);
    setKind('bonus');
    setSharpMode('shared');
    setFreshLabel('');
  };

  const handleToggle = () => {
    if (open) closeAndReset();
    else setOpen(true);
  };

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        closeAndReset();
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const handleActivate = async (id: number) => {
    if (id === activeProfile?.id) {
      closeAndReset();
      return;
    }
    try {
      await activate.mutateAsync(id);
      closeAndReset();
    } catch {
      // error surfaced via activate.error; keep dropdown open
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setCreateError(null);
    const useShared = sharpMode === 'shared';
    try {
      await create.mutateAsync({
        name,
        kind,
        use_shared_sharp: useShared,
        fresh_sharp_label: useShared ? null : freshLabel.trim() || name,
      });
      setNewName('');
      setFreshLabel('');
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await remove.mutateAsync(id);
    } catch {
      // surfaced via remove.error
    }
  };

  if (isLoading) {
    return <div className="text-muted text-xs px-2">Loading…</div>;
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={handleToggle}
        className="flex items-center gap-2 px-2 py-1 text-xs border border-border bg-panel hover:border-tabBankroll transition-colors"
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ backgroundColor: activeProfile?.color ?? '#666' }}
        />
        <span className="text-text">{activeProfile?.name ?? 'No profile'}</span>
        <span className="text-muted2">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-72 z-50 bg-panel border border-border shadow-lg">
          <div className="max-h-64 overflow-y-auto">
            {profiles.map((profile) => {
              const isActive = profile.id === activeProfile?.id;
              return (
                <div
                  key={profile.id}
                  className={`flex items-center justify-between px-2 py-1.5 border-b border-border/50 hover:bg-panel2 ${
                    isActive ? 'bg-panel2' : ''
                  }`}
                >
                  <button
                    onClick={() => handleActivate(profile.id)}
                    className="flex-1 text-left flex items-center gap-2"
                  >
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ backgroundColor: profile.color }}
                    />
                    <span className={isActive ? 'text-tabBankroll' : 'text-text'}>
                      {profile.name}
                    </span>
                    {isActive && <span className="text-muted2 text-[10px]">active</span>}
                  </button>
                  {!isActive && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(profile.id);
                      }}
                      title="Delete profile"
                      className="px-1.5 py-0.5 text-muted2 hover:text-error text-xs"
                    >
                      ×
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <form
            onSubmit={handleCreate}
            className="flex flex-col gap-2 p-2 border-t border-border"
          >
            <input
              type="text"
              value={newName}
              onChange={(e) => {
                setNewName(e.target.value);
                setCreateError(null);
              }}
              placeholder="New profile name"
              className="px-2 py-1 bg-panel2 border border-border text-text text-xs"
            />

            {/* Purpose — drives Rule-B ROI bucketing */}
            <div className="flex items-center gap-1">
              <span className="text-muted2 text-[10px] w-12">Purpose</span>
              {(['edge', 'bonus'] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setKind(k)}
                  className={`px-2 py-0.5 text-[10px] border ${
                    kind === k
                      ? 'border-tabBankroll text-tabBankroll bg-tabBankroll/10'
                      : 'border-border text-muted2'
                  }`}
                >
                  {k === 'edge' ? 'Edge' : 'Bonus campaign'}
                </button>
              ))}
            </div>

            {/* Sharp accounts — reuse the shared pool or spin up fresh ones */}
            <div className="flex items-center gap-1">
              <span className="text-muted2 text-[10px] w-12">Sharp</span>
              {(['shared', 'fresh'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setSharpMode(m)}
                  className={`px-2 py-0.5 text-[10px] border ${
                    sharpMode === m
                      ? 'border-tabBankroll text-tabBankroll bg-tabBankroll/10'
                      : 'border-border text-muted2'
                  }`}
                >
                  {m === 'shared' ? 'Use mine' : 'Fresh'}
                </button>
              ))}
            </div>

            {sharpMode === 'fresh' && (
              <input
                type="text"
                value={freshLabel}
                onChange={(e) => setFreshLabel(e.target.value)}
                placeholder="Fresh sharp label (e.g. alt2)"
                className="px-2 py-1 bg-panel2 border border-border text-text text-xs"
              />
            )}

            <button
              type="submit"
              disabled={!newName.trim() || create.isPending}
              className="px-2 py-1 text-xs bg-tabBankroll/20 text-tabBankroll hover:bg-tabBankroll/30 disabled:opacity-50"
            >
              {create.isPending ? '…' : 'Create'}
            </button>
          </form>
          {createError && (
            <div className="px-2 pb-2 text-[10px] text-error">{createError}</div>
          )}
          {activate.error && (
            <div className="px-2 pb-2 text-[10px] text-error">
              {activate.error instanceof Error ? activate.error.message : 'Activation failed'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
