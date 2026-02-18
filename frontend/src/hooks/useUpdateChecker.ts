import { useEffect, useState } from 'react';

interface VersionInfo {
  version: string;
  data_dir: string;
  is_bundled: boolean;
}

interface UpdateState {
  updateAvailable: boolean;
  currentVersion: string | null;
  latestVersion: string | null;
  downloadUrl: string | null;
}

const GITHUB_REPO = 'blomen/BankrollBBQ';

export function useUpdateChecker(): UpdateState {
  const [state, setState] = useState<UpdateState>({
    updateAvailable: false,
    currentVersion: null,
    latestVersion: null,
    downloadUrl: null,
  });

  useEffect(() => {
    checkForUpdates();
  }, []);

  async function checkForUpdates() {
    try {
      // Get current version from our API
      const versionRes = await fetch('/api/version');
      if (!versionRes.ok) return;
      const info: VersionInfo = await versionRes.json();

      // Only check updates in bundled mode
      if (!info.is_bundled) return;

      setState(prev => ({ ...prev, currentVersion: info.version }));

      // Check GitHub releases
      const releasesRes = await fetch(
        `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`,
        { headers: { Accept: 'application/vnd.github.v3+json' } }
      );
      if (!releasesRes.ok) return;
      const release = await releasesRes.json();

      const latestTag = release.tag_name?.replace(/^v/, '') || '';
      if (latestTag && latestTag !== info.version) {
        setState({
          updateAvailable: true,
          currentVersion: info.version,
          latestVersion: latestTag,
          downloadUrl: release.html_url || null,
        });
      }
    } catch {
      // Silent fail — update check is non-critical
    }
  }

  return state;
}
