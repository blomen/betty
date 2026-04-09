import { useState, useEffect, useRef } from 'react';

export interface PriceStreamState {
  domOdds: number | null;
  apiOdds: number | null;
  fairOdds: number | null;
  edge: number | null;
  priceMatch: boolean;
  lastUpdate: Date | null;
}

const DEFAULT_STATE: PriceStreamState = {
  domOdds: null,
  apiOdds: null,
  fairOdds: null,
  edge: null,
  priceMatch: false,
  lastUpdate: null,
};

export function usePriceStream(
  providerId: string | null,
  betId: number | null,
): PriceStreamState {
  const [state, setState] = useState<PriceStreamState>(DEFAULT_STATE);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setState(DEFAULT_STATE);

    if (!providerId && betId === null) return;

    esRef.current?.close();

    const es = new EventSource('/api/mirror/stream/prices');
    esRef.current = es;

    es.addEventListener('price_update', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setState(prev => ({
        ...prev,
        domOdds: data.dom_odds ?? prev.domOdds,
        apiOdds: data.api_odds ?? prev.apiOdds,
        lastUpdate: new Date(),
      }));
    });

    es.addEventListener('price_verified', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.bet_id !== betId) return;
      setState(prev => ({
        ...prev,
        domOdds: data.dom_odds ?? prev.domOdds,
        apiOdds: data.api_odds ?? prev.apiOdds,
        priceMatch: data.price_match ?? prev.priceMatch,
        lastUpdate: new Date(),
      }));
    });

    es.addEventListener('edge_update', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.bet_id !== betId) return;
      setState(prev => ({
        ...prev,
        fairOdds: data.fair_odds ?? prev.fairOdds,
        edge: data.edge ?? prev.edge,
        lastUpdate: new Date(),
      }));
    });

    es.onerror = () => {
      es.close();
      esRef.current = null;
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [providerId, betId]);

  return state;
}
