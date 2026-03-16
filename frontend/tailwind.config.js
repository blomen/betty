/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0a0e0a',
        panel: '#131a13',
        panel2: '#1a231a',
        border: '#2a3a2a',
        text: '#d4e0d4',
        muted: '#7a9a7a',
        muted2: '#5a7a5a',
        accent: '#4FC3F7',
        accentBg: '#0a1a0a',
        accentBorder: '#2a3a2a',
        success: '#4CAF50',
        yellow: '#FACC15',
        warning: '#FF9800',
        error: '#EF5350',
        tableBorder: '#2a3a2a',
        calloutBorder: '#4CAF50',
        // Tab colors — must match Sidebar.tsx dot colors
        tabExtract: '#60a5fa',
        tabArb: '#22c55e',
        tabValue: '#FF9800',
        tabBonus: '#A78BFA',
        tabBets: '#1E88E5',
        tabBankroll: '#EC4899',
        tabProfiles: '#7C3AED',
        tabPolymarket: '#A855F7',
        tabReverse: '#EF5350',
        tabStats: '#9AA0A6',
        tabTradingBankroll: '#EC4899',
        tabTradingToday: '#FACC15',
        tabTradingBuilder: '#22C55E',
        tabTradingTrades: '#4FC3F7',
        tabTradingJournal: '#A78BFA',
        tabTradingScanner: '#06B6D4',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Cascadia Code', 'SF Mono', 'Fira Code', 'Consolas', 'ui-monospace', 'monospace'],
      },
      animation: {
        blink: 'blink 1s step-end infinite',
        fadeIn: 'fadeIn 0.2s ease-out',
      },
      keyframes: {
        blink: {
          '0%, 100%': { opacity: 1 },
          '50%': { opacity: 0 },
        },
        fadeIn: {
          '0%': { opacity: 0, transform: 'translateY(4px)' },
          '100%': { opacity: 1, transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
