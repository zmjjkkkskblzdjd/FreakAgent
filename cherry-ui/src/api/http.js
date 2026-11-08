


export const AUTH_BASE = import.meta.env.VITE_AUTH_BASE || '/api/auth'
export const API_BASE = import.meta.env.VITE_API_BASE || '/api/ua'
export const KB_BASE = import.meta.env.VITE_KB_BASE || '/api/kb'



let unauthorizedHandler = null
export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn
}

export async function request(
  path,
  { method = 'GET', body, signal, timeout = 30000, base = API_BASE } = {}
) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  if (signal) signal.addEventListener('abort', () => ctrl.abort(), { once: true })

  try {
    const res = await fetch(base + path, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal
    })
    const text = await res.text()
    let data = null
    if (text) {
      try { data = JSON.parse(text) } catch { data = text }
    }
    if (!res.ok) {
      const detail = data?.detail ?? data
      const msg = typeof detail === 'string'
        ? detail
        : (detail?.hint || detail?.error || `HTTP ${res.status}`)
      const err = new Error(msg)
      err.status = res.status
      err.detail = detail
      if (res.status === 401 && base !== AUTH_BASE) unauthorizedHandler?.(err)
      throw err
    }
    return data
  } finally {
    clearTimeout(timer)
  }
}
