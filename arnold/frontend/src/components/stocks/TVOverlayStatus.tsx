import { useEffect, useState } from 'react'
import type { TVOverlayStatus as Status } from '@/types/stocks'

const TV_NQ_URL = 'https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1!'

export function TVOverlayStatus() {
  const [status, setStatus] = useState<Status | null>(null)
  const [copied, setCopied] = useState(false)
  const [opening, setOpening] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const r = await fetch('/stocks/api/tv-overlay/status')
        if (!r.ok) return
        const data = await r.json()
        if (!cancelled) setStatus(data)
      } catch { /* ignore */ }
    }
    poll()
    const iv = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [])

  const copyUrl = async () => {
    if (!status) return
    try {
      await navigator.clipboard.writeText(window.location.origin + status.userscript_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* ignore */ }
  }

  const openInMirror = async () => {
    setOpening(true)
    setOpenError(null)
    // 30s upper bound — Chromium cold-start with extension load takes ~10s.
    // Without this the button could hang forever if the server stalls.
    const ac = new AbortController()
    const timer = setTimeout(() => ac.abort(), 30_000)
    try {
      const r = await fetch('/mirror/open-tab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: TV_NQ_URL }),
        signal: ac.signal,
      })
      if (!r.ok) {
        let detail = ''
        try {
          const body = await r.json()
          detail = body?.detail ?? ''
        } catch {
          detail = await r.text().catch(() => '')
        }
        setOpenError(detail.slice(0, 200) || `HTTP ${r.status}`)
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setOpenError(msg.includes('aborted') ? 'timeout (30s) — check arnold.bat log' : msg)
    } finally {
      clearTimeout(timer)
      setOpening(false)
    }
  }

  if (!status) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">TV Overlay</div>
        <div className="text-zinc-400">Loading…</div>
      </div>
    )
  }

  const attached = status.attached_clients > 0
  const dotColor = attached ? 'bg-emerald-500' : 'bg-zinc-600'
  const ageS = status.last_paint_at ? Math.round((Date.now() / 1000) - status.last_paint_at) : null

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">TV Overlay</span>
        <div className="flex gap-1">
          <button
            onClick={openInMirror}
            disabled={opening}
            className="px-2 py-0.5 text-[10px] uppercase tracking-wider bg-emerald-900/60 hover:bg-emerald-800/60 disabled:opacity-50 text-emerald-300 rounded"
          >
            {opening ? 'Opening…' : 'Open TV in mirror'}
          </button>
          <button
            onClick={copyUrl}
            className="px-2 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded"
          >
            {copied ? 'Copied' : 'Copy userscript URL'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 mb-1">
        <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
        <span className="text-zinc-300">
          {attached ? `${status.attached_clients} client${status.attached_clients > 1 ? 's' : ''} attached` : 'No overlay clients'}
        </span>
      </div>
      {!attached && (
        <div className="text-zinc-500 mt-1 leading-tight">
          Click "Open TV in mirror" — the mirror Chromium ships with the
          overlay extension preloaded, so the chart will start drawing
          zones automatically. (For Tampermonkey users: copy the URL.)
        </div>
      )}
      <div className="flex gap-3 mt-2 text-zinc-500">
        <span>{status.draw_count} drawn</span>
        {ageS !== null && <span>painted {ageS}s ago</span>}
      </div>
      {status.error && <div className="text-red-400 mt-1 truncate" title={status.error}>{status.error}</div>}
      {openError && <div className="text-red-400 mt-1 truncate" title={openError}>open failed: {openError}</div>}
    </div>
  )
}
