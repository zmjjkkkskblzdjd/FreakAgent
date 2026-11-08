import { defineStore } from 'pinia'
import { useAssistantStore } from './assistant'
import { useSettingsStore } from './settings'
import * as sessionApi from '@/api/session'
import { streamChat, resumeChat, cancelAgent, undoWrite as undoAgent } from '@/api/agent'
import { listDataSources } from '@/api/datasources'



const streamControllers = new Map()


const PHASE_TEXT = {
  recall_memory: '正在回忆历史…',
  compress_history: '正在整理上下文…',
  resolve_question: '正在理解指代…',
  recall_catalog: '正在匹配数据资产…',
  router: '正在判断路由…',
  preflight: '正在规划执行方案…',
  rag: '正在查询资料…',
  sql: '正在查询数据库…',
  approve_write: '等待写入审批…',
  execute_write: '正在执行写入…',
  merge: '正在生成报告…',
  web_attribution: '正在核对来源…',
  finalize: '正在检查结果…',
  chitchat: '正在回复…',
  web: '正在联网搜索…'
}


function markStep(msg, node, state) {
  const label = PHASE_TEXT[node]
  if (!label) return
  if (!Array.isArray(msg.steps)) msg.steps = []
  let step = msg.steps.find((s) => s.node === node)
  if (state === 'start') {
    if (!step) {
      step = { node, label, state: 'running' }
      msg.steps.push(step)
    } else {
      step.state = 'running'
    }
    msg.phase = label
  } else if (state === 'done') {
    if (!step) msg.steps.push({ node, label, state: 'done' })
    else step.state = 'done'
  }
}

function formatTime(ts) {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}


function historyMessage(item) {
  const meta = item.metadata || {}
  return {
    id: item.id,
    role: item.role,
    content: item.content,
    status: item.status === 'interrupted' ? 'interrupt'
      : item.status === 'error' ? 'error'
        : item.status === 'cancelled' ? 'cancelled' : 'done',
    citations: meta.citations || [],
    sql: meta.sql || null,
    validationErrors: meta.validation_errors || [],
    mode: meta.mode || '',
    channel: meta.channel || null,
    writes: meta.writes || [],
    
    kbEmptyNames: meta.kb_empty_names || [],
    refusalReason: meta.refusal_reason || '',
    interrupt: meta.interrupt || null,
    error: meta.error || ''
  }
}


