import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import type { ExtractionSettingsResponse, ExtractionPlatform } from '@/services/api';

const TIER_BADGES: Record<string, { label: string; cls: string }> = {
  sharp: { label: 'SHARP', cls: 'text-yellow-400 bg-yellow-400/10' },
  api_soft: { label: 'API', cls: 'text-blue-400 bg-blue-400/10' },
  browser_soft: { label: 'BROWSER', cls: 'text-orange-400 bg-orange-400/10' },
};

/* ── Checkbox ─────────────────────────────────────────────── */

function Checkbox({
  checked,
  indeterminate = false,
  disabled = false,
  onChange,
}: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onChange: () => void;
}) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onChange(); }}
      disabled={disabled}
      className={`w-[18px] h-[18px] rounded-[4px] flex-shrink-0 flex items-center justify-center border-2 transition-all duration-150 ${
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
      } ${
        checked || indeterminate
          ? 'bg-success border-success'
          : 'border-muted/40 hover:border-muted/60'
      }`}
    >
      {checked && !indeterminate && (
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
          <path d="M2.5 6L5 8.5L9.5 3.5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      )}
      {indeterminate && (
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
          <path d="M3 6H9" stroke="white" strokeWidth="2" strokeLinecap="round"/>
        </svg>
      )}
    </button>
  );
}

/* ── Platform Section ─────────────────────────────────────── */

function PlatformSection({
  platform,
  toggling,
  onToggleProvider,
  onTogglePlatform,
}: {
  platform: ExtractionPlatform;
  toggling: Set<string>;
  onToggleProvider: (providerId: string, enabled: boolean) => void;
  onTogglePlatform: (platformId: string, providerIds: string[], enabled: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const providers = platform.providers;
  const allEnabled = providers.every(p => p.enabled);
  const someEnabled = providers.some(p => p.enabled);
  const indeterminate = someEnabled && !allEnabled;
  const enabledCount = providers.filter(p => p.enabled).length;
  const isSingle = providers.length === 1;
  const tierInfo = TIER_BADGES[platform.tier];
  const isToggling = providers.some(p => toggling.has(p.provider_id));

  return (
    <div className="border border-border/40 rounded-lg overflow-hidden">
      {/* Platform header */}
      <div
        className={`flex items-center gap-3 px-3 py-2.5 ${
          isSingle ? '' : 'cursor-pointer hover:bg-panel2/30'
        }`}
        onClick={() => !isSingle && setExpanded(!expanded)}
      >
        <Checkbox
          checked={allEnabled}
          indeterminate={indeterminate}
          disabled={isToggling}
          onChange={() => {
            if (isSingle) {
              onToggleProvider(providers[0].provider_id, !providers[0].enabled);
            } else {
              onTogglePlatform(
                platform.platform_id,
                providers.map(p => p.provider_id),
                !allEnabled,
              );
            }
          }}
        />
        <span className={`text-sm font-semibold ${allEnabled || someEnabled ? 'text-text' : 'text-muted'}`}>
          {platform.platform_name}
        </span>
        {tierInfo && (
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${tierInfo.cls}`}>
            {tierInfo.label}
          </span>
        )}
        <span className="ml-auto text-xs text-muted font-mono tabular-nums">
          {enabledCount}/{providers.length}
        </span>
        {!isSingle && (
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className={`text-muted/50 transition-transform duration-150 ${expanded ? 'rotate-180' : ''}`}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </div>

      {/* Provider rows */}
      {!isSingle && expanded && (
        <div className="border-t border-border/20">
          {providers.map(provider => (
            <div
              key={provider.provider_id}
              className="flex items-center gap-3 pl-9 pr-3 py-1.5 hover:bg-panel/30"
            >
              <Checkbox
                checked={provider.enabled}
                disabled={toggling.has(provider.provider_id)}
                onChange={() => onToggleProvider(provider.provider_id, !provider.enabled)}
              />
              <span className={`text-sm ${provider.enabled ? 'text-text' : 'text-muted'}`}>
                {provider.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Settings Gear Icon ───────────────────────────────────── */

const GearIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9AA0A6" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>
);

/* ── Main Page ────────────────────────────────────────────── */

export function SettingsPage() {
  const [settings, setSettings] = useState<ExtractionSettingsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [toggling, setToggling] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await api.getExtractionSettings();
      setSettings(data);
    } catch (err) {
      setError('Failed to load extraction settings');
      console.error('Failed to load settings:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  const updateProviders = (idSet: Set<string>, enabled: boolean) => {
    setSettings(prev => {
      if (!prev) return prev;
      return {
        ...prev,
        platforms: prev.platforms.map(plat => ({
          ...plat,
          providers: plat.providers.map(p =>
            idSet.has(p.provider_id) ? { ...p, enabled } : p
          ),
        })),
      };
    });
  };

  const handleToggleProvider = async (providerId: string, enabled: boolean) => {
    setToggling(prev => new Set(prev).add(providerId));
    updateProviders(new Set([providerId]), enabled);
    try {
      await api.toggleExtractionProvider(providerId, enabled);
    } catch (err) {
      console.error('Toggle failed:', err);
      loadSettings();
    } finally {
      setToggling(prev => { const next = new Set(prev); next.delete(providerId); return next; });
    }
  };

  const handleTogglePlatform = async (_platformId: string, providerIds: string[], enabled: boolean) => {
    const ids = new Set(providerIds);
    setToggling(prev => { const next = new Set(prev); ids.forEach(id => next.add(id)); return next; });
    updateProviders(ids, enabled);
    try {
      await api.toggleExtractionBatch(providerIds, enabled);
    } catch (err) {
      console.error('Batch toggle failed:', err);
      loadSettings();
    } finally {
      setToggling(prev => { const next = new Set(prev); ids.forEach(id => next.delete(id)); return next; });
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <GearIcon />
          Settings
        </h2>
        <div className="text-muted text-sm">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <GearIcon />
        Settings
      </h2>

      <div className="flex gap-1">
        <button className="px-3 py-1 text-xs font-semibold rounded bg-panel2 text-text border border-border">
          Extraction
        </button>
      </div>

      {error && (
        <div className="text-error text-sm bg-error/10 border border-error/30 px-3 py-2 rounded">
          {error}
        </div>
      )}

      <p className="text-muted text-xs">
        Toggle platforms and individual sites for extraction. Disabled providers are skipped on the next run.
      </p>

      <div className="space-y-2">
        {settings?.platforms.map(platform => (
          <PlatformSection
            key={platform.platform_id}
            platform={platform}
            toggling={toggling}
            onToggleProvider={handleToggleProvider}
            onTogglePlatform={handleTogglePlatform}
          />
        ))}
      </div>
    </div>
  );
}
