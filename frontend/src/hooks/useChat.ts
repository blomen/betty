import { useState, useCallback, useRef } from 'react';
import type { Message, BettingContext } from '@/types';
import { streamChat, simulateChat } from '@/services/claude';

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function useChat(context: BettingContext) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || isLoading) return;

      const userMessage: Message = {
        id: generateId(),
        role: 'user',
        content: content.trim(),
        timestamp: new Date(),
      };

      const assistantMessage: Message = {
        id: generateId(),
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isStreaming: true,
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      setIsLoading(true);
      setError(null);

      abortControllerRef.current = new AbortController();

      const allMessages = [...messages, userMessage];

      const callbacks = {
        onToken: (token: string) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessage.id
                ? { ...m, content: m.content + token }
                : m
            )
          );
        },
        onComplete: (fullResponse: string) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessage.id
                ? { ...m, content: fullResponse || m.content, isStreaming: false }
                : m
            )
          );
          setIsLoading(false);
        },
        onError: (err: Error) => {
          setError(err.message);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessage.id
                ? { ...m, content: `Error: ${err.message}`, isStreaming: false }
                : m
            )
          );
          setIsLoading(false);
        },
      };

      try {
        await streamChat(
          allMessages,
          context,
          callbacks,
          abortControllerRef.current.signal
        );
      } catch {
        // If streaming fails, fall back to simulation
        await simulateChat(allMessages, context, callbacks);
      }
    },
    [messages, context, isLoading]
  );

  const stopGeneration = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setIsLoading(false);
    }
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return {
    messages,
    setMessages,
    isLoading,
    error,
    sendMessage,
    stopGeneration,
    clearMessages,
  };
}
