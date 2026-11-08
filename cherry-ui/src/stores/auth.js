import { defineStore } from 'pinia'
import * as authApi from '@/api/auth'
import { useChatStore } from './chat'


export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    tenant: null,
    initialized: false,
    loading: false,
    error: ''
  }),

  getters: {
    isLoggedIn: (s) => !!s.user,
    displayName: (s) => s.user?.username || s.user?.email || '',
    initial: (s) => (s.user?.username || s.user?.email || '?').slice(0, 1).toUpperCase(),
    isAdmin: (s) => ['owner', 'admin'].includes(s.user?.role || ''),
    
    isOwner: (s) => !!s.user?.is_owner,
    isDbAdmin: (s) => !!s.user?.is_db_admin,
    isKbAdmin: (s) => !!s.user?.is_kb_admin
  },

  actions: {
    apply(data) {
      this.user = data?.user || null
      this.tenant = data?.tenant || null
    },

    
    async fetchMe() {
      if (this.loading) return this.user
      this.loading = true
      try {
        this.apply(await authApi.me())
      } catch {
        this.user = null
        this.tenant = null
      } finally {
        this.initialized = true
        this.loading = false
      }
      return this.user
    },

    async login(payload) {
      this.error = ''
      const data = await authApi.login(payload)
      this.apply(data)
      this.initialized = true
      useChatStore().$reset()
      return this.user
    },

    async register(payload) {
      this.error = ''
      const data = await authApi.register(payload)
      this.apply(data)
      this.initialized = true
      useChatStore().$reset()
      return this.user
    },

    async logout() {
      try {
        await authApi.logout()
      } catch {
        
      }
      this.clear()
      useChatStore().$reset()
    },

    clear() {
      this.user = null
      this.tenant = null
      this.initialized = true
      this.error = ''
    }
  }
})
