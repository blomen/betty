import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSharpRefresh } from './useSharpRefresh'

const fakeOkResponse = {
  provider_id: 'pinnacle',
  matchup_id: 1234567,
  participants: ['Linette', 'Swiatek'],
  markets: [
    { key: 's;0;m', period: 0, prices: [
      { designation: 'home', american: 1450, decimal: 14.51, points: null },
      { designation: 'away', american: -2500, decimal: 1.04, points: null },
    ]},
  ],
}

describe('useSharpRefresh', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => fakeOkResponse,
    })) as any
  })
  afterEach(() => vi.restoreAllMocks())

  it('starts in idle', () => {
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline',
      baselineProviderId: 'pinnacle',
      matchupId: '1234567',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    expect(result.current.state).toBe('idle')
    expect(result.current.freshFair).toBeNull()
  })

  it('lands in unsupported when baseline missing', async () => {
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline',
      baselineProviderId: null,
      matchupId: null,
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => { await result.current.refresh() })
    expect(result.current.state).toBe('unsupported')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('lands in unsupported for polymarket', async () => {
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline',
      baselineProviderId: 'polymarket',
      matchupId: 'x',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => { await result.current.refresh() })
    expect(result.current.state).toBe('unsupported')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('refreshes pinnacle and exposes freshFair (devigged)', async () => {
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline',
      baselineProviderId: 'pinnacle',
      matchupId: '1234567',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => { await result.current.refresh() })
    await waitFor(() => expect(result.current.state).toBe('fresh'))
    expect(result.current.freshFair).not.toBeNull()
    // Multiplicative devig on [14.51, 1.04]:
    //   margin = 1/14.51 + 1/1.04 - 1 ≈ 0.0303
    //   home fair = 14.51 * 1.0303 ≈ 14.95
    expect(result.current.freshFair!.home).toBeGreaterThan(14.5)
    expect(result.current.freshFair!.home).toBeLessThan(15.5)
  })

  it('dedupes concurrent calls by eventKey', async () => {
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline:dedupe',
      baselineProviderId: 'pinnacle',
      matchupId: '1234567',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => {
      await Promise.all([
        result.current.refresh(),
        result.current.refresh(),
        result.current.refresh(),
      ])
    })
    expect((globalThis.fetch as any).mock.calls.length).toBe(1)
  })

  it('surfaces stale state on fetch failure', async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error('network') }) as any
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline:netfail',
      baselineProviderId: 'pinnacle',
      matchupId: '1234567',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => { await result.current.refresh() })
    expect(result.current.state).toBe('stale')
    expect(result.current.freshFair).toBeNull()
  })

  it('surfaces stale on endpoint-returned error payload', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ provider_id: 'pinnacle', error: 'matchup_not_found' }),
    })) as any
    const { result } = renderHook(() => useSharpRefresh({
      eventKey: 'evt:moneyline:errpayload',
      baselineProviderId: 'pinnacle',
      matchupId: '1234567',
      market: 'moneyline',
      point: null,
      outcome: 'home',
      eventId: 'evt-1',
    }))
    await act(async () => { await result.current.refresh() })
    expect(result.current.state).toBe('stale')
  })
})