function applyFinal(msg, report) {
  if (report?.answer_md) msg.content = report.answer_md
  else if (report?.event === 'final' && !report?.data && !msg.content) msg.content = '（本轮没有产生答案）'
  msg.citations = report?.citations || []
  msg.sql = report?.sql || null
  msg.validationErrors = report?.validation_errors || []
  msg.mode = report?.mode || ''
  msg.channel = report?.channel || null
  msg.writes = report?.writes || []
  msg.kbEmptyNames = report?.kb_empty_names || []
  msg.refusalReason = report?.refusal_reason || ''
  msg.status = 'done'
  msg.phase = ''
  msg.interrupt = null
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessions: [],
    currentSessionId: null,
    draftInput: '',
    sendingBySession: {},         
    loadingSessions: false,
    error: '',
    kbPanelOpen: false,
    selectedKbIds: [],            
    datasources: [],              
    datasourceId: null,           
    datasourcesLoaded: false
  }),

  getters: {
    groupedByAssistant: (state) => {
      const assistantStore = useAssistantStore()
      return assistantStore.assistants.map((a) => ({
        assistant: a,
        items: state.sessions.filter((s) => s.assistantId === a.id)
      }))
    },
    currentSession: (state) =>
      state.sessions.find((s) => s.id === state.currentSessionId),
    
    isSending: (state) => (id = state.currentSessionId) =>
      !!(id && state.sendingBySession[id]),
    currentAssistant: (state) => {
      const s = state.sessions.find((x) => x.id === state.currentSessionId)
      if (!s) return null
      const assistantStore = useAssistantStore()
      return assistantStore.assistants.find((a) => a.id === s.assistantId)
    }
  },

  actions: {
    async loadSessions() {
      this.loadingSessions = true
      this.error = ''
      try {
        const data = await sessionApi.listSessions()
        const assistantStore = useAssistantStore()
        const fallback = assistantStore.assistants[0]?.id || null
        const old = new Map(this.sessions.map((s) => [s.id, s]))

        this.sessions = (data?.sessions || []).map((raw) => {
          const prev = old.get(raw.thread_id)
          return {
            id: raw.thread_id,
            thread_id: raw.thread_id,
            assistantId: prev?.assistantId || fallback,
            title: raw.title,
            pinned: !!raw.pinned,
            message_count: raw.message_count || 0,
            preview: raw.last_preview || '',
            time: formatTime(raw.updated_at),
            messages: prev?.messages || [],
            loaded: prev?.loaded || false,
            datasourceId: prev?.datasourceId ?? null
          }
        })

        if (!this.sessions.some((s) => s.id === this.currentSessionId)) {
          this.currentSessionId = this.sessions[0]?.id || null
        }
        if (this.currentSessionId) await this.loadMessages(this.currentSessionId)
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingSessions = false
      }
    },

    async loadMessages(id) {
      const s = this.sessions.find((x) => x.id === id)
      if (!s || s.loaded) return
      try {
        
        let beforeSeq = null
        let collected = []
        for (let page = 0; page < 5; page++) {
          const data = await sessionApi.listMessages(id, { limit: 200, beforeSeq })
          const batch = data?.messages || []
          collected = [...batch, ...collected]
          if (!data?.has_more || !data?.next_before_seq) break
          beforeSeq = data.next_before_seq
        }
        s.messages = collected.map(historyMessage)
        s.loaded = true
      } catch {
        
      }
    },

    selectSession(id) {
      this.currentSessionId = id
      const s = this.sessions.find((x) => x.id === id)
      
      this.datasourceId = s?.datasourceId ?? null
      this.loadMessages(id)
    },

    async loadDatasources(force = false) {
      if (this.datasourcesLoaded && !force) return
      try {
        const data = await listDataSources()
        this.datasources = (data?.datasources || []).filter((d) => d.enabled !== 0)
        this.datasourcesLoaded = true
        
        if (this.datasourceId != null
          && !this.datasources.some((d) => d.id === this.datasourceId)) {
          this.datasourceId = null
          if (this.currentSession) this.currentSession.datasourceId = null
        }
      } catch {
        
      }
    },

    selectDatasource(id) {
      this.datasourceId = id ?? null
      if (this.currentSession) this.currentSession.datasourceId = this.datasourceId
    },

    async newSession(assistantId = null) {
      const assistantStore = useAssistantStore()
      const aid = assistantId || assistantStore.assistants[0]?.id || null
      try {
        const data = await sessionApi.createSession()
        const session = {
          id: data.thread_id,
          thread_id: data.thread_id,
          assistantId: aid,
          title: '新对话',
          pinned: false,
          time: '刚刚',
          messages: [],
          loaded: true,
          datasourceId: this.datasourceId ?? null
        }
        this.sessions.unshift(session)
        this.currentSessionId = session.id
        return session
      } catch (e) {
        this.error = e.message
        return null
      }
    },

    async deleteSession(id) {
      try {
        await sessionApi.removeSession(id)
      } catch (e) {
        this.error = e.message
        return
      }
      const idx = this.sessions.findIndex((s) => s.id === id)
      if (idx < 0) return
      this.sessions.splice(idx, 1)
      if (this.currentSessionId === id) {
        const next = this.sessions[idx] || this.sessions[idx - 1]
        this.currentSessionId = next?.id || null
      }
    },

    
    async deleteMessage(messageId, threadId = this.currentSessionId) {
      const s = this.sessions.find((x) => x.id === threadId)
      if (!s || !messageId) return false
      if (this.sendingBySession[threadId]) {
        this.error = '本轮生成中，请先终止再删除'
        return false
      }
      try {
        await sessionApi.removeMessage(threadId, messageId)
      } catch (e) {
        this.error = e.message
        return false
      }
      const idx = s.messages.findIndex((m) => m.id === messageId)
      if (idx >= 0) s.messages.splice(idx, 1)
      this.error = ''
      this.syncTitles()
      return true
    },

    
    async regenerateMessage(messageId, threadId = this.currentSessionId) {
      const s = this.sessions.find((x) => x.id === threadId)
      if (!s) return false
      if (this.sendingBySession[threadId]) {
        this.error = '本轮生成中，请先终止再重新生成'
        return false
      }
      const idx = s.messages.findIndex((m) => m.id === messageId && m.role === 'assistant')
      if (idx < 0) return false
      const userMsg = s.messages.slice(0, idx).reverse().find((m) => m.role === 'user')
      const text = (userMsg?.content || '').trim()
      if (!text) {
        this.error = '找不到对应的提问，无法重新生成'
        return false
      }
      if (!(await this.deleteMessage(messageId, threadId))) return false
      
      if (/^m\d+$/.test(userMsg.id)) {
        const i = s.messages.findIndex((m) => m.id === userMsg.id)
        if (i >= 0) s.messages.splice(i, 1)
        this.syncTitles()
      } else if (!(await this.deleteMessage(userMsg.id, threadId))) {
        return false
      }
      this.error = ''
      await this.sendMessage(text)
      return true
    },

    async togglePin(id) {
      const s = this.sessions.find((x) => x.id === id)
      if (!s) return
      const next = !s.pinned
      try {
        await sessionApi.patchSession(id, { pinned: next })
        s.pinned = next
      } catch (e) {
        this.error = e.message
      }
    },

    toggleKbPanel() {
      this.kbPanelOpen = !this.kbPanelOpen
    },

    toggleKbSelect(id) {
      const idx = this.selectedKbIds.indexOf(id)
      if (idx >= 0) this.selectedKbIds.splice(idx, 1)
      else this.selectedKbIds.push(id)
    },

    async sendMessage(content) {
      const text = (content || '').trim()
      if (!text) return

      const session = this.currentSession || (await this.newSession())
      if (!session) return
      const tid = session.thread_id
      
      if (this.sendingBySession[tid]) return

      session.messages.push({
        id: 'm' + Date.now(),
        role: 'user',
        content: text,
        status: 'done',
        citations: [],
        sql: null,
        validationErrors: []
      })
      const ai = {
        id: 'm' + (Date.now() + 1),
        role: 'assistant',
        content: '',
        phase: '正在连接…',
        status: 'streaming',
        steps: [],        
        thinking: '',     
        citations: [],
        sql: null,
        validationErrors: [],
        kbEmptyNames: [],
        refusalReason: '',
        interrupt: null,
        error: ''
      }
      session.messages.push(ai)
      const msg = session.messages[session.messages.length - 1]   

      this.draftInput = ''
      this.sendingBySession[tid] = true
      this.error = ''
      const ctrl = new AbortController()
      streamControllers.set(tid, ctrl)

      try {
        const settings = useSettingsStore()
        const dsId = session.datasourceId ?? this.datasourceId ?? null
        await streamChat(
          {
            sessionId: session.id,
            threadId: tid,
            message: text,
            
            kbIds: this.selectedKbIds.length
              ? [...this.selectedKbIds]
              : (settings.kbFallback ? ['default'] : []),
            kbFallback: settings.kbFallback,
            
            datasourceId: dsId,
            datasourceEnabled: dsId !== null || settings.datasourceFallback,
            
            webEnabled: settings.webEnabled
          },
          {
            
            meta: (m) => {
              const userMsg = session.messages[session.messages.length - 2]
              if (m?.user_message_id && userMsg) userMsg.id = m.user_message_id
              if (m?.assistant_message_id) msg.id = m.assistant_message_id
              if (m?.request_id) session.requestId = m.request_id
            },
            node: (d) => markStep(msg, d.node, d.state || 'start'),
            
            thinking: (d) => { msg.thinking += d.delta || '' },
            token: (d) => { msg.phase = ''; msg.content += d.delta },
            interrupt: (p) => { msg.interrupt = p; msg.phase = ''; msg.status = 'interrupt' },
            final: (r) => applyFinal(msg, r),
            
            cancelled: () => { msg.status = 'cancelled'; msg.phase = ''; msg.interrupt = null },
            error: (e) => { msg.status = 'error'; msg.error = e.message || '出错了'; msg.phase = '' }
          },
          ctrl.signal
        )
      } catch (e) {
        if (ctrl.signal.aborted) {
          
          msg.status = 'cancelled'
          msg.phase = ''
          msg.interrupt = null
        } else {
          msg.status = 'error'
          msg.error = e.message
          msg.phase = ''
        }
      } finally {
        streamControllers.delete(tid)
        delete this.sendingBySession[tid]
        if (msg.status === 'streaming') msg.status = 'done'
      }
      this.syncTitles()
    },

    async resume(command) {
      const session = this.currentSession
      if (!session) return
      const tid = session.thread_id
      if (this.sendingBySession[tid]) return
      const msg = [...session.messages].reverse()
        .find((m) => m.role === 'assistant' && m.interrupt)
      if (!msg) return

      this.sendingBySession[tid] = true
      const ctrl = new AbortController()
      streamControllers.set(tid, ctrl)
      
      msg.status = 'streaming'
      msg.phase = '正在恢复…'
      
      
      const answered = msg.interrupt
      msg.interrupt = null
      try {
        const r = await resumeChat(tid, command, ctrl.signal)
        if (r?.event === 'interrupt') {
          msg.interrupt = r.data
          msg.status = 'interrupt'
          msg.phase = ''
        } else if (r?.event === 'cancelled') {
          msg.status = 'cancelled'
          msg.interrupt = null
          msg.phase = ''
        } else {
          applyFinal(msg, r)
        }
      } catch (e) {
        msg.interrupt = ctrl.signal.aborted ? null : answered
        if (ctrl.signal.aborted) {
          msg.status = 'cancelled'
        } else {
          msg.status = 'error'
          msg.error = e.message
        }
        msg.phase = ''
      } finally {
        streamControllers.delete(tid)
        delete this.sendingBySession[tid]
      }
      this.syncTitles()
    },

    
    async cancelSession(threadId = this.currentSessionId) {
      if (!threadId) return
      streamControllers.get(threadId)?.abort()
      streamControllers.delete(threadId)

      const s = this.sessions.find((x) => x.id === threadId)
      const msg = [...(s?.messages || [])].reverse()
        .find((m) => m.role === 'assistant' && m.status === 'streaming')
      if (msg) {
        msg.status = 'cancelled'
        msg.phase = ''
        msg.interrupt = null
        msg.error = ''
      }
      delete this.sendingBySession[threadId]

      try {
        await cancelAgent(s?.thread_id || threadId)
      } catch {
        
      }
    },

    async undoLastWrite(threadId, messageId) {
      const res = await undoAgent(threadId)
      const s = this.sessions.find((x) => x.id === threadId)
      const msg = s?.messages.find((m) => m.id === messageId)
      if (msg) {
        
        msg.writes = (msg.writes || []).map((w) => ({ ...w, undo_available: false }))
        if (res?.answer_md) msg.content = res.answer_md
      }
      return res
    },

    async syncTitles() {
      try {
        const data = await sessionApi.listSessions()
        for (const raw of data?.sessions || []) {
          const s = this.sessions.find((x) => x.id === raw.thread_id)
          if (!s) continue
          s.title = raw.title
          s.pinned = !!raw.pinned
          s.message_count = raw.message_count || 0
          s.preview = raw.last_preview || ''
          s.time = formatTime(raw.updated_at)
        }
      } catch {
        
      }
    }
  }
})
