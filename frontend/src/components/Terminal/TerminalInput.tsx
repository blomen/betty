import { useState, useRef, useEffect, useCallback, KeyboardEvent } from 'react';
import type { Command } from '@/utils/commands';

interface TerminalInputProps {
  onSend: (message: string) => void;
  onCommand?: (command: string, args: string) => void;
  commands?: Command[];
  onStop?: () => void;
  isLoading?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

export function TerminalInput({
  onSend,
  onCommand,
  commands = [],
  onStop,
  isLoading = false,
  disabled = false,
  placeholder = 'Ask about arbitrage, value bets, or betting strategies... (or type / for commands)',
}: TerminalInputProps) {
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Filter commands based on input
  const filteredCommands = useCallback(() => {
    if (!input.startsWith('/')) return [];
    const query = input.slice(1).toLowerCase().split(/\s+/)[0];
    return commands.filter((cmd) =>
      cmd.name.toLowerCase().startsWith(query)
    ).slice(0, 8); // Max 8 suggestions
  }, [input, commands]);

  const commandSuggestions = filteredCommands();

  // Show/hide command suggestions
  useEffect(() => {
    setShowCommandSuggestions(input.startsWith('/') && input.length > 0 && commandSuggestions.length > 0);
    setSelectedCommandIndex(0);
  }, [input, commandSuggestions.length]);

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

    // Check if it's a slash command
    if (trimmed.startsWith('/') && onCommand) {
      const parts = trimmed.slice(1).split(/\s+/);
      const command = parts[0].toLowerCase();
      const args = parts.slice(1).join(' ');
      onCommand(command, args);
    } else {
      onSend(trimmed);
    }

    setInput('');
    setShowCommandSuggestions(false);
  }, [input, isLoading, disabled, onSend, onCommand]);

  const handleCommandSelect = useCallback((cmd: Command) => {
    setInput(`/${cmd.name} `);
    setShowCommandSuggestions(false);
    textareaRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Command suggestions navigation
      if (showCommandSuggestions && commandSuggestions.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedCommandIndex((prev) =>
            prev < commandSuggestions.length - 1 ? prev + 1 : 0
          );
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedCommandIndex((prev) =>
            prev > 0 ? prev - 1 : commandSuggestions.length - 1
          );
          return;
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          handleCommandSelect(commandSuggestions[selectedCommandIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setShowCommandSuggestions(false);
          return;
        }
      }

      // Enter to send (Shift+Enter for newline)
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
        return;
      }

      // Escape to stop generation or close suggestions
      if (e.key === 'Escape') {
        if (isLoading && onStop) {
          e.preventDefault();
          onStop();
        } else if (showCommandSuggestions) {
          e.preventDefault();
          setShowCommandSuggestions(false);
        }
        return;
      }

      // Up/Down for history navigation (only when not showing command suggestions)
      if (!showCommandSuggestions) {
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
      }
    },
    [input, history, historyIndex, isLoading, handleSend, onStop, showCommandSuggestions, commandSuggestions, selectedCommandIndex, handleCommandSelect]
  );

  return (
    <div className="border-t border-terminal-border bg-terminal-surface relative">
      {/* Command Suggestions Dropdown */}
      {showCommandSuggestions && commandSuggestions.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 bg-terminal-surface border border-terminal-accent/30 border-b-0 max-h-64 overflow-y-auto">
          <div className="p-2 border-b border-terminal-border/50 text-xs text-terminal-muted">
            Available commands (Tab to select, ↑↓ to navigate)
          </div>
          {commandSuggestions.map((cmd, index) => (
            <button
              key={cmd.name}
              onClick={() => handleCommandSelect(cmd)}
              className={`w-full text-left px-3 py-2 flex items-start gap-3 border-b border-terminal-border/30 last:border-b-0
                         transition-colors ${
                           index === selectedCommandIndex
                             ? 'bg-terminal-accent/20 text-terminal-accent'
                             : 'text-terminal-text hover:bg-terminal-accent/10'
                         }`}
            >
              <span className="font-mono text-sm font-medium whitespace-nowrap">
                /{cmd.name}
              </span>
              <span className="text-xs text-terminal-muted flex-1">
                {cmd.description}
              </span>
              {cmd.category && (
                <span className="text-[10px] text-terminal-muted/60 px-1.5 py-0.5 bg-terminal-bg rounded">
                  {cmd.category}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

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
        <span>/ for commands</span>
        <span>Shift+Enter for newline</span>
        {isLoading && <span>Esc to stop</span>}
        {!showCommandSuggestions && history.length > 0 && <span>Up/Down for history</span>}
        {showCommandSuggestions && <span>Tab to select</span>}
      </div>
    </div>
  );
}
