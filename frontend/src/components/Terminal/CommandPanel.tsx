import { useState, useEffect, useRef } from 'react';
import type { Command } from '@/utils/commands';

// Color map for command categories
const commandColors: Record<string, string> = {
  extract: 'text-terminal-accent',      // Blue - Data
  arb: 'text-terminal-orange',          // Orange - Opportunity
  value: 'text-terminal-orange',        // Orange - Opportunity
  bonus: 'text-terminal-orange',        // Orange - Opportunity
  bets: 'text-terminal-orange',         // Orange - Betting
  bankroll: 'text-terminal-green',      // Green - Money
  stats: 'text-terminal-accent',        // Blue - Analytics
  clear: 'text-terminal-muted',         // Muted - System
};

const getCommandColor = (commandName: string): string => {
  return commandColors[commandName] || 'text-terminal-secondary';
};

interface CommandPanelProps {
  commands: Command[];
  isOpen: boolean;
  onClose: () => void;
  onSelect: (command: string) => void;
  onAutofill: (command: string) => void;
  filter: string;
}

export function CommandPanel({
  commands,
  isOpen,
  onClose,
  onSelect,
  onAutofill,
  filter,
}: CommandPanelProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const optionRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  // Filter commands based on input
  const filteredCommands = commands.filter((cmd) => {
    const query = filter.slice(1).toLowerCase();
    return cmd.name.toLowerCase().startsWith(query);
  });

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [filter]);

  // Auto-scroll to keep selected option visible
  useEffect(() => {
    const selectedButton = optionRefs.current.get(selectedIndex);
    if (selectedButton) {
      selectedButton.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [selectedIndex]);

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev < filteredCommands.length - 1 ? prev + 1 : 0
        );
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev > 0 ? prev - 1 : filteredCommands.length - 1
        );
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (filteredCommands[selectedIndex]) {
          onSelect(filteredCommands[selectedIndex].name);
        }
      } else if (e.key === 'Tab') {
        e.preventDefault();
        if (filteredCommands[selectedIndex]) {
          onAutofill(filteredCommands[selectedIndex].name);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, filteredCommands, selectedIndex, onSelect, onAutofill, onClose]);

  if (!isOpen) return null;

  return (
    <div className="border border-terminal-border rounded bg-terminal-surface font-mono text-xs my-2 p-2 max-h-64 overflow-y-auto">
      <div className="flex flex-col gap-1">
        {filteredCommands.map((cmd, idx) => (
          <button
            key={cmd.name}
            ref={(el) => { if (el) optionRefs.current.set(idx, el); }}
            onClick={() => onSelect(cmd.name)}
            className={`px-2 py-1.5 rounded text-left transition-colors ${
              idx === selectedIndex
                ? `bg-terminal-accent/15 ${getCommandColor(cmd.name)}`
                : `hover:bg-terminal-bg/50 ${getCommandColor(cmd.name)} opacity-70 hover:opacity-100`
            }`}
          >
            /{cmd.name}
          </button>
        ))}
      </div>
    </div>
  );
}
