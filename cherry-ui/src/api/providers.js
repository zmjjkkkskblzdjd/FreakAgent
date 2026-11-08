import { request } from './http'



export const PROVIDER_BASE = import.meta.env.VITE_PROVIDER_BASE || '/api/providers'
export const ROUTES_BASE = import.meta.env.VITE_ROUTES_BASE || '/api/routes'

export const listProviders = () => request('', { base: PROVIDER_BASE })

export const createProvider = (body) =>
  request('', { method: 'POST', body, base: PROVIDER_BASE })

export const updateProvider = (id, body) =>
  request(`/${id}`, { method: 'PATCH', body, base: PROVIDER_BASE })

export const deleteProvider = (id) =>
  request(`/${id}`, { method: 'DELETE', base: PROVIDER_BASE })


export const setProviderKey = (id, apiKey) =>
  request(`/${id}/key`, { method: 'PUT', body: { api_key: apiKey }, base: PROVIDER_BASE })

export const testProvider = (id) =>
  request(`/${id}/test`, { method: 'POST', base: PROVIDER_BASE })


export const fetchProviderModels = (id) =>
  request(`/${id}/models`, { method: 'POST', base: PROVIDER_BASE })

export const listRoutes = (purpose = 'chat') =>
  request(`?purpose=${encodeURIComponent(purpose)}`, { base: ROUTES_BASE })

export const saveRoutes = (purpose, models) =>
  request('', { method: 'PUT', body: { purpose, models }, base: ROUTES_BASE })



export const MODEL_PREF_BASE =
  import.meta.env.VITE_MODEL_PREF_BASE || '/api/model-pref'

export const getModelPref = (purpose = 'chat') =>
  request(`?purpose=${encodeURIComponent(purpose)}`, { base: MODEL_PREF_BASE })

export const setModelPref = (providerId, model, purpose = 'chat') =>
  request('', {
    method: 'PUT',
    body: { purpose, provider_id: providerId ?? null, model: model ?? null },
    base: MODEL_PREF_BASE
  })
