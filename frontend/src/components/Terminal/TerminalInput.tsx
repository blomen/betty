import { useState, useRef, useEffect, useCallback, KeyboardEvent } from 'react';

interface TerminalInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  isLoading?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

export function TerminalInput({
  onSend,
  onStop,
  isLoading = false,
  disabled = false,
  placeholder = 'Ask about arbitrage, value bets, or betting strategies...',
}: TerminalInputProps) {
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
    }
  }, [input]);

  // Focus on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isLoading || disabled) return;

    setHistory((prev) => [...prev, trimmed]);
    setHistoryIndex(-1);
    onSend(trimmed);
    setInput('');
  }, [input, isLoading, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter to send (Shift+Enter for newline)
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
        return;
      }

      // Escape to stop generation
      if (e.key === 'Escape' && isLoading && onStop) {
        e.preventDefault();
        onStop();
        return;
      }

      // Up/Down for history navigation
      if (e.key === 'ArrowUp' && input === '' && history.length > 0) {
        e.preventDefault();
        const newIndex = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
        setHistoryIndex(newIndex);
        setInput(history[newIndex]);
        return;
      }

      if (e.key === 'ArrowDown' && historyIndex !== -1) {
        e.preventDefault();
        const newIndex = historyIndex + 1;
        if (newIndex >= history.length) {
          setHistoryIndex(-1);
          setInput('');
        } else {
          setHistoryIndex(newIndex);
          setInput(history[newIndex]);
        }
        return;
      }
    },
    [input, history, historyIndex, isLoading, handleSend, onStop]
  );

  return (
    <div className="border-t border-terminal-border bg-terminal-surface">
      <div className="flex items-end gap-2 p-3">
        {/* ASCII Prompt indicator */}
        <div className="flex-shrink-0 pb-2">
          <span className={`font-bold ${isLoading ? 'text-terminal-yellow' : 'text-terminal-accent'}`}>
            {isLoading ? '...' : '>'}
          </span>
        </div>

        {/* Input area */}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent text-terminal-text placeholder-terminal-muted/50
                     resize-none outline-none py-1.5 min-h-[36px] max-h-[200px]"
        />

        {/* Action button */}
        {isLoading ? (
          <button
            onClick={onStop}
            className="flex-shrink-0 px-3 py-1.5 rounded bg-terminal-red/20 text-terminal-red
                       hover:bg-terminal-red/30 transition-colors text-sm font-medium"
            title="Stop generation (Esc)"
          >
            [stop]
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!input.trim() || disabled}
            className="flex-shrink-0 px-3 py-1.5 rounded bg-terminal-accent/20 text-terminal-accent
                       hover:bg-terminal-accent/30 transition-colors text-sm font-medium
                       disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-terminal-accent/20"
            title="Send message (Enter)"
          >
            [send]
          </button>
        )}
      </div>

      {/* Hints */}
      <div className="px-3 pb-2 flex items-center gap-4 text-[10px] text-terminal-muted/50">
        <span>Enter to send</span>
        <span>Shift+Enter for newline</span>
        {isLoading && <span>Esc to stop</span>}
        {history.length > 0 && <span>Up/Down for history</span>}
      </div>
    </div>
  );
}
