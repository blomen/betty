import { memo } from 'react';
import type { Message } from '@/types';
import { StreamingText } from './StreamingText';

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo(function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';

  return (
    <div className="animate-fadeIn">
      <div className="flex items-start gap-3 py-4 px-4">
        {/* ASCII Avatar */}
        <div
          className={`flex-shrink-0 w-7 h-7 rounded flex items-center justify-center font-bold text-sm ${
            isUser
              ? 'bg-terminal-border text-terminal-muted'
              : 'bg-terminal-accent/20 text-terminal-accent'
          }`}
        >
          {isUser ? '>' : '*'}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0 pt-0.5">
          {/* Role label */}
          <div className="flex items-center gap-2 mb-1">
            <span
              className={`text-xs font-medium ${
                isUser ? 'text-terminal-muted' : 'text-terminal-accent'
              }`}
            >
              {isUser ? 'you' : 'oddopp'}
            </span>
            <span className="text-xs text-terminal-muted/50">
              {formatTime(message.timestamp)}
            </span>
          </div>

          {/* Message content */}
          {isUser ? (
            <p className="text-terminal-text whitespace-pre-wrap">{message.content}</p>
          ) : (
            <StreamingText
              content={message.content}
              isStreaming={message.isStreaming}
            />
          )}
        </div>
      </div>

      {/* Separator */}
      <div className="border-b border-terminal-border/50 mx-4" />
    </div>
  );
});

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
