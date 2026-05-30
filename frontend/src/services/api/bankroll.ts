import type { BankrollInfo, BankrollStats, BankrollExposure, AllocationEnvelope } from '@/types';
import { fetchJson } from './client';

export const bankrollApi = {
  async getBankroll(profileId?: number): Promise<BankrollInfo> {
    const q = profileId != null ? `?profile_id=${profileId}` : '';
    return fetchJson<BankrollInfo>(`/bankroll${q}`);
  },

  async getBankrollStats(profileId?: number): Promise<BankrollStats> {
    const q = profileId != null ? `?profile_id=${profileId}` : '';
    return fetchJson<BankrollStats>(`/bankroll/stats${q}`);
  },

  async getBankrollStatus(): Promise<{
    profile_id: number;
    profile_name: string;
    bankroll: number;
    bonus_progress: Record<string, import('@/types').BonusProgressEntry>;
  }> {
    return fetchJson('/bankroll/status');
  },

  async setAllBalances(
    balance: number,
    providerIds?: string[]
  ): Promise<{
    success: boolean;
    updated_count: number;
    balance_per_provider: number;
    total_balance: number;
  }> {
    return fetchJson('/bankroll/set-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        balance,
        provider_ids: providerIds,
      }),
    });
  },

  async allocate(liquidAmount: number | null): Promise<AllocationEnvelope> {
    return fetchJson<AllocationEnvelope>('/bankroll/allocate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ liquid_amount: liquidAmount }),
    });
  },

  async getLiquidBalance(): Promise<{ liquid_balance: number }> {
    return fetchJson('/bankroll/liquid');
  },

  async setBalance(
    providerId: string,
    balance: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    old_balance: number;
    new_balance: number;
  }> {
    return fetchJson(`/bankroll/set/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance }),
    });
  },

  async resetAllBalances(): Promise<{
    success: boolean;
    reset_count: number;
    message: string;
  }> {
    return fetchJson('/bankroll/reset-all', { method: 'POST' });
  },

  async getBankrollExposure(): Promise<BankrollExposure> {
    return fetchJson<BankrollExposure>('/bankroll/exposure');
  },

  async getDrawdownStatus(): Promise<DrawdownStatus> {
    return fetchJson<DrawdownStatus>('/bankroll/drawdown');
  },

  async getBankrollFull(): Promise<{
    info: BankrollInfo;
    exposure: BankrollExposure;
    stats: BankrollStats;
  }> {
    // Try the combined endpoint; fall back to 3 parallel calls if the deployed
    // backend doesn't have /full yet (404).
    try {
      return await fetchJson('/bankroll/full');
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status !== 404) throw err;
      const [info, exposure, stats] = await Promise.all([
        fetchJson<BankrollInfo>('/bankroll'),
        fetchJson<BankrollExposure>('/bankroll/exposure'),
        fetchJson<BankrollStats>('/bankroll/stats'),
      ]);
      return { info, exposure, stats };
    }
  },

  async depositWithBonus(
    providerId: string,
    amount: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    deposit: number;
    bonus_claimed: number;
    total_added: number;
    old_balance: number;
    new_balance: number;
    bonus_status: string | null;
    bonus_type: string | null;
    bonus_limit: number | null;
  }> {
    return fetchJson(`/bankroll/deposit/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async claimBonus(providerId: string): Promise<{ success: boolean; provider_id: string; status: string }> {
    return fetchJson(`/bankroll/claim-bonus/${providerId}`, { method: 'POST' });
  },
};

export type DrawdownProviderRow = {
  provider_id: string;
  pnl_sek_7d: number;
  n_bets: number;
  breached: boolean;
};

export type DrawdownStatus = {
  enabled: boolean;
  threshold_pct: number;
  min_bets_for_breach: number;
  stake_bankroll_sek: number;
  providers: DrawdownProviderRow[];
};
