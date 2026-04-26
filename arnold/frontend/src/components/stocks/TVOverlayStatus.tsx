import { useEffect, useState } from 'react'
import type { TVOverlayStatus as Status } from '@/types/stocks'

export function TVOverlayStatus() {
  const [status, setStatus] = useState<Status | null>(null)
  const [copied, setCopied] = useState(false)

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
        <button
          onClick={copyUrl}
          className="px-2 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded"
        >
          {copied ? 'Copied' : 'Copy userscript URL'}
        </button>
      </div>
      <div className="flex items-center gap-2 mb-1">
        <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
        <span className="text-zinc-300">
          {attached ? `${status.attached_clients} client${status.attached_clients > 1 ? 's' : ''} attached` : 'No overlay clients'}
        </span>
      </div>
      {!attached && (
        <div className="text-zinc-500 mt-1 leading-tight">
          Install the userscript in Tampermonkey, then open NQ on TradingView.
        </div>
      )}
      <div className="flex gap-3 mt-2 text-zinc-500">
        <span>{status.draw_count} drawn</span>
        {ageS !== null && <span>painted {ageS}s ago</span>}
      </div>
      {status.error && <div className="text-red-400 mt-1 truncate" title={status.error}>{status.error}</div>}
    </div>
  )
}
