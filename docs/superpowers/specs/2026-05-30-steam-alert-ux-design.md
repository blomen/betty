# Steam Alert UX (detect-fast + alert, v1) — Design Spec

**Date:** 2026-05-30
**Status:** Approved design — pending implementation plan
**Sub-project 3 of 5** in the "profit-lever gap" program. This is **v1 (alert UX)** of the steam-execution gap; the dedicated low-latency steam loop and any auto-/semi-auto placement are explicitly deferred to later increments.

## Background

Research (verified) identifies steam-chasing as a durable syndicate edge: when sharp
books move, slower soft books hang the stale line for ~30-60s — bet it before they
adjust. Betty already **detects** steam (`backend/src/analysis/steam_detector.py`:
≥3 distinct books moving the same direction within a rolling window) and **surfaces**
it as a small inline `steam ▲ N` pill on value-bet rows
(`frontend/src/pages/PlayPage.tsx` ~line 2703-2734). But nothing **alerts** the user —
a steam-flagged value bet looks like any other row, so the user can miss the window.

## Goal

When a **new, actionable** steam-flagged value bet appears, alert the user in-app so
they place it within the window: play a one-time sound and pin the bet to the top of
the value list with a strong highlight. Frontend-only; runs on the existing poll
cadence.

## Decisions (from brainstorming)

- **Automation level:** detect-fast + alert. **No** auto- or semi-auto placement
  (user still places manually).
- **Latency:** alert UX on the **existing** post-Pinnacle scanner/poll cadence. A
  dedicated high-frequency steam loop is deferred (fast-follow once we see how often
  alerts land in time).
- **Channels:** in-app **sound + pin-to-top** with strong highlight. **No** desktop
  notification.
- **Noise control:** alert only on **actionable** bets — steam_signal present AND the
  bet's provider is funded/selected AND edge ≥ floor. Fire **once per newly-seen** bet
  (per session).
- **Approach A:** pure client-side in PlayPage on the existing `/play/batch` poll. No
  backend change.

## Prerequisite (server-side, user-controlled)

`STEAM_DETECTOR_ENABLED=1` must be set on the server so `OddsBatchProcessor` writes
`odds_movements` and the scanner populates `steam_signal` in the play/batch payload.
This is env/config the user controls (no code in this sub-project). If it's already
enabled, nothing to do. If off, the alert simply never fires (the UX degrades safely
to today's behavior).

## Architecture

Two frontend units; no backend code.

### Component 1 — `useSteamAlert` hook + pure `selectNewSteamAlerts` helper

Location: `frontend/src/hooks/useSteamAlert.ts` (new).

- **Pure helper** `selectNewSteamAlerts(bets, seen, fundedProviders, edgeFloor) -> string[]`:
  returns the keys of bets that (a) carry a `steam_signal` (with a `direction`),
  (b) have `provider ∈ fundedProviders`, (c) have `edge_pct ≥ edgeFloor`, and
  (d) whose key is not already in `seen`. Key = `event_id|market|outcome|provider|point`.
  Pure, no side effects — unit-testable.
- **Hook** `useSteamAlert(bets, fundedProviders, edgeFloor)`: holds a `seenKeys` ref
  (session-scoped Set). On each render where `bets` changes, computes
  `selectNewSteamAlerts(...)`; if non-empty, plays a **synthesized WebAudio beep**
  (short oscillator burst — no audio asset shipped) once and adds the new keys to
  `seen`. Returns the set of currently-actionable steam keys so the page can style
  them (pin + highlight). On empty/absent steam data the hook no-ops.

### Component 2 — PlayPage wiring

- Compute `fundedProviders` from the existing provider-selection/funded state and
  pass the current value-bet batch + an `edgeFloor` constant to `useSteamAlert`.
- **Pin-to-top:** in the value-list sort, elevate keys in the returned actionable-steam
  set above the normal edge sort.
- **Highlight:** render alerting rows with a strong visual treatment (beyond the
  existing small pill) — e.g. an accent border/background — so the pinned bet is
  unmistakable.

## Data flow

```
scanner (post-Pinnacle) → opportunities w/ annotations.steam_signal
    ↓  (existing /api/opportunities/play/batch poll)
PlayPage value batch
    ↓
useSteamAlert(batch, fundedProviders, edgeFloor)
    ├─ selectNewSteamAlerts → new actionable keys → WebAudio beep (once each)
    └─ returns actionable-steam key set
    ↓
PlayPage: pin those rows to top + strong highlight
```

## Error handling & edge cases

| Case | Behavior |
|---|---|
| `steam_signal` absent (detector off / no move) | Hook no-ops; no sound; normal list |
| Provider not funded/selected | Excluded from alerts (still shown normally) |
| `edge_pct < floor` | Excluded from alerts |
| Bet already seen this session | No re-alert (key in `seen`) |
| Bet disappears then reappears | Stays in `seen` for the session → no re-spam |
| Browser blocks audio before a user gesture | Sound is best-effort; pin + highlight still work (no error) |
| Many new steam bets in one poll | One beep for the batch (not N) — fire once if any new keys |

## Testing

- **Unit (`selectNewSteamAlerts`):** returns only steam+funded+edge≥floor+unseen keys;
  excludes non-steam, unfunded, below-floor, and already-seen; dedupes within a batch.
- The hook's audio/DOM glue and PlayPage sort/highlight are thin; verified via the
  local client / Claude Preview (no betting-path involvement).

## Deployment note

**Frontend-only** — ships via `betty.bat` (Vite + local FastAPI). **No backend rebuild,
no migration, no betting-path change.** The only server-side dependency is the
`STEAM_DETECTOR_ENABLED` env flag (user-controlled).

## The 5-gap program (context)

Sub-project 3 of 5: 1. multi-book sharp blend (shipped) → 2. liquidity-aware sizing
(merged) → **3. steam alert UX (this spec; v1 of the steam gap)** → 4. shading-aware
edge adjustment → 5. bonus-play behavior shaping. Deferred within #3: low-latency
steam loop, semi-/full-auto placement.
