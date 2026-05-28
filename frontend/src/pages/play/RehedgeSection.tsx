import { useEffect, useState } from 'react';

import { api } from '../../services/api';
import type { RehedgeOpportunity } from '../../services/api/rehedge';

/**
 * Read-only display of open-position rehedge candidates.
 *
 * Phase 1: shows the candidate side-by-side with the original bet so the
 * bettor can manually place the hedge. Phase 2 (separate plan) wires
 * auto-placement via arb_runner.
 */
export function RehedgeSection() {
  const [opps, setOpps] = useState<RehedgeOpportunity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.fetchRehedgeOpportunities();
        if (!cancelled) {
          setOpps(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 30_000); // poll every 30s
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading) return <div className="text-sm text-gray-500">Loading rehedge candidates…</div>;
  if (error) return <div className="text-sm text-red-600">Rehedge fetch failed: {error}</div>;
  if (opps.length === 0) {
    return (
      <div className="text-sm text-gray-500">
        No active rehedge candidates. The scanner runs every 5 minutes.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-700">
        Open-position rehedge ({opps.length})
      </h3>
      {opps.map((o) => (
        <RehedgeCard key={o.opportunity_id} opp={o} />
      ))}
    </div>
  );
}

function RehedgeCard({ opp }: { opp: RehedgeOpportunity }) {
  const bet = opp.original_bet;
  const event = opp.event;
  return (
    <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs">
      <div className="mb-2 font-medium">
        {event.home_team} vs {event.away_team}
        {opp.key_number != null && (
          <span className="ml-2 rounded bg-amber-200 px-1.5 py-0.5 text-amber-900">
            middle on {opp.key_number}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="font-semibold text-gray-700">You bet</div>
          {bet ? (
            <div>
              {bet.provider} {bet.outcome} {bet.point}<br />
              @ {bet.odds} for {bet.stake} {bet.currency}
            </div>
          ) : (
            <div className="text-gray-500">(original bet missing)</div>
          )}
        </div>
        <div>
          <div className="font-semibold text-gray-700">Hedge with</div>
          <div>
            {opp.hedge_provider} {opp.hedge_outcome} {opp.hedge_point}<br />
            @ {opp.hedge_odds} for {opp.recommended_stake_sek.toFixed(2)} SEK
          </div>
        </div>
      </div>
      {opp.wing_loss_pct != null && (
        <div className="mt-2 text-gray-600">
          Wing loss if no middle: {(opp.wing_loss_pct * 100).toFixed(2)}%
        </div>
      )}
    </div>
  );
}
