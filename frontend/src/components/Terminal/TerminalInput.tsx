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
  placeholder = 'Ask a question or type / for commands...',
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

    console.log('[DEBUG] handleSend called with:', trimmed);
    console.log('[DEBUG] isLoading:', isLoading, 'disabled:', disabled);
    console.log('[DEBUG] onSend:', typeof onSend, 'onCommand:', typeof onCommand);

    setHistory((prev) => [...prev, trimmed]);
    setHistoryIndex(-1);

    // Check if it's a slash command
    if (trimmed.startsWith('/') && onCommand) {
      console.log('[DEBUG] Executing slash command');
      const parts = trimmed.slice(1).split(/\s+/);
      const command = parts[0].toLowerCase();
      const args = parts.slice(1).join(' ');
      onCommand(command, args);
    } else {
      console.log('[DEBUG] Sending message to chat');
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
        <div className="absolute bottom-full left-0 right-0 bg-terminal-bg border-l border-r border-t border-terminal-accent/30 max-h-64 overflow-y-auto">
          <div className="px-3 py-1.5 border-b border-terminal-border/50 text-[10px] text-terminal-muted uppercase tracking-wider">
            [commands] Tab to select | Up/Down to navigate
          </div>
          {commandSuggestions.map((cmd, index) => (
            <button
              key={cmd.name}
              onClick={() => handleCommandSelect(cmd)}
              className={`w-full text-left px-3 py-2 flex items-start gap-3 border-b border-terminal-border/30 last:border-b-0
                         transition-colors font-mono text-sm ${
                           index === selectedCommandIndex
                             ? 'bg-terminal-accent/10 text-terminal-accent'
                             : 'text-terminal-text hover:bg-terminal-border/30'
                         }`}
            >
              <span className="font-medium whitespace-nowrap text-terminal-accent">
                /{cmd.name}
              </span>
              <span className="text-xs text-terminal-muted flex-1 mt-0.5">
                {cmd.description}
              </span>
              {cmd.category && (
                <span className="text-[10px] text-terminal-muted/60 uppercase tracking-wide">
                  [{cmd.category}]
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Input Box */}
      <div className="flex items-start gap-2 p-3 font-mono">
        {/* Input area */}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent text-terminal-text placeholder-terminal-muted/40
                     resize-none outline-none py-1.5 min-h-[28px] max-h-[200px] font-mono"
        />

        {/* Action button - Enhanced style */}
        {isLoading ? (
          <button
            onClick={onStop}
            className="flex-shrink-0 px-3 py-1.5 text-terminal-red hover:bg-terminal-red/10
                       transition-colors text-xs font-mono font-bold border border-terminal-red/30 rounded"
            title="Stop generation (Esc)"
          >
            [STOP]
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!input.trim() || disabled}
            className="flex-shrink-0 px-3 py-1.5 bg-terminal-accent/10 hover:bg-terminal-accent/20 text-terminal-accent
                       transition-all text-xs font-mono font-bold border border-terminal-accent rounded
                       disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-terminal-accent/10"
            title="Send message (Enter)"
          >
            [ASK]
          </button>
        )}
      </div>

      {/* Terminal hints bar */}
      <div className="px-3 pb-2 border-t border-terminal-border/30 pt-1.5 bg-terminal-bg/50">
        <div className="flex items-center gap-4 text-[10px] text-terminal-muted/50 font-mono">
          <span>[Enter] send</span>
          <span>[/] commands</span>
          <span>[Shift+Enter] newline</span>
          {isLoading && <span className="text-terminal-yellow">[Esc] stop</span>}
          {!showCommandSuggestions && history.length > 0 && <span>[Up/Down] history</span>}
          {showCommandSuggestions && <span className="text-terminal-accent">[Tab] select</span>}
        </div>
      </div>
    </div>
  );
}
