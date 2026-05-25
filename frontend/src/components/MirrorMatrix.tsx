/**
 * MirrorMatrix — live capability matrix for all providers.
 *
 * Phase 4 of the platform rebuild (2026-05-08). Replaces the static §9
 * markdown table in `docs/mirror-workflow.md` that "lied" — silently showed
 * ✅ for capabilities that had broken without anyone noticing.
 *
 * Reads `/api/mirror/health` (rebuilt daily by the smoke-test cron in
 * `backend/src/jobs/mirror_smoke.py`). Each row shows:
 *   - overall badge (green/amber/red)
 *   - home_url HTTP status
 *   - last balance intercept (proves the local mirror is actually seeing
 *     data from this provider — not just that the server cron pinged it)
 *   - last placement (proves a real bet went through)
 *   - last skip + reason (when the runner aborted)
 *
 * Pre-deploy: endpoint returns 404 → empty table + "endpoint not deployed"
 * hint. Post-deploy + first cron pass: rows populate within 24h or one
 * `POST /api/mirror/health/recompute` call.
 */
import { useEffect, useState } from 'react'

interface HealthRow {
  provider_id: string
  home_url_status: 'green' | 'amber' | 'red' | null
  home_url_http_code: number | null
  last_login_detected_at: string | null
  last_balance_intercept_at: string | null
  last_placement_at: string | null
  last_settled_at: string | null
  last_provider_skipped_at: string | null
  last_provider_skipped_reason: string | null
  overall: 'green' | 'amber' | 'red' | null
  notes: string | null
  checked_at: string | null
}

interface HealthResponse {
  providers: HealthRow[]
}

const POLL_INTERVAL_MS = 30_000

function relTime(iso: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const diff = Date.now() - t
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

function badgeColor(status: string | null): string {
  if (status === 'green') return 'bg-green-900/40 text-green-300 border border-green-700/50'
  if (status === 'amber') return 'bg-amber-900/40 text-amber-300 border border-amber-700/50'
  if (status === 'red') return 'bg-red-900/50 text-red-300 border border-red-700/50'
  return 'bg-zinc-800 text-zinc-500 border border-zinc-700'
}

export function MirrorMatrix() {
  const [rows, setRows] = useState<HealthRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [recomputing, setRecomputing] = useState(false)

  const fetchOnce = async () => {
    try {
      const r = await fetch('/api/mirror/health')
      if (!r.ok) {
        setError(`/api/mirror/health → ${r.status}`)
        setLoading(false)
        return
      }
      const data: HealthResponse = await r.json()
      setRows((data.providers ?? []).sort((a, b) => a.provider_id.localeCompare(b.provider_id)))
      setError(null)
      setLoading(false)
    } catch (e: any) {
      setError(String(e?.message ?? e))
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchOnce()
    const id = setInterval(fetchOnce, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  const recompute = async () => {
    setRecomputing(true)
    try {
      await fetch('/api/mirror/health/recompute', { method: 'POST' })
      await fetchOnce()
    } finally {
      setRecomputing(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-zinc-950 text-zinc-200">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-zinc-900/50">
        <h2 className="text-sm font-bold uppercase tracking-wider text-purple-300">
          Mirror Capability Matrix
        </h2>
        <span className="text-[10px] text-zinc-500">
          {rows.length} providers · auto-generated from event log + daily HTTP probe
        </span>
        <button
          onClick={recompute}
          disabled={recomputing}
          className="ml-auto px-2 py-0.5 text-[10px] rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700"
        >
          {recomputing ? 'recomputing…' : 'recompute now'}
        </button>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs text-red-400 bg-red-950/20 border-b border-red-900/40">
          {error} — server not deployed yet, or recompute hasn't run. The frontend gracefully falls back to empty.
        </div>
      )}

      {loading ? (
        <div className="px-4 py-3 text-xs text-zinc-500">loading…</div>
      ) : rows.length === 0 ? (
        <div className="px-4 py-3 text-xs text-zinc-500">
          No health rows yet. Run <code className="text-amber-400">POST /api/mirror/health/recompute</code> to populate
          from `mirror_event_log`, or wait 24h for the first scheduled cron pass.
        </div>
      ) : (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-zinc-900 border-b border-zinc-800">
              <tr className="text-[10px] uppercase tracking-wider text-zinc-500">
                <th className="px-3 py-1.5 text-left">Provider</th>
                <th className="px-3 py-1.5 text-left">Overall</th>
                <th className="px-3 py-1.5 text-left">Home URL</th>
                <th className="px-3 py-1.5 text-left">Last balance</th>
                <th className="px-3 py-1.5 text-left">Last placement</th>
                <th className="px-3 py-1.5 text-left">Last settled</th>
                <th className="px-3 py-1.5 text-left">Last skip</th>
                <th className="px-3 py-1.5 text-left">Checked</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.provider_id} className="border-b border-zinc-800/50 hover:bg-zinc-900/30">
                  <td className="px-3 py-1.5 font-mono uppercase text-zinc-200">{row.provider_id}</td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 text-[9px] rounded uppercase ${badgeColor(row.overall)}`}>
                      {row.overall ?? '?'}
                    </span>
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 text-[9px] rounded ${badgeColor(row.home_url_status)}`}>
                      {row.home_url_status ?? '?'}
                    </span>
                    {row.home_url_http_code != null && (
                      <span className="ml-1.5 text-[10px] font-mono text-zinc-500">
                        HTTP {row.home_url_http_code}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-zinc-400">{relTime(row.last_balance_intercept_at)}</td>
                  <td className="px-3 py-1.5 text-zinc-400">{relTime(row.last_placement_at)}</td>
                  <td className="px-3 py-1.5 text-zinc-400">{relTime(row.last_settled_at)}</td>
                  <td className="px-3 py-1.5">
                    <span className="text-zinc-400">{relTime(row.last_provider_skipped_at)}</span>
                    {row.last_provider_skipped_reason && (
                      <span className="ml-1.5 text-[10px] text-amber-400" title={row.last_provider_skipped_reason}>
                        {row.last_provider_skipped_reason.slice(0, 40)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-zinc-500 text-[10px]">{relTime(row.checked_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
