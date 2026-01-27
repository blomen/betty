import { useState, useEffect, useCallback } from 'react';
import type { OpportunityWithEvent, EventSummary } from '@/types';
import { api } from '@/services/api';

interface OpportunitiesFilters {
  type?: 'arbitrage' | 'value' | 'bonus';
  provider?: string;
  market?: string;
  sport?: string;
  minValue?: number;
}

export function useOpportunities(filters: OpportunitiesFilters = {}, refreshInterval = 10000) {
  const [opportunities, setOpportunities] = useState<OpportunityWithEvent[]>([]);
  const [eventsMap, setEventsMap] = useState<Map<string, EventSummary>>(new Map());
  const [count, setCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { opportunities: opps, count: oppCount } = await api.getOpportunities(
        filters.type,
        true,
        filters.provider,
        undefined,
        filters.provider,
        filters.market,
        filters.sport,
        filters.minValue
      );

      // Extract unique event IDs
      const eventIds = [...new Set(opps.map((o) => o.event_id))];

      // Fetch event details for all unique events
      const eventPromises = eventIds.map((id) => api.getEvent(id));
      const events = await Promise.all(eventPromises);

      // Create events map
      const newEventsMap = new Map<string, EventSummary>();
      events.forEach((event) => {
        newEventsMap.set(event.id, {
          id: event.id,
          sport: event.sport,
          league: event.league,
          home_team: event.home_team,
          away_team: event.away_team,
          start_time: event.start_time,
          odds_count: event.odds_count,
        });
      });

      // Enrich opportunities with event details
      const enrichedOpps: OpportunityWithEvent[] = opps.map((opp) => ({
        ...opp,
        event: newEventsMap.get(opp.event_id),
      }));

      setOpportunities(enrichedOpps);
      setEventsMap(newEventsMap);
      setCount(oppCount);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load opportunities');
    } finally {
      setIsLoading(false);
    }
  }, [filters.type, filters.provider, filters.market, filters.sport, filters.minValue]);

  useEffect(() => {
    refresh();

    if (refreshInterval > 0) {
      const interval = setInterval(refresh, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [refresh, refreshInterval]);

  return {
    opportunities,
    eventsMap,
    count,
    isLoading,
    error,
    refresh,
  };
}
