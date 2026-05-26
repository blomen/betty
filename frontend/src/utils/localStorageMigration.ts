/**
 * localStorage key migration helper (arnold → betty rename, PR C1, 2026-05-26).
 *
 * On first read of a betty:* key, if the value is missing and the legacy
 * arnold:* twin exists, copy it over and delete the old key. Subsequent
 * reads/writes use only the new key.
 *
 * Once enough time has passed that no live browser still has the
 * arnold:* keys (a few weeks of normal use), this helper and its callsites
 * can be ripped out — they're a no-op when the legacy key isn't present.
 */

/**
 * Read a value, migrating from the legacy `arnold:` key to the new
 * `betty:` key on first hit.
 */
export function migratedLocalStorageGet(newKey: string, oldKey: string): string | null {
  try {
    const newVal = localStorage.getItem(newKey);
    if (newVal !== null) return newVal;
    const oldVal = localStorage.getItem(oldKey);
    if (oldVal !== null) {
      try {
        localStorage.setItem(newKey, oldVal);
        localStorage.removeItem(oldKey);
      } catch {
        /* localStorage full / disabled — fall through and return the legacy value */
      }
      return oldVal;
    }
    return null;
  } catch {
    return null;
  }
}
