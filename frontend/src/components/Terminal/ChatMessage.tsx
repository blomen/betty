import { memo } from 'react';
import type { Message } from '@/types';
import { StreamingText } from './StreamingText';

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo(function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';

  return (
    <div className="py-4 px-4 font-mono">
      {/* Message content */}
      <div className="flex gap-3">
        {/* Role indicator */}
        <span className={`flex-shrink-0 ${isUser ? 'text-terminal-secondary' : 'text-terminal-accent'}`}>
          {isUser ? '>' : '*'}
        </span>

        {/* Content */}
        <div className="flex-1 min-w-0">
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
    </div>
  );
});
