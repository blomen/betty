import { fetchJson } from './client';

export interface OppSnapshotSummary {
  total: number;
  distinct_events: number;
  mean_pinnacle_clv_pct: number | null;
  beat_close_pct: number | null;
}

export interface OppSnapshotHistoryPoint {
  detected_at: string;
  type: 'value' | 'arb' | 'reverse_value';
  pinnacle_clv_pct: number;
}

export interface OppSnapshotBreakdownRow {
  provider_id: string;
  type: 'value' | 'arb' | 'reverse_value';
  market: string;
  n: number;
  mean_pinnacle_clv_pct: number | null;
  mean_provider_clv_pct: number | null;
  mean_edge_at_detection: number | null;
}

export interface OppSnapshotStats {
  summary: OppSnapshotSummary;
  history: OppSnapshotHistoryPoint[];
  breakdown: OppSnapshotBreakdownRow[];
}

export const oppSnapshotsApi = {
  async getOppSnapshotStats(days = 30): Promise<OppSnapshotStats> {
    return fetchJson<OppSnapshotStats>(`/opp-snapshots/stats?days=${days}`);
  },
};
