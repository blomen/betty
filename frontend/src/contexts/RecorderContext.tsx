import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from '@/services/api';

interface RecorderContextValue {
  isRecording: boolean;
  recordingProvider: string | null;
  recordingWorkflow: string | null;
  actionCount: number;
  startAutoRecord: (provider: string, workflow: string) => Promise<void>;
  stopAutoRecord: () => Promise<void>;
}

const RecorderContext = createContext<RecorderContextValue | null>(null);

function getWsUrl(): string {
  const loc = window.location;
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${loc.host}/ws/recorder`;
}

export function RecorderProvider({ children }: { children: ReactNode }) {
  const [isRecording, setIsRecording] = useState(false);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [recordingProvider, setRecordingProvider] = useState<string | null>(null);
  const [recordingWorkflow, setRecordingWorkflow] = useState<string | null>(null);
  const [actionCount, setActionCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check for orphaned recording on mount
  useEffect(() => {
    api.getRecorderStatus().then((s) => {
      if (s.is_recording && s.session_id) {
        setIsRecording(true);
        setSessionId(s.session_id);
        setActionCount(s.action_count ?? 0);
      }
    }).catch(() => {});
  }, []);

  // WebSocket for live action count
  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 15_000);
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'action') {
          setActionCount((c) => c + 1);
          // Auto-detect provider from actions if not set
          if (msg.data?.provider_id) {
            setRecordingProvider((prev) => prev || msg.data.provider_id);
          }
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      if (pingRef.current) clearInterval(pingRef.current);
      pingRef.current = null;
    };

    ws.onerror = () => ws.close();
  }, []);

  const disconnectWs = useCallback(() => {
    if (pingRef.current) clearInterval(pingRef.current);
    pingRef.current = null;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (isRecording) connectWs();
    else disconnectWs();
    return () => disconnectWs();
  }, [isRecording, connectWs, disconnectWs]);

  const startAutoRecord = useCallback(async (provider: string, workflow: string) => {
    // If already recording, stop the current one first
    if (isRecording && sessionId) {
      try { await api.stopRecording(sessionId); } catch { /* ignore */ }
      disconnectWs();
    }

    setRecordingProvider(provider);
    setRecordingWorkflow(workflow);
    setActionCount(0);

    try {
      const res = await api.startRecording({
        cdp_url: 'http://localhost:9222',
        action_type: workflow,
        label: provider,
      });
      setSessionId(res.session_id);
      setIsRecording(true);
    } catch {
      // Best-effort: CDP not available, silently skip recording
      // Provider window will still open — recording just won't happen
      setRecordingProvider(null);
      setRecordingWorkflow(null);
    }
  }, [isRecording, sessionId, disconnectWs]);

  const stopAutoRecord = useCallback(async () => {
    if (!sessionId) {
      setIsRecording(false);
      setRecordingProvider(null);
      setRecordingWorkflow(null);
      return;
    }

    try {
      await api.stopRecording(sessionId);
    } catch { /* ignore */ }

    setIsRecording(false);
    setSessionId(null);
    setRecordingProvider(null);
    setRecordingWorkflow(null);
    setActionCount(0);
  }, [sessionId]);

  return (
    <RecorderContext.Provider value={{
      isRecording,
      recordingProvider,
      recordingWorkflow,
      actionCount,
      startAutoRecord,
      stopAutoRecord,
    }}>
      {children}
    </RecorderContext.Provider>
  );
}

export function useRecorder(): RecorderContextValue {
  const ctx = useContext(RecorderContext);
  if (!ctx) throw new Error('useRecorder must be used within RecorderProvider');
  return ctx;
}
