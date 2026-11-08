import { defineStore } from 'pinia'
import {
  createKb,
  listChunks,
  listDocs,
  listKbs,
  removeDoc,
  removeKb,
  renameKb,
  setKbDescription,
  uploadDoc
} from '@/api/kb'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))


function fmtDate(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}


function fmtSize(n) {
  if (!n) return '0B'
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / 1024 / 1024).toFixed(1)}MB`
}

function mapKb(k) {
  return {
    id: k.id,
    name: k.name,
    description: k.description || '',
    docCount: k.doc_count || 0,
    embeddingModel: k.embedding_model,
    chunkSize: k.chunk_size,
    overlap: k.overlap,
    updateTime: fmtDate(k.created_at),
    canWrite: !!k.can_write,
    canDelete: !!k.can_delete,
    canManageGrants: !!k.can_manage_grants,
    docs: []
  }
}

function mapDoc(d) {
  return {
    id: d.id,
    name: d.name,
    size: fmtSize(d.size),
    chunks: d.chunks,
    status: d.status,
    error: d.error,
    preview: d.preview
  }
}

const PENDING = ['解析中', '索引中']

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    bases: [],
    currentKbId: null,
    currentDocId: null,
    uploading: false,
    loading: false,
    chunks: [],
    chunksLoading: false,
    error: ''
  }),

  getters: {
    currentBase: (state) => state.bases.find((b) => b.id === state.currentKbId),
    currentDoc: (state) => {
      const base = state.bases.find((b) => b.id === state.currentKbId)
      if (!base) return null
      return base.docs.find((d) => d.id === state.currentDocId)
    }
  },

  actions: {
    
    async load() {
      this.loading = true
      this.error = ''
      try {
        const { kbs } = await listKbs()
        this.bases = kbs.map(mapKb)
        if (!this.bases.some((b) => b.id === this.currentKbId)) {
          this.currentKbId = this.bases[0]?.id || null
        }
        if (this.currentKbId) await this.loadDocs(this.currentKbId)
      } catch (e) {
        this.error = e.message
      } finally {
        this.loading = false
      }
    },

    async loadDocs(kbId) {
      const base = this.bases.find((b) => b.id === kbId)
      if (!base) return
      try {
        const { docs } = await listDocs(kbId)
        base.docs = docs.map(mapDoc)
        base.docCount = base.docs.length
        if (!base.docs.some((d) => d.id === this.currentDocId)) {
          this.currentDocId = base.docs[0]?.id || null
        }
      } catch (e) {
        this.error = e.message
      }
    },

    selectBase(id) {
      this.currentKbId = id
      this.currentDocId = null
      this.chunks = []
      this.loadDocs(id)
    },

    selectDoc(id) {
      this.currentDocId = id
    },

    
    async loadChunks(kbId, docId) {
      if (kbId !== this.currentKbId || docId !== this.currentDocId) return
      this.chunksLoading = true
      this.chunks = []
      try {
        const { chunks } = await listChunks(kbId, docId, 8)
        if (docId === this.currentDocId) this.chunks = chunks
      } catch (e) {
        this.error = e.message
      } finally {
        if (docId === this.currentDocId) this.chunksLoading = false
      }
    },

    
    async upload(file) {
      const base = this.currentBase
      if (!base || !file) return
      this.uploading = true
      this.error = ''
      try {
        const doc = await uploadDoc(base.id, file)
        this.currentDocId = doc.id
        await this.pollDocs(base.id)
      } catch (e) {
        this.error = e.message
      } finally {
        this.uploading = false
      }
    },

    
    async pollDocs(kbId) {
      const deadline = Date.now() + 120000
      await this.loadDocs(kbId)
      while (Date.now() < deadline) {
        const base = this.bases.find((b) => b.id === kbId)
        const pending = (base?.docs || []).some((d) => PENDING.includes(d.status))
        if (!pending) break
        await sleep(1500)
        await this.loadDocs(kbId)
      }
      
      if (kbId === this.currentKbId && this.currentDocId && !this.chunks.length) {
        const doc = this.currentDoc?.status
        if (doc === '已索引') await this.loadChunks(kbId, this.currentDocId)
      }
    },

    async deleteDoc(docId) {
      const base = this.currentBase
      if (!base) return
      try {
        await removeDoc(base.id, docId)
        if (this.currentDocId === docId) this.chunks = []
        await this.loadDocs(base.id)
      } catch (e) {
        this.error = e.message
      }
    },

    async newBase(name = '', description) {
      try {
        const k = await createKb(name, description)
        this.bases.push(mapKb({ ...k, created_at: Date.now() / 1000,
          can_write: true, can_delete: true }))
        this.currentKbId = k.id
        this.currentDocId = null
        this.chunks = []
      } catch (e) {
        this.error = e.message
      }
    },

    async renameBase(id, name) {
      try {
        await renameKb(id, name)
        const b = this.bases.find((x) => x.id === id)
        if (b) b.name = name
      } catch (e) {
        this.error = e.message
      }
    },

    
    async setDescription(id, description) {
      const desc = (description || '').trim()
      try {
        const res = await setKbDescription(id, desc)
        const b = this.bases.find((x) => x.id === id)
        if (b) b.description = res?.description ?? desc
        return true
      } catch (e) {
        this.error = e.message
        return false
      }
    },

    async deleteBase(id) {
      try {
        await removeKb(id)
        await this.load()
      } catch (e) {
        this.error = e.message
      }
    }
  }
})
