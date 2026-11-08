import { defineStore } from 'pinia'
import { assistants as mockAssistants } from '@/data/mock'

export const useAssistantStore = defineStore('assistant', {
  state: () => ({
    assistants: JSON.parse(JSON.stringify(mockAssistants)),
    currentId: mockAssistants[0]?.id || null
  }),

  getters: {
    current: (state) => state.assistants.find((a) => a.id === state.currentId),
    byId: (state) => (id) => state.assistants.find((a) => a.id === id)
  },

  actions: {
    select(id) {
      this.currentId = id
    },
    update(id, patch) {
      const a = this.assistants.find((x) => x.id === id)
      if (a) Object.assign(a, patch)
    },
    newAssistant() {
      const id = 'a' + Date.now()
      const a = {
        id,
        name: '未命名助手',
        avatar: '🤖',
        color: '#13b5b1',
        tags: [],
        desc: '',
        prompt: '',
        temperature: 0.7,
        maxTokens: 4096,
        topP: 0.9,
        model: '',
        knowledgeIds: []
      }
      this.assistants.unshift(a)
      this.currentId = id
      return a
    },
    deleteAssistant(id) {
      const idx = this.assistants.findIndex((x) => x.id === id)
      if (idx >= 0) this.assistants.splice(idx, 1)
      if (this.currentId === id) {
        this.currentId = this.assistants[0]?.id || null
      }
    }
  }
})
