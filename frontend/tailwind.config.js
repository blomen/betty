/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#1a1a1a',
        panel: '#202020',
        panel2: '#242424',
        border: '#2f2f2f',
        text: '#e7e7e7',
        muted: '#a7a7a7',
        muted2: '#7f7f7f',
        accent: '#a78bfa',
        accentBg: '#2a2540',
        accentBorder: '#3a2f66',
        success: '#22c55e',
        warning: '#f59e0b',
        error: '#EF5350',
        tableBorder: 'rgba(255,255,255,.28)',
        calloutBorder: '#48968c',
        // Tab colors
        tabExtract: '#60a5fa',
        tabArb: '#22c55e',
        tabValue: '#f59e0b',
        tabBonus: '#a78bfa',
        tabBets: '#22d3d8',
        tabBankroll: '#ec4899',
        tabProfiles: '#8b5cf6',
        tabPolymarket: '#6366f1',
        tabStats: '#94a3b8',
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
