import { useState, useRef, useEffect, useCallback, KeyboardEvent } from 'react';
import type { Command } from '@/utils/commands';
import type { BonusWorkflowState, BonusDropdownOption, DropdownWorkflowState, DropdownOption, BankrollWorkflowState, BankrollOption } from '@/types';

interface TerminalInputProps {
  onSend: (message: string) => void;
  onCommand?: (command: string, args: string) => void;
  commands?: Command[];
  onStop?: () => void;
  isLoading?: boolean;
  disabled?: boolean;
  placeholder?: string;
  onSlashTyped?: () => void;
  onInputChange?: (value: string) => void;
  autofillValue?: string;
  // Bonus workflow props
  bonusWorkflow?: BonusWorkflowState;
  bonusOptions?: BonusDropdownOption[];
  onBonusSelect?: (option: BonusDropdownOption) => void;
  onBonusCancel?: () => void;
  // Generic dropdown workflow props (extract, arb, value)
  dropdownWorkflow?: DropdownWorkflowState;
  dropdownOptions?: DropdownOption[];
  onDropdownSelect?: (option: DropdownOption) => void;
  onDropdownCancel?: () => void;
  // Bankroll workflow props
  bankrollWorkflow?: BankrollWorkflowState;
  bankrollOptions?: BankrollOption[];
  onBankrollSelect?: (option: BankrollOption) => void;
  onBankrollCancel?: () => void;
  // Selected indices (controlled by parent for inline panel sync)
  selectedDropdownIndex?: number;
  selectedBonusIndex?: number;
  selectedBankrollIndex?: number;
  onSelectedDropdownIndexChange?: (index: number) => void;
  onSelectedBonusIndexChange?: (index: number) => void;
  onSelectedBankrollIndexChange?: (index: number) => void;
}

