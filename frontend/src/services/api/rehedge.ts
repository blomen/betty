/** Typed client for /api/opportunities/rehedge. */

export interface RehedgeOriginalBet {
  provider: string | null;
  market: string | null;
  outcome: string | null;
  point: number | null;
  odds: number | null;
  stake: number | null;
  currency: string | null;
}

export interface RehedgeEvent {
  id: string;
  home_team: string | null;
  away_team: string | null;
  start_time: string | null;
  sport: string | null;
}

export interface RehedgeOpportunity {
  opportunity_id: number;
  case: 'post_placement_middle' | 'clv_inversion_salvage';
  original_bet_id: number;
  original_bet: RehedgeOriginalBet | null;
  hedge_provider: string;
  hedge_market: string;
  hedge_outcome: string;
  hedge_point: number | null;
  hedge_odds: number;
  recommended_stake_sek: number;
  key_number: number | null;
  wing_loss_pct: number | null;
  event: RehedgeEvent;
  detected_at: string | null;
}

async function fetchRehedgeOpportunities(): Promise<RehedgeOpportunity[]> {
  const resp = await fetch('/api/opportunities/rehedge');
  if (!resp.ok) {
    throw new Error(`/api/opportunities/rehedge failed: ${resp.status}`);
  }
  const data: { opportunities: RehedgeOpportunity[] } = await resp.json();
  return data.opportunities;
}

export const rehedgeApi = {
  fetchRehedgeOpportunities,
};
