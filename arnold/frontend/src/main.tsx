import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

// Surface escaped errors (useEffect throws, unhandled promises) to the user
window.addEventListener('error', (ev) => {
  const root = document.getElementById('root')
  if (root && root.childNodes.length === 0) {
    root.innerHTML = `<div style="color:#f87171;padding:24px;font-family:monospace;background:#111;min-height:100vh"><b>Uncaught error:</b><br>${ev.message}<br><pre style="font-size:11px;color:#aaa">${ev.error?.stack ?? ''}</pre></div>`
  }
})
window.addEventListener('unhandledrejection', (ev) => {
  const root = document.getElementById('root')
  if (root && root.childNodes.length === 0) {
    root.innerHTML = `<div style="color:#f87171;padding:24px;font-family:monospace;background:#111;min-height:100vh"><b>Unhandled rejection:</b><br>${ev.reason}</div>`
  }
})

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
