import { request } from './http'



export const DS_BASE = import.meta.env.VITE_DATASOURCES_BASE || '/api/datasources'

export const listDataSources = () => request('', { base: DS_BASE })

export const createDataSource = (body) =>
  request('', { method: 'POST', body, base: DS_BASE })

export const updateDataSource = (id, body) =>
  request(`/${id}`, { method: 'PATCH', body, base: DS_BASE })

export const deleteDataSource = (id) =>
  request(`/${id}`, { method: 'DELETE', base: DS_BASE })


export const testDataSource = (body) =>
  request('/test', { method: 'POST', body, base: DS_BASE })

export const initDataSource = (id) =>
  request(`/${id}/init`, { method: 'POST', base: DS_BASE })

export const listDataSourceTables = (id) =>
  request(`/${id}/tables`, { base: DS_BASE })


export const selectTables = (id, tables, selected = true) =>
  request(`/${id}/tables/select`, { method: 'POST', body: { tables, selected }, base: DS_BASE })

export const getMirror = (id) =>
  request(`/${id}/mirror`, { base: DS_BASE })

export const startMirror = (id) =>
  request(`/${id}/mirror/start`, { method: 'POST', base: DS_BASE })

export const stopMirror = (id) =>
  request(`/${id}/mirror/stop`, { method: 'POST', base: DS_BASE })

export const resyncMirror = (id) =>
  request(`/${id}/mirror/resync`, { method: 'POST', base: DS_BASE })

export const listGrants = (id) =>
  request(`/${id}/grants`, { base: DS_BASE })

export const addGrants = (id, userId, tables) =>
  request(`/${id}/grants`, { method: 'POST', body: { user_id: userId, tables }, base: DS_BASE })

export const removeGrants = (id, userId, tables = []) =>
  request(`/${id}/grants`, { method: 'DELETE', body: { user_id: userId, tables }, base: DS_BASE })


export const listMembers = () =>
  request('/users?limit=200', { base: '/api/admin' })
