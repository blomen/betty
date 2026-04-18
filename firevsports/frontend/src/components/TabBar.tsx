// Stub TabBar for firevsports — provides TAB_COLORS and TabIcon used by pages

export const TAB_COLORS: Record<string, string> = {
  play: '#22c55e',
  value: '#FF9800',
  arb: '#10b981',
  reverse: '#EF5350',
  polymarket: '#A855F7',
  stats: '#1E88E5',
  bankroll: '#EC4899',
  specials: '#A78BFA',
  bets: '#1E88E5',
  profiles: '#A78BFA',
  settings: '#9AA0A6',
  success: '#10b981',
  pending: '#f59e0b',
};

export function TabIcon({ name, color, size = 16 }: { name: string; color: string; size?: number }) {
  const w = size;
  const h = size;
  const v = '0 0 24 24';

  switch (name) {
    case 'sports':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
          <path d="M2 12h20"/>
        </svg>
      );
    default:
      return (
        <svg width={w} height={h} viewBox={v} fill="none">
          <circle cx="12" cy="12" r="5" stroke={color} strokeWidth="1.5"/>
        </svg>
      );
  }
}
