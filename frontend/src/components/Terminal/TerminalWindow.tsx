import { useEffect, useRef } from 'react';
import type { BettingContext } from '@/types';
import { useChat } from '@/hooks/useChat';
import { TerminalHeader } from './TerminalHeader';
import { TerminalInput } from './TerminalInput';
import { ChatMessage } from './ChatMessage';
import { WelcomeMessage } from './WelcomeMessage';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
  isContextLoading: boolean;
}

export function TerminalWindow({
  context,
  onRefresh,
  isContextLoading,
}: TerminalWindowProps) {
  const { messages, isLoading, sendMessage, stopGeneration, clearMessages } =
    useChat(context);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="flex flex-col h-full bg-terminal-bg">
      {/* Header */}
      <TerminalHeader
        context={context}
        isLoading={isContextLoading}
        onClear={clearMessages}
        onRefresh={onRefresh}
      />

      {/* Messages area - centered */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <WelcomeMessage context={context} />
          ) : (
            <div className="pb-4">
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Input - centered */}
      <div className="max-w-4xl mx-auto w-full">
        <TerminalInput
          onSend={sendMessage}
          onStop={stopGeneration}
          isLoading={isLoading}
        />
      </div>
    </div>
  );
}
