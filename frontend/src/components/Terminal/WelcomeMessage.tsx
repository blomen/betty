import type { BettingContext, BankrollExposure, Profile } from '@/types';
import { SuggestionCards } from './SuggestionCards';

interface WelcomeMessageProps {
  context: BettingContext;
  exposure: BankrollExposure;
  activeProfile: Profile | null;
}

export function WelcomeMessage({
  activeProfile,
}: WelcomeMessageProps) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] px-4">
      {/* Logo */}
      <div className="mb-8 text-6xl">
        <div className="text-terminal-accent font-mono">
          [*]
        </div>
      </div>

      {/* Title */}
      <h1 className="text-3xl font-mono font-bold text-terminal-text mb-2">
        OddOpp
      </h1>
      <p className="text-terminal-muted text-sm mb-8">
        Sports betting analytics terminal
      </p>

      {/* Optional: Active Profile Badge */}
      {activeProfile && (
        <div className="text-xs text-terminal-muted mb-12">
          [@] {activeProfile.name}
        </div>
      )}

      {/* Suggestion Cards */}
      <SuggestionCards />

      {/* Optional: Hint */}
      <div className="mt-12 text-xs text-terminal-muted">
        Type <span className="text-terminal-accent">/help</span> to see all commands
      </div>
    </div>
  );
}
