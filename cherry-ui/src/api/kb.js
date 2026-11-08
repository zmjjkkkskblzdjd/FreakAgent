import { KB_BASE, request } from './http'

const kb = (opts = {}) => ({ ...opts, base: KB_BASE })


export const listKbs = () => request('/kbs', kb())


export const createKb = (name, description) => {
  const body = { name }
  if (description !== undefined) body.description = description
  return request('/kbs', kb({ method: 'POST', body }))
}

export const renameKb = (id, name) =>
  request(`/kbs/${id}`, kb({ method: 'PATCH', body: { name } }))


export const setKbDescription = (id, description) =>
  request(`/kbs/${id}/description`, kb({ method: 'PATCH', body: { description } }))

export const removeKb = (id) => request(`/kbs/${id}`, kb({ method: 'DELETE' }))

export const listDocs = (id) => request(`/kbs/${id}/docs`, kb())


export async function uploadDoc(kbId, file) {
  const form = new FormData()
  form.append('file', file, file.name)
  const res = await fetch(`${KB_BASE}/kbs/${kbId}/docs`, { method: 'POST', body: form })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = data?.detail ?? data
    throw new Error(detail?.hint || detail?.error || `HTTP ${res.status}`)
  }
  return data
}

export const removeDoc = (kbId, docId) =>
  request(`/kbs/${kbId}/docs/${docId}`, kb({ method: 'DELETE' }))

export const listChunks = (kbId, docId, limit = 5) =>
  request(`/kbs/${kbId}/docs/${docId}/chunks?limit=${limit}`, kb())


export const searchKb = (kbId, { query, topK = 5, threshold = 0 }) =>
  request(`/kbs/${kbId}/search`, kb({
    method: 'POST',
    body: { query, top_k: topK, threshold },
    timeout: 120000
  }))




export const listKbMembers = () => request('/members', kb())


export const listKbGrants = (kbId) => request(`/kbs/${kbId}/grants`, kb())


export const setKbGrant = (kbId, userId, perm) =>
  request(`/kbs/${kbId}/grants`, kb({ method: 'PUT', body: { user_id: userId, perm } }))

export const revokeKbGrant = (kbId, userId) =>
  request(`/kbs/${kbId}/grants/${userId}`, kb({ method: 'DELETE' }))
