import { request } from './http'


const ADMIN_BASE = import.meta.env.VITE_ADMIN_BASE || '/api/admin'

export const listUsers = ({ limit = 100, offset = 0 } = {}) =>
  request(`/users?limit=${limit}&offset=${offset}`, { base: ADMIN_BASE })


export const updateUser = (id, body) =>
  request(`/users/${id}`, { method: 'PATCH', body, base: ADMIN_BASE })


export const createUser = (body) =>
  request('/users', { method: 'POST', body, base: ADMIN_BASE })


export const resetUserPassword = (id, newPassword) =>
  request(`/users/${id}/reset-password`, {
    method: 'POST',
    body: newPassword ? { new_password: newPassword } : {},
    base: ADMIN_BASE
  })
