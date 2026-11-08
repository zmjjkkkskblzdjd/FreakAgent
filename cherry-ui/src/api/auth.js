import { AUTH_BASE, request } from './http'



export const getCaptcha = () => request('/captcha', { base: AUTH_BASE })

export const register = (payload) =>
  request('/register', { method: 'POST', body: payload, base: AUTH_BASE })

export const login = (payload) =>
  request('/login', { method: 'POST', body: payload, base: AUTH_BASE })

export const logout = () => request('/logout', { method: 'POST', base: AUTH_BASE })

export const me = () => request('/me', { base: AUTH_BASE })

export const changePassword = (payload) =>
  request('/password', { method: 'POST', body: payload, base: AUTH_BASE })
