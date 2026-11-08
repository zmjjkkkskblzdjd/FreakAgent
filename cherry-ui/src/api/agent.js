import { API_BASE, request } from './http'


export async function streamChat({ sessionId, threadId, message, kbIds = [], kbFallback = true, datasourceId = null, datasourceEnabled = true, webEnabled = true }, handlers = {}, signal) {
  const res = await fetch(`${API_BASE}/agent/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      thread_id: threadId,
      message,
      
      kb_ids: kbIds,
      kb_fallback: kbFallback,
      
      datasource_id: datasourceId ?? null,
      
      datasource_enabled: datasourceEnabled,
      
      web_enabled: webEnabled
    }),
    signal
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) {
        let event = 'message'
        let data = ''
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
          
        }
        if (!data) continue
        let payload
        try { payload = JSON.parse(data) } catch { continue }
        handlers[event]?.(payload)
      }
    }
  } finally {
    reader.releaseLock?.()
  }
}


export function resumeChat(threadId, command, signal) {
  return request('/agent/resume', {
    method: 'POST',
    body: { thread_id: threadId, command },
    timeout: 900000,
    signal
  })
}


export function cancelAgent(threadId) {
  return request('/agent/cancel', {
    method: 'POST',
    body: { thread_id: threadId },
    timeout: 15000
  })
}


export function undoWrite(threadId) {
  return request('/agent/undo', {
    method: 'POST',
    body: { thread_id: threadId },
    timeout: 60000
  })
}