export function TerminalInput({
  onSend,
  onCommand,
  commands: _commands = [],
  onStop,
  isLoading = false,
  disabled = false,
  placeholder = 'Type / for commands...',
  onSlashTyped,
  onInputChange,
  autofillValue,
  bonusWorkflow,
  bonusOptions = [],
  onBonusSelect,
  onBonusCancel,
  dropdownWorkflow,
  dropdownOptions = [],
  onDropdownSelect,
  onDropdownCancel,
  bankrollWorkflow,
  bankrollOptions = [],
  onBankrollSelect,
  onBankrollCancel,
  selectedDropdownIndex: controlledDropdownIndex,
  selectedBonusIndex: controlledBonusIndex,
  selectedBankrollIndex: controlledBankrollIndex,
  onSelectedDropdownIndexChange,
  onSelectedBonusIndexChange,
  onSelectedBankrollIndexChange,
}: TerminalInputProps) {
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [internalBonusIndex, setInternalBonusIndex] = useState(0);
  const [internalDropdownIndex, setInternalDropdownIndex] = useState(0);
  const [internalBankrollIndex, setInternalBankrollIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Use controlled or internal state
  const selectedBonusIndex = controlledBonusIndex ?? internalBonusIndex;
  const selectedDropdownIndex = controlledDropdownIndex ?? internalDropdownIndex;
  const selectedBankrollIndex = controlledBankrollIndex ?? internalBankrollIndex;

  const setSelectedBonusIndex = (value: number | ((prev: number) => number)) => {
    const newValue = typeof value === 'function' ? value(selectedBonusIndex) : value;
    setInternalBonusIndex(newValue);
    onSelectedBonusIndexChange?.(newValue);
  };

  const setSelectedDropdownIndex = (value: number | ((prev: number) => number)) => {
    const newValue = typeof value === 'function' ? value(selectedDropdownIndex) : value;
    setInternalDropdownIndex(newValue);
    onSelectedDropdownIndexChange?.(newValue);
  };

  const setSelectedBankrollIndex = (value: number | ((prev: number) => number)) => {
    const newValue = typeof value === 'function' ? value(selectedBankrollIndex) : value;
    setInternalBankrollIndex(newValue);
    onSelectedBankrollIndexChange?.(newValue);
  };

  // Check if bonus workflow is active
  const isBonusActive = bonusWorkflow?.step !== 'idle' && bonusWorkflow?.step !== undefined;

  // Check if generic dropdown workflow is active
  const isDropdownActive = dropdownWorkflow?.type !== 'idle' && dropdownWorkflow?.type !== undefined;

  // Check if bankroll workflow is active (and has options to navigate)
  const isBankrollActive = bankrollWorkflow?.step !== 'idle' && bankrollWorkflow?.step !== undefined &&
    bankrollWorkflow?.step !== 'enter-amount' && bankrollWorkflow?.step !== 'confirm-reset';

  // Reset bonus selection when options change
  useEffect(() => {
    setSelectedBonusIndex(0);
  }, [bonusOptions]);

  // Reset dropdown selection when options change
  useEffect(() => {
    setSelectedDropdownIndex(0);
  }, [dropdownOptions]);

  // Reset bankroll selection when options change
  useEffect(() => {
    setSelectedBankrollIndex(0);
  }, [bankrollOptions]);

  // Handle autofill from command panel (value includes counter suffix like "/bankroll#1")
  useEffect(() => {
    if (autofillValue !== undefined) {
      const value = autofillValue.split('#')[0];
      setInput(value);
      onInputChange?.(value);
      textareaRef.current?.focus();
    }
  }, [autofillValue]);

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

  // Handle input changes
  const handleInputChange = useCallback((value: string) => {
    setInput(value);
    onInputChange?.(value);

    // Notify parent when / is typed
    if (value === '/') {
      onSlashTyped?.();
    }
  }, [onInputChange, onSlashTyped]);

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
    onInputChange?.('');
  }, [input, isLoading, disabled, onSend, onCommand, onInputChange]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Generic dropdown navigation (extract, arb, value - takes highest priority)
      if (isDropdownActive && dropdownOptions.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedDropdownIndex((prev) =>
            prev < dropdownOptions.length - 1 ? prev + 1 : 0
          );
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedDropdownIndex((prev) =>
            prev > 0 ? prev - 1 : dropdownOptions.length - 1
          );
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          onDropdownSelect?.(dropdownOptions[selectedDropdownIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onDropdownCancel?.();
          return;
        }
        return;
      }

      // Bonus dropdown navigation (takes priority)
      if (isBonusActive && bonusOptions.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedBonusIndex((prev) =>
            prev < bonusOptions.length - 1 ? prev + 1 : 0
          );
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedBonusIndex((prev) =>
            prev > 0 ? prev - 1 : bonusOptions.length - 1
          );
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          onBonusSelect?.(bonusOptions[selectedBonusIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onBonusCancel?.();
          return;
        }
        return;
      }

      // Bankroll dropdown navigation
      if (isBankrollActive && bankrollOptions.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedBankrollIndex((prev) =>
            prev < bankrollOptions.length - 1 ? prev + 1 : 0
          );
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedBankrollIndex((prev) =>
            prev > 0 ? prev - 1 : bankrollOptions.length - 1
          );
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          onBankrollSelect?.(bankrollOptions[selectedBankrollIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onBankrollCancel?.();
          return;
        }
        return;
      }

      // Enter to send (Shift+Enter for newline)
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
        return;
      }

      // Escape to stop generation
      if (e.key === 'Escape') {
        if (isLoading && onStop) {
          e.preventDefault();
          onStop();
        }
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
    [input, history, historyIndex, isLoading, handleSend, onStop, isBonusActive, bonusOptions, selectedBonusIndex, onBonusSelect, onBonusCancel, isDropdownActive, dropdownOptions, selectedDropdownIndex, onDropdownSelect, onDropdownCancel, isBankrollActive, bankrollOptions, selectedBankrollIndex, onBankrollSelect, onBankrollCancel]
  );

  return (
    <div className="flex items-start gap-2 p-4 border-t border-terminal-border">
      <span className="text-terminal-accent py-1 select-none">&gt;</span>
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => handleInputChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        className="flex-1 bg-transparent text-terminal-text placeholder-terminal-muted/50 caret-terminal-cursor
                   resize-none outline-none py-1 min-h-[24px] max-h-[200px]"
      />
      {isLoading && (
        <span className="text-terminal-muted text-xs py-1 animate-pulse">...</span>
      )}
    </div>
  );
}
