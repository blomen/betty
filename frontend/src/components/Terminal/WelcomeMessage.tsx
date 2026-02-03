export function WelcomeMessage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] px-4">
      {/* Logo */}
      <div className="mb-4 text-4xl text-terminal-accent font-mono">
        [*]
      </div>

      {/* Title */}
      <h1 className="text-2xl font-mono font-bold text-terminal-text mb-8">
        OddOpp
      </h1>

      {/* Hint */}
      <p className="text-sm text-terminal-muted">
        Type <span className="text-terminal-accent">/</span> for commands
      </p>
    </div>
  );
}
