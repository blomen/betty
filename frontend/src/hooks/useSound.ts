import { useRef, useCallback } from 'react';

/** Frequencies and durations for level alert tones. */
const TONES = {
  approaching: { freq: 440, duration: 0.1, gain: 0.15 },
  at_level: { freq: 880, duration: 0.2, gain: 0.3 },
  at_target: { freq: 660, duration: 0.15, gain: 0.25 },
} as const;

type ToneType = keyof typeof TONES;

export function useSound() {
  const ctxRef = useRef<AudioContext | null>(null);
  const unlockedRef = useRef(false);

  /** Must be called from a user interaction (click) to unlock audio. */
  const unlock = useCallback(() => {
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext();
    }
    if (ctxRef.current.state === 'suspended') {
      ctxRef.current.resume();
    }
    unlockedRef.current = true;
  }, []);

  const play = useCallback((tone: ToneType) => {
    const ctx = ctxRef.current;
    if (!ctx || !unlockedRef.current) return;

    const { freq, duration, gain: gainVal } = TONES[tone];
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.value = gainVal;
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

    osc.connect(gain).connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duration);
  }, []);

  return { unlock, play };
}
