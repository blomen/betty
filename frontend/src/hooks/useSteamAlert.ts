import { useEffect, useRef, useState } from 'react';

/** Minimum edge (%) for a steam bet to be alert-worthy. */
export const STEAM_ALERT_MIN_EDGE_PCT = 3;

/** Shape of the bet fields this module reads (a subset of the play-batch row). */
export interface SteamAlertBet {
  event_id: string;
  market: string;
  outcome: string;
  provider: string;
  point?: number | null;
  edge_pct?: number | null;
  annotations?: {
    steam_signal?: { direction?: 'up' | 'down'; provider_count?: number } | null;
  } | null;
}

/** Stable identity for a bet row. */
export function steamKey(b: SteamAlertBet): string {
  return `${b.event_id}|${b.market}|${b.outcome}|${b.provider}|${b.point ?? ''}`;
}

function isActionableSteam(b: SteamAlertBet, funded: Set<string>, edgeFloor: number): boolean {
  const dir = b.annotations?.steam_signal?.direction;
  if (!dir) return false;
  if (!funded.has(b.provider)) return false;
  if ((b.edge_pct ?? 0) < edgeFloor) return false;
  return true;
}

/** All currently-actionable steam keys (for pin/highlight), regardless of seen. */
export function currentActionableSteamKeys(
  bets: SteamAlertBet[],
  funded: Set<string>,
  edgeFloor: number,
): Set<string> {
  const out = new Set<string>();
  for (const b of bets) {
    if (isActionableSteam(b, funded, edgeFloor)) out.add(steamKey(b));
  }
  return out;
}

/** Keys of actionable steam bets not yet in `seen` (deduped). Pure. */
export function selectNewSteamAlerts(
  bets: SteamAlertBet[],
  seen: Set<string>,
  funded: Set<string>,
  edgeFloor: number,
): string[] {
  const fresh: string[] = [];
  const local = new Set<string>();
  for (const b of bets) {
    if (!isActionableSteam(b, funded, edgeFloor)) continue;
    const k = steamKey(b);
    if (seen.has(k) || local.has(k)) continue;
    local.add(k);
    fresh.push(k);
  }
  return fresh;
}

/** Best-effort short alert tone via WebAudio (no asset). Silent on failure. */
function playBeep(): void {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.36);
    osc.onended = () => ctx.close().catch(() => {});
  } catch {
    /* audio blocked (no user gesture yet) — pin/highlight still convey the alert */
  }
}

/**
 * Fires a one-time beep when a NEW actionable steam bet appears in `bets`, and
 * returns the set of all currently-actionable steam keys (for pin + highlight).
 * Session-scoped: a bet is alerted once; reappearance does not re-alert.
 */
export function useSteamAlert(
  bets: SteamAlertBet[],
  funded: Set<string>,
  edgeFloor: number = STEAM_ALERT_MIN_EDGE_PCT,
): Set<string> {
  const seen = useRef<Set<string>>(new Set());
  const [activeKeys, setActiveKeys] = useState<Set<string>>(new Set());

  const fundedSig = [...funded].sort().join(',');
  useEffect(() => {
    const fresh = selectNewSteamAlerts(bets, seen.current, funded, edgeFloor);
    setActiveKeys(currentActionableSteamKeys(bets, funded, edgeFloor));
    if (fresh.length > 0) {
      for (const k of fresh) seen.current.add(k);
      playBeep();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bets, edgeFloor, fundedSig]);

  return activeKeys;
}
