import { useState, useCallback } from 'react';
import type { Message } from '@/types';

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Simply add message to the list - no LLM call
  const sendMessage = useCallback(
    (content: string) => {
      if (!content.trim()) return;

      const message: Message = {
        id: generateId(),
        role: 'assistant',
        content: content.trim(),
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, message]);
    },
    []
  );

  const stopGeneration = useCallback(() => {
    setIsLoading(false);
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
