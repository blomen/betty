import { describe, expect, it } from 'vitest';
import { currentActionableSteamKeys, selectNewSteamAlerts, steamKey } from './useSteamAlert';

type Bet = Parameters<typeof steamKey>[0];

const bet = (over: Partial<Bet> = {}): Bet => ({
  event_id: 'e1', market: 'moneyline', outcome: 'home', provider: 'polymarket',
  point: null, edge_pct: 6, annotations: { steam_signal: { direction: 'up', provider_count: 3 } },
  ...over,
}) as Bet;

const FUNDED = new Set(['polymarket', 'kalshi']);
const FLOOR = 3;

describe('selectNewSteamAlerts', () => {
  it('returns an unseen actionable steam bet', () => {
    const keys = selectNewSteamAlerts([bet()], new Set(), FUNDED, FLOOR);
    expect(keys).toEqual([steamKey(bet())]);
  });
  it('excludes bets with no steam_signal direction', () => {
    const b = bet({ annotations: { steam_signal: null } });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });
  it('excludes unfunded providers', () => {
    const b = bet({ provider: 'betsson' });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });
  it('excludes bets below the edge floor', () => {
    const b = bet({ edge_pct: 1 });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });
  it('excludes already-seen keys', () => {
    const b = bet();
    expect(selectNewSteamAlerts([b], new Set([steamKey(b)]), FUNDED, FLOOR)).toEqual([]);
  });
  it('dedupes within a batch', () => {
    const keys = selectNewSteamAlerts([bet(), bet()], new Set(), FUNDED, FLOOR);
    expect(keys).toEqual([steamKey(bet())]);
  });
});

describe('currentActionableSteamKeys', () => {
  it('returns all actionable steam keys regardless of seen', () => {
    const set = currentActionableSteamKeys([bet(), bet({ outcome: 'away' })], FUNDED, FLOOR);
    expect(set.has(steamKey(bet()))).toBe(true);
    expect(set.has(steamKey(bet({ outcome: 'away' })))).toBe(true);
    expect(set.has(steamKey(bet({ provider: 'betsson' })))).toBe(false);
  });
});

describe('steamKey', () => {
  it('is stable and includes provider + outcome + point', () => {
    expect(steamKey(bet({ point: -1.5 }))).toBe('e1|moneyline|home|polymarket|-1.5');
    expect(steamKey(bet({ point: null }))).toBe('e1|moneyline|home|polymarket|');
  });
});
