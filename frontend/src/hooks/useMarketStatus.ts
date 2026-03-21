import { useState, useEffect } from 'react';

export type MarketState = 'open' | 'closed' | 'halt';

interface MarketStatus {
  state: MarketState;
  label: string;
  opensIn: string | null; // human-readable time until next open
}

/**
 * CME Globex hours (NQ futures):
 *   Sunday 18:00 ET → Friday 17:00 ET
 *   Daily halt: 17:00–18:00 ET
 *
 * All times converted to Europe/Stockholm for display.
 */
function getGlobexState(now: Date): MarketStatus {
  // Convert to US/Eastern to check Globex schedule
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const wd = et.getDay(); // 0=Sun, 6=Sat
  const hour = et.getHours();
  const min = et.getMinutes();

  // Saturday: fully closed
  if (wd === 6) {
    return { state: 'closed', label: 'CLOSED', opensIn: formatOpensIn(now, nextSundayOpen(now)) };
  }

  // Friday after 17:00 ET: closed for weekend
  if (wd === 5 && hour >= 17) {
    return { state: 'closed', label: 'CLOSED', opensIn: formatOpensIn(now, nextSundayOpen(now)) };
  }

  // Sunday before 18:00 ET: closed (weekend)
  if (wd === 0 && hour < 18) {
    return { state: 'closed', label: 'CLOSED', opensIn: formatOpensIn(now, nextOpenToday18ET(now)) };
  }

  // Daily halt: 17:00–18:00 ET (Mon-Thu)
  if (hour === 17) {
    return { state: 'halt', label: 'HALT', opensIn: formatOpensIn(now, nextDailyOpen(now)) };
  }

  return { state: 'open', label: 'LIVE', opensIn: null };
}

/** Next Sunday 18:00 ET as a Date */
function nextSundayOpen(now: Date): Date {
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const wd = et.getDay();
  let daysUntilSunday = (7 - wd) % 7;
  if (daysUntilSunday === 0 && et.getHours() >= 18) daysUntilSunday = 7;
  if (daysUntilSunday === 0 && et.getHours() < 18) daysUntilSunday = 0;

  const target = new Date(et);
  target.setDate(target.getDate() + daysUntilSunday);
  target.setHours(18, 0, 0, 0);

  // Convert back: difference from ET target to now
  const etNow = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const diffMs = target.getTime() - etNow.getTime();
  return new Date(now.getTime() + diffMs);
}

/** Next 18:00 ET today (for daily halt) */
function nextDailyOpen(now: Date): Date {
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const target = new Date(et);
  target.setHours(18, 0, 0, 0);
  const diffMs = target.getTime() - et.getTime();
  return new Date(now.getTime() + diffMs);
}

/** Next open today at 18:00 ET (Sunday case) */
function nextOpenToday18ET(now: Date): Date {
  return nextDailyOpen(now);
}

function formatOpensIn(now: Date, opensAt: Date): string {
  let diffMs = opensAt.getTime() - now.getTime();
  if (diffMs < 0) diffMs = 0;

  const totalMinutes = Math.floor(diffMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

export function useMarketStatus(): MarketStatus {
  const [status, setStatus] = useState<MarketStatus>(() => getGlobexState(new Date()));

  useEffect(() => {
    const id = setInterval(() => {
      setStatus(getGlobexState(new Date()));
    }, 30_000); // update every 30s
    return () => clearInterval(id);
  }, []);

  return status;
}
