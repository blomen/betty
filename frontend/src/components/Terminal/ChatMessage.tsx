import { memo } from 'react';
import type { Message } from '@/types';
import { StreamingText } from './StreamingText';

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo(function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';

  return (
    <div className="border-b border-terminal-border/30 py-3 px-4 font-mono">
      {/* Terminal-style header */}
      <div className="flex items-center gap-2 mb-2">
        <span className={`font-bold ${isUser ? 'text-terminal-muted' : 'text-terminal-accent'}`}>
          {isUser ? '[>]' : '[*]'}
        </span>
        <span className={`text-xs font-medium uppercase tracking-wide ${
          isUser ? 'text-terminal-muted' : 'text-terminal-accent'
        }`}>
          {isUser ? 'you' : 'oddopp'}
        </span>
        <span className="text-[10px] text-terminal-muted/50 ml-auto">
          {formatTime(message.timestamp)}
        </span>
      </div>

      {/* Message content */}
      <div className="pl-6">
        {isUser ? (
          <p className="text-terminal-text whitespace-pre-wrap leading-relaxed">{message.content}</p>
        ) : (
          <StreamingText
            content={message.content}
            isStreaming={message.isStreaming}
          />
        )}
      </div>
    </div>
  );
});

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}
