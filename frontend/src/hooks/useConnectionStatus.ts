// frontend/src/hooks/useConnectionStatus.ts
import { useState, useEffect } from 'react';
import { connectionManager, type ConnectionState } from '@/services/connectionManager';

export interface ConnectionStatus {
  status: ConnectionState;
  latencyMs: number | null;
  message: string;
}

export function useConnectionStatus(): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>({
    status: connectionManager.getState(),
    latencyMs: connectionManager.getLatency(),
    message: connectionManager.getMessage(),
  });

  useEffect(() => {
    const unsub = connectionManager.subscribe((state, latencyMs, message) => {
      setStatus({ status: state, latencyMs, message });
    });
    return unsub;
  }, []);

  return status;
}
