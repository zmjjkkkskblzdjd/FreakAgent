import { defineStore } from 'pinia'
import { defaultAssistant } from '@/data/mock'
import { useAuthStore } from './auth'
import {
  createProvider,
  fetchProviderModels,
  getModelPref,
  listProviders,
  saveRoutes,
  setModelPref,
  setProviderKey,
  testProvider as testProviderApi,
  updateProvider
} from '@/api/providers'

const ONBOARDED_KEY = 'cherryUi_onboarded'



const KB_FALLBACK_KEY = 'cherryUi_kbFallback'
const DS_FALLBACK_KEY = 'cherryUi_datasourceFallback'


const WEB_KEY = 'cherryUi_webEnabled'

function readFlag(key) {
  return localStorage.getItem(key) === 'true'
}

function readFlagDefaultTrue(key) {
  const raw = localStorage.getItem(key)
  return raw === null ? true : raw === 'true'
}



function onboardKey(userId) {
  return userId ? `${ONBOARDED_KEY}_${userId}` : ONBOARDED_KEY
}


function normalize(p) {
  return {
    id: p.id,
    name: p.name,
    providerType: p.provider_type,
    baseUrl: p.base_url,
    color: p.color || '#64748b',
    desc: p.desc || '',
    keyUrl: p.key_url || '',
    enabled: !!p.enabled,
    hasKey: !!p.has_key,
    apiKeyMasked: p.api_key_masked || '',
    keyFingerprint: p.key_fingerprint || '',
    apiKey: '', 
    testing: false,
    connected: !!p.has_key,
    models: p.models || [],
    selectedModels: p.selected_models || [],
    fetchingModels: false,
    modelsError: '',
    
    modelsSource: p.models_source || '',
    modelsSyncedAt: p.models_synced_at || ''
  }
}

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    theme: 'light',
    onboardingCompleted: false,
    onboardingLoaded: false,
    providers: [],
    providersLoaded: false,
    providersError: '',
    defaultChatModel: defaultAssistant.model,
    defaultTranslateModel: defaultAssistant.translateModel,
    fontSize: 14,
    language: 'zh-CN',
    kbFallback: readFlag(KB_FALLBACK_KEY),
    datasourceFallback: readFlag(DS_FALLBACK_KEY),
    webEnabled: readFlagDefaultTrue(WEB_KEY),
    
    
    modelPrefOptions: [],
    modelPrefSelected: null,
    modelPrefLoaded: false,
    modelPrefError: ''
  }),

  getters: {
    
    enabledProviders: (state) => state.providers.filter((p) => p.enabled),
    
    allEnabledModels: (state) => {
      const list = []
      state.providers.forEach((p) => {
        if (!p.enabled) return
        p.models.forEach((m) => {
          if (p.selectedModels.includes(m.id)) {
            list.push({ ...m, providerId: p.id, providerName: p.name })
          }
        })
      })
      return list
    },
    
    
    
    routeModels() {
      if (this.modelPrefOptions.length) {
        return this.modelPrefOptions.map((o) => ({
          key: `${o.provider_id}:${o.model}`,
          providerId: o.provider_id,
          providerName: o.provider_name,
          id: o.model,
          name: o.model
        }))
      }
      return this.allEnabledModels.map((m) => ({
        key: `${m.providerId}:${m.id}`,
        providerId: m.providerId,
        providerName: m.providerName,
        id: m.id,
        name: m.name
      }))
    },
    
    currentModelKey() {
      if (this.modelPrefSelected) {
        return `${this.modelPrefSelected.provider_id}:${this.modelPrefSelected.model}`
      }
      return this.routeModels[0]?.key || ''
    }
  },

  actions: {
    setTheme(theme) {
      this.theme = theme
    },

    
    setKbFallback(on) {
      this.kbFallback = !!on
      localStorage.setItem(KB_FALLBACK_KEY, String(this.kbFallback))
    },

    setDatasourceFallback(on) {
      this.datasourceFallback = !!on
      localStorage.setItem(DS_FALLBACK_KEY, String(this.datasourceFallback))
    },

    
    setWebEnabled(on) {
      this.webEnabled = !!on
      localStorage.setItem(WEB_KEY, String(this.webEnabled))
    },

    
    async loadProviders(force = false) {
      if (this.providersLoaded && !force) return
      try {
        const data = await listProviders()
        this.providers = (data?.providers || []).map(normalize)
        this.providersError = ''
      } catch (e) {
        this.providersError = e?.message || '模型服务加载失败'
      } finally {
        this.providersLoaded = true
        const models = this.allEnabledModels
        if (!models.find((m) => m.id === this.defaultChatModel)) {
          this.defaultChatModel = models[0]?.id || ''
        }
        if (!models.find((m) => m.id === this.defaultTranslateModel)) {
          this.defaultTranslateModel = models[0]?.id || ''
        }
        this.loadModelPref()
      }
    },

    
    async loadModelPref(force = false) {
      if (this.modelPrefLoaded && !force) return
      try {
        const d = await getModelPref('chat')
        this.modelPrefOptions = d?.options || []
        this.modelPrefSelected = d?.selected || null
        this.modelPrefError = ''
      } catch (e) {
        
        this.modelPrefError = e?.message || '模型偏好加载失败'
      } finally {
        this.modelPrefLoaded = true
      }
    },

    
    async selectModelByKey(key) {
      const m = this.routeModels.find((x) => x.key === key)
      if (!m) return
      const prev = this.modelPrefSelected
      this.modelPrefSelected = { provider_id: m.providerId, model: m.id }
      this.modelPrefError = ''
      try {
        await setModelPref(m.providerId, m.id)
      } catch (e) {
        this.modelPrefSelected = prev
        this.modelPrefError = e?.message || '模型选择保存失败'
      }
    },

    async toggleProvider(id) {
      const p = this.providers.find((x) => x.id === id)
      if (!p) return
      const next = !p.enabled
      p.enabled = next
      try {
        await updateProvider(id, { enabled: next ? 1 : 0 })
      } catch (e) {
        p.enabled = !next
        this.providersError = e?.message || '保存失败'
      }
    },

    
    async saveProviderKey(id, key) {
      const p = this.providers.find((x) => x.id === id)
      if (!p) return
      const data = await setProviderKey(id, key)
      if (data?.provider) {
        const merged = normalize(data.provider)
        p.hasKey = merged.hasKey
        p.apiKeyMasked = merged.apiKeyMasked
        p.keyFingerprint = merged.keyFingerprint
        p.connected = merged.hasKey
      }
      p.apiKey = ''
      return data
    },

    async testProvider(id) {
      const p = this.providers.find((x) => x.id === id)
      if (!p) return { ok: false, error: 'provider_not_found' }
      p.testing = true
      try {
        const r = await testProviderApi(id)
        p.connected = !!r?.ok
        return r
      } finally {
        p.testing = false
      }
    },

    
    async fetchModels(id) {
      const p = this.providers.find((x) => x.id === id)
      if (!p) return { ok: false, error: 'provider_not_found' }
      p.fetchingModels = true
      p.modelsError = ''
      try {
        const r = await fetchProviderModels(id)
        if (!r?.ok) {
          p.modelsError = r?.hint || r?.error || '拉取失败'
          return r
        }
        const ids = new Set(p.models.map((m) => m.id))
        ;(r.models || []).forEach((m) => {
          if (!ids.has(m.id)) {
            p.models.push(m)
            ids.add(m.id)
          }
        })
        p.modelsSource = r.source || ''
        p.modelsSyncedAt = r.synced_at || ''
        return r
      } catch (e) {
        p.modelsError = e?.message || '拉取失败'
        return { ok: false, error: 'request_failed' }
      } finally {
        p.fetchingModels = false
      }
    },

    
    async addModel(providerId, rawName) {
      const modelId = String(rawName || '').trim().slice(0, 128)
      if (!modelId) return false
      const p = this.providers.find((x) => x.id === providerId)
      if (!p) return false
      if (!p.models.find((m) => m.id === modelId)) {
        p.models.push({ id: modelId, name: modelId })
      }
      if (!p.selectedModels.includes(modelId)) p.selectedModels.push(modelId)
      await this.persistRoutes()
      return true
    },

    
    async createCustomProvider(payload, modelNames = []) {
      const data = await createProvider(payload)
      const created = data?.provider
      if (!created?.id) throw new Error('创建失败')
      await this.loadProviders(true)
      const p = this.providers.find((x) => x.id === created.id)
      if (p) {
        modelNames
          .map((m) => String(m || '').trim())
          .filter(Boolean)
          .forEach((m) => {
            if (!p.models.find((x) => x.id === m)) p.models.push({ id: m, name: m })
            if (!p.selectedModels.includes(m)) p.selectedModels.push(m)
          })
        await this.persistRoutes()
      }
      return created
    },

    async toggleModel(providerId, modelId) {
      const p = this.providers.find((x) => x.id === providerId)
      if (!p) return
      const idx = p.selectedModels.indexOf(modelId)
      if (idx >= 0) p.selectedModels.splice(idx, 1)
      else p.selectedModels.push(modelId)
      await this.persistRoutes()
    },

    
    async persistRoutes() {
      const models = []
      this.providers.forEach((p) => {
        p.selectedModels.forEach((m) => models.push({ provider_id: p.id, model: m }))
      })
      try {
        await saveRoutes('chat', models)
        this.providersError = ''
        this.loadModelPref(true)   
      } catch (e) {
        this.providersError = e?.message || '模型选择保存失败'
      }
    },

    
    loadOnboarding() {
      const auth = useAuthStore()
      this.onboardingCompleted = localStorage.getItem(onboardKey(auth.user?.id)) === 'true'
      this.onboardingLoaded = true
    },
    
    completeOnboarding(skip = false) {
      const auth = useAuthStore()
      this.onboardingCompleted = true
      this.onboardingLoaded = true
      localStorage.setItem(onboardKey(auth.user?.id), 'true')
    },
    
    resetOnboarding() {
      const auth = useAuthStore()
      this.onboardingCompleted = false
      localStorage.removeItem(onboardKey(auth.user?.id))
    }
  }
})
