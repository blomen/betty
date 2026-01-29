interface Suggestion {
  title: string;
  description: string;
  command: string;
}

const suggestions: Suggestion[] = [
  {
    title: "Run extraction",
    description: "Extract latest odds from providers",
    command: "/extract"
  },
  {
    title: "View opportunities",
    description: "See arbitrage and value bets",
    command: "/opportunities"
  },
  {
    title: "Check bankroll",
    description: "View balance and exposure",
    command: "/bankroll"
  },
];

export function SuggestionCards() {
  const handleClick = (command: string) => {
    // Auto-fill command in input
    const input = document.querySelector('textarea') as HTMLTextAreaElement;
    if (input) {
      input.value = command;
      input.focus();
      // Trigger input event to update React state
      const event = new Event('input', { bubbles: true });
      input.dispatchEvent(event);
    }
  };

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-2xl">
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.command}
          onClick={() => handleClick(suggestion.command)}
          className="group p-4 rounded-lg border border-terminal-border bg-terminal-surface/50 hover:bg-terminal-surface hover:border-terminal-accent transition-all text-left"
        >
          <div className="text-sm font-mono font-semibold text-terminal-text mb-1 group-hover:text-terminal-accent transition-colors">
            {suggestion.title}
          </div>
          <div className="text-xs text-terminal-muted">
            {suggestion.description}
          </div>
          <div className="text-xs text-terminal-accent mt-2 font-mono">
            {suggestion.command}
          </div>
        </button>
      ))}
    </div>
  );
}
