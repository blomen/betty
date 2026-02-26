/**
 * Opens a provider URL in a named browser window.
 *
 * Uses window.open with a per-provider window name ("bbq_<provider>")
 * so clicking "Place Bet" or "Deposit" for the same provider reuses
 * the existing tab instead of spawning new windows.
 *
 * If the window already exists, it navigates to the new URL and focuses it.
 */

const providerWindows: Record<string, Window | null> = {};

export function openProviderWindow(url: string | null, windowName: string): Window | null {
  if (!url) return null;

  // Check if we already have a reference and it's still open
  const existing = providerWindows[windowName];
  if (existing && !existing.closed) {
    try {
      existing.location.href = url;
      existing.focus();
      return existing;
    } catch {
      // Cross-origin or closed — fall through to open new
    }
  }

  // Open new (or reuse by name if browser still has it)
  const win = window.open(url, windowName);
  if (win) {
    providerWindows[windowName] = win;
    win.focus();
  }
  return win;
}
