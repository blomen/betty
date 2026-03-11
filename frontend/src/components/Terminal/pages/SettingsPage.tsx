import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import type { ExtractionSettingsResponse, ExtractionPlatform } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';

const TIER_BADGES: Record<string, { label: string; cls: string }> = {
  sharp: { label: 'SHARP', cls: 'text-yellow-400 bg-yellow-400/10' },
  api_soft: { label: 'API', cls: 'text-blue-400 bg-blue-400/10' },
  browser_soft: { label: 'BROWSER', cls: 'text-orange-400 bg-orange-400/10' },
};

/* ── Checkbox ─────────────────────────────────────────────── */

const SETTINGS_ACCENT = TAB_COLORS.settings; // #9AA0A6

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
  const active = checked || indeterminate;
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onChange(); }}
      disabled={disabled}
      className={`w-[18px] h-[18px] rounded-[4px] flex-shrink-0 flex items-center justify-center border-2 transition-all duration-150 ${
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
      } ${
        !active ? 'border-muted/40 hover:border-muted/60' : 'border-transparent'
      }`}
      style={active ? { background: SETTINGS_ACCENT, borderColor: SETTINGS_ACCENT } : undefined}
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

  // Sites not already shown as toggleable providers
  const sites = platform.sites ?? [];
  const providerNames = new Set(providers.map(p => p.name));
  const extraSites = sites.filter(s => !providerNames.has(s));

  return (
    <div className="border border-border/30 overflow-hidden">
      {/* Platform header */}
      <div
        className={`flex items-center gap-3 px-3 py-2 bg-panel ${
          !isSingle || extraSites.length > 0 ? 'cursor-pointer hover:bg-panel2/30' : ''
        }`}
        onClick={() => (!isSingle || extraSites.length > 0) && setExpanded(!expanded)}
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
        {extraSites.length > 0 && (
          <span className="text-[11px] text-muted2">{sites.length} {sites.length === 1 ? 'site' : 'sites'}</span>
        )}
        <span className="ml-auto text-xs text-muted font-mono tabular-nums">
          {enabledCount}/{providers.length}
        </span>
        {(!isSingle || extraSites.length > 0) && (
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className={`text-muted/50 transition-transform duration-150 ${expanded ? 'rotate-180' : ''}`}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </div>

      {/* Provider rows + extra sites */}
      {expanded && (!isSingle || extraSites.length > 0) && (
        <div className="border-t border-border/20">
          {!isSingle && providers.map(provider => (
            <div
              key={provider.provider_id}
              className="flex items-center gap-3 pl-9 pr-3 py-1.5 hover:bg-panel2/20"
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
          {extraSites.length > 0 && (
            <div className="px-9 py-1.5 text-[11px] text-muted2">
              {extraSites.join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

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

  const settingsColor = TAB_COLORS.settings;

  if (isLoading) {
    return (
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="settings" color={settingsColor} size={16} />
          Settings
        </h2>
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="settings" color={settingsColor} size={16} />
        Settings
      </h2>

      {/* Sub-tab selector */}
      <div className="flex gap-1 border-b border-border">
        <button
          className="px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] border-[#9AA0A6] text-text"
        >
          Extraction
        </button>
      </div>

      {error && (
        <div className="px-3 py-2 bg-error/10 border border-error/30 text-error text-xs flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-error/60 hover:text-error ml-2">x</button>
        </div>
      )}

      <p className="text-muted text-xs">
        Toggle platforms and individual sites for extraction. Disabled providers are skipped on the next run.
      </p>

      <div className="border-l-2 border-[#9AA0A6] space-y-2 pl-0">
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
