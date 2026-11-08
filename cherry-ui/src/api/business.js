import { KB_BASE, request } from './http'


const biz = (opts = {}) => ({ ...opts, base: KB_BASE })


export const listBusinessTerms = (keyword = '') =>
  request(`/business/terms${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`, biz())

export const createBusinessTerm = (body) =>
  request('/business/terms', biz({ method: 'POST', body }))

export const updateBusinessTerm = (id, body) =>
  request(`/business/terms/${id}`, biz({ method: 'PATCH', body }))

export const deleteBusinessTerm = (id) =>
  request(`/business/terms/${id}`, biz({ method: 'DELETE' }))


export const setBusinessRecall = (id, isRecall) =>
  request(`/business/terms/${id}/recall`, biz({ method: 'POST', body: { is_recall: isRecall } }))


export const syncBusinessTerms = () =>
  request('/business/sync', biz({ method: 'POST', timeout: 180000 }))
