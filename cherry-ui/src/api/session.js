import { request } from './http'

export const listSessions = (params = {}) => {
  const qs = new URLSearchParams()
  if (params.limit) qs.set('limit', params.limit)
  if (params.updatedBefore) qs.set('updated_before', params.updatedBefore)
  const tail = qs.toString() ? `?${qs}` : ''
  return request(`/sessions${tail}`)
}
export const createSession = () => request('/sessions', { method: 'POST' })
export const listMessages = (threadId, params = {}) => {
  const qs = new URLSearchParams()
  if (params.beforeSeq) qs.set('before_seq', params.beforeSeq)
  if (params.limit) qs.set('limit', params.limit)
  const tail = qs.toString() ? `?${qs}` : ''
  return request(`/sessions/${threadId}/messages${tail}`)
}

export const patchSession = (threadId, patch) =>
  request(`/sessions/${threadId}`, { method: 'PATCH', body: patch })

export const removeSession = (threadId) =>
  request(`/sessions/${threadId}`, { method: 'DELETE' })


export const removeMessage = (threadId, messageId) =>
  request(`/sessions/${threadId}/messages/${encodeURIComponent(messageId)}`, {
    method: 'DELETE'
  })
