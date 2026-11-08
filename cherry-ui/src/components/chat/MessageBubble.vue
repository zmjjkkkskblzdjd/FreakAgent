<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Icon from '@/components/common/Icon.vue'
import InterruptCard from './InterruptCard.vue'
import { useChatStore } from '@/stores/chat'

const props = defineProps({
  message: { type: Object, required: true }
})
const chat = useChatStore()

const isUser = computed(() => props.message.role === 'user')


const segments = computed(() => {
  const parts = props.message.content.split(/```/)
  const result = []
  parts.forEach((part, idx) => {
    if (idx % 2 === 0) {
      
      if (part.trim()) result.push({ type: 'text', content: part })
    } else {
      
      const lines = part.split('\n')
      const lang = lines[0].trim()
      const code = lang && !lang.includes(' ') ? lines.slice(1).join('\n') : part
      result.push({ type: 'code', lang: lang && !lang.includes(' ') ? lang : '', content: code })
    }
  })
  return result
})

const citations = computed(() => props.message.citations || [])
const sqlText = computed(() => props.message.sql?.sql || '')
const errors = computed(() => props.message.validationErrors || [])


const kbEmptyNames = computed(() => props.message.kbEmptyNames || [])
const scopeHint = computed(() => {
  if (citations.value.length || !kbEmptyNames.value.length) return ''
  if ((props.message.content || '').includes('检索范围说明')) return ''
  const tail = props.message.refusalReason === 'lane_failed' ? '，本次未形成可引用结论' : ''
  return `检索范围：${kbEmptyNames.value.join('、')} 本轮未检索到可用资料${tail}`
})

const channelText = computed(() => {
  const ch = props.message.channel
  if (!ch || !ch.source) return ''
  const label = ch.source === 'mysql-direct' ? '源库限额直读' : '镜像（ClickHouse）'
  const lag = ch.lag_ms != null ? `，滞后 ${ch.lag_ms}ms` : ''
  const note = ch.mirror_note ? `；${ch.mirror_note}` : ''
  return `数据通道：${label}${lag}${note}`
})
const streaming = computed(() => props.message.status === 'streaming')


const writes = computed(() => props.message.writes || [])
const undoBusy = ref(false)
const undoError = ref('')
const nowTick = ref(Date.now())
let countdownTimer = null
onMounted(() => {
  
  countdownTimer = setInterval(() => { nowTick.value = Date.now() }, 60000)
})
onUnmounted(() => { if (countdownTimer) clearInterval(countdownTimer) })

function fmtRemaining(iso) {
  const t = new Date(iso).getTime()
  if (!iso || Number.isNaN(t)) return '可撤回'
  const rem = t - nowTick.value
  if (rem <= 0) return '已过期'
  const mins = Math.ceil(rem / 60000)
  if (mins <= 1) return '即将到期'
  return mins > 10 ? `剩余 ${Math.ceil(mins / 10) * 10} 分钟` : `剩余 ${mins} 分钟`
}

async function undo() {
  if (undoBusy.value) return
  const w = writes.value[0] || {}
  const label = `${(w.operation || '').toUpperCase() || '写'}${w.table ? ' · ' + w.table : ''} · 影响 ${w.affected_rows ?? 0} 行`
  
  if (!window.confirm(`确认撤回本次写入？\n${label}\n撤回会再写一次，CDC/数仓可见。`)) return
  undoBusy.value = true
  undoError.value = ''
  try {
    const res = await chat.undoLastWrite(chat.currentSessionId, props.message.id)
    if (!res?.ok) undoError.value = res?.message || res?.error || '撤回失败'
  } catch (e) {
    undoError.value = e?.message || '撤回失败'
  } finally {
    undoBusy.value = false
  }
}
const steps = computed(() => props.message.steps || [])
const thinking = computed(() => props.message.thinking || '')
const thinkBody = ref(null)
const sqlOpen = ref(false)
const citeOpen = ref(false)


watch(thinking, async () => {
  await nextTick()
  const el = thinkBody.value
  if (el) el.scrollTop = el.scrollHeight
})

function copyCode(code) {
  navigator.clipboard?.writeText(code)
}
function copyAnswer() {
  navigator.clipboard?.writeText(props.message.content || '')
}

async function removeMessage() {
  if (!window.confirm('确认删除这条消息？删除后它不会再进入对话上下文，且不可恢复。')) return
  await chat.deleteMessage(props.message.id)
}

async function regenerate() {
  await chat.regenerateMessage(props.message.id)
}
</script>

<template>
  <div class="bubble-row" :class="{ user: isUser }">
    <div class="avatar" :class="isUser ? 'user-avatar' : 'ai-avatar'">
      <Icon :name="isUser ? 'user' : 'sparkles'" :size="18" />
    </div>
    <div class="bubble-body">
      <div class="bubble-name">{{ isUser ? '我' : '助手' }}</div>
      <div class="bubble" :class="isUser ? 'user-bubble' : 'ai-bubble'">
        <template v-for="(seg, i) in segments" :key="i">
          <pre v-if="seg.type === 'code'" class="code-block"><div class="code-head"><span class="lang">{{ seg.lang || 'code' }}</span><button class="copy" @click="copyCode(seg.content)"><Icon name="copy" :size="12" /> 复制</button></div><code>{{ seg.content }}</code></pre>
          <p v-else class="text-seg">{{ seg.content }}</p>
        </template>

        
        <div v-if="streaming && !message.content && steps.length" class="steps">
          <span
            v-for="(s, i) in steps"
            :key="s.node + i"
            class="step"
            :class="s.state"
          >
            <Icon v-if="s.state === 'done'" name="check" :size="10" />
            <span v-else class="step-dot" />
            {{ s.label }}
          </span>
        </div>

        
        <div v-if="streaming && !message.content && thinking" class="think-box">
          <div class="think-head">
            <Icon name="sparkles" :size="12" />
            <span>思考过程</span>
          </div>
          <div ref="thinkBody" class="think-body">{{ thinking }}</div>
        </div>

        
        <div v-if="message.phase && !steps.length" class="phase">
          <span class="phase-dot" />{{ message.phase }}
        </div>
        
        <span v-else-if="streaming" class="cursor" />
      </div>

      
      <div v-if="message.error" class="error-bar">
        <Icon name="close" :size="14" />
        <span>{{ message.error }}</span>
      </div>

      
      <div v-if="channelText" class="channel-hint">
        <Icon name="database" :size="12" />
        <span>{{ channelText }}</span>
      </div>

      
      <div v-if="scopeHint" class="channel-hint">
        <Icon name="database" :size="12" />
        <span>{{ scopeHint }}</span>
      </div>

      
      <div v-if="errors.length" class="warn-bar">
        <div class="warn-head"><Icon name="tag" :size="14" /> 以下校验未通过（答案仍交付）</div>
        <ul class="warn-list">
          <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
        </ul>
      </div>

      
      <div v-if="sqlText" class="extra-block">
        <div class="extra-head" @click="sqlOpen = !sqlOpen">
          <Icon :name="sqlOpen ? 'chevron-down' : 'chevron-right'" :size="14" />
          <span>执行 SQL</span>
          <button class="copy extra-copy" @click.stop="copyCode(sqlText)"><Icon name="copy" :size="12" /> 复制</button>
        </div>
        <pre v-if="sqlOpen" class="sql-block"><code>{{ sqlText }}</code></pre>
      </div>

      
      <div v-if="citations.length" class="extra-block">
        <div class="extra-head" @click="citeOpen = !citeOpen">
          <Icon :name="citeOpen ? 'chevron-down' : 'chevron-right'" :size="14" />
          <span>引用来源（{{ citations.length }}）</span>
        </div>
        <div v-if="citeOpen" class="cite-list">
          <div v-for="(c, i) in citations" :key="c.chunk_id || i" class="cite-item">
            <div class="cite-title">
              <Icon name="book" :size="13" />
              
              <span>{{ c.source || c.url || '未知来源' }}</span>
              <code class="cite-id">{{ c.chunk_id }}</code>
            </div>
            <div v-if="c.excerpt" class="cite-excerpt">{{ c.excerpt }}</div>
          </div>
        </div>
      </div>

      
      <div v-if="writes.length" class="write-bar" aria-live="polite">
        <div v-for="(w, i) in writes" :key="w.audit_id || i" class="write-row">
          <Icon name="database" :size="12" />
          <span class="write-text">
            {{ w.operation ? w.operation.toUpperCase() : '写' }}{{ w.table ? ' · ' + w.table : '' }} · 影响 {{ w.affected_rows }} 行
          </span>
          <span class="write-countdown">
            {{ w.undo_available ? fmtRemaining(w.undo_deadline) : '已撤回 / 不可撤回' }}
          </span>
          <button
            v-if="w.undo_available"
            class="write-undo"
            :disabled="undoBusy"
            @click="undo()"
          >{{ undoBusy ? '撤回中…' : '撤回' }}</button>
        </div>
        <div v-if="undoError" class="write-error">{{ undoError }}</div>
      </div>

      
      <div v-if="message.status === 'cancelled'" class="cancel-bar">
        <Icon name="stop" :size="12" />
        <span>{{ message.content ? '本次回答已终止' : '本次调用已终止' }}</span>
      </div>

      
      <InterruptCard
        v-if="message.interrupt"
        :interrupt="message.interrupt"
        :sending="chat.isSending(chat.currentSessionId)"
        @resume="chat.resume"
      />

      <div class="bubble-actions" v-if="message.status !== 'streaming'">
        <button v-if="!isUser" class="act" title="复制" @click="copyAnswer"><Icon name="copy" :size="14" /></button>
        <button
          v-if="!isUser"
          class="act"
          title="重新生成"
          :disabled="chat.isSending(chat.currentSessionId)"
          @click="regenerate"
        ><Icon name="refresh" :size="14" /></button>
        <button class="act danger" title="删除" @click="removeMessage"><Icon name="trash" :size="14" /></button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bubble-row {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}
.bubble-row.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.user-avatar {
  background: var(--accent);
  color: #fff;
}
.ai-avatar {
  background: var(--bg-active);
  color: var(--accent);
}
.bubble-body {
  max-width: 72%;
  min-width: 0;
}
.user .bubble-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.bubble-name {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  margin-bottom: 4px;
}
.bubble {
  padding: 10px 14px;
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  line-height: 1.65;
  word-break: break-word;
}
.user-bubble {
  background: var(--user-bubble);
  color: var(--text-primary);
  border-top-right-radius: 4px;
}
.ai-bubble {
  background: var(--assistant-bubble);
  border: 1px solid var(--border);
  border-top-left-radius: 4px;
}
.text-seg {
  margin: 0 0 8px;
  white-space: pre-wrap;
}
.text-seg:last-child {
  margin-bottom: 0;
}
.phase {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-elevated);
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
}
.step.running {
  border-color: var(--accent);
  color: var(--accent);
}
.step-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.2s infinite ease-in-out;
}
.think-box {
  margin-top: 8px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  overflow: hidden;
}
.think-head {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 9px;
  font-size: 11px;
  color: var(--text-muted);
  border-bottom: 1px dashed var(--border);
}
.think-body {
  max-height: 168px;
  overflow-y: auto;
  padding: 7px 9px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}
.phase-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.2s infinite ease-in-out;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}
.cursor {
  display: inline-block;
  width: 3px;
  height: 14px;
  background: var(--accent);
  vertical-align: middle;
  animation: pulse 1s infinite steps(2);
}
.code-block {
  margin: 8px 0;
  background: #1e2329;
  color: #e6eaed;
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.code-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.05);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.lang {
  font-size: 11px;
  color: #9aa7af;
  text-transform: lowercase;
}
.copy {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #aab2b8;
}
.copy:hover {
  color: #fff;
}
.code-block code {
  display: block;
  padding: 12px;
  white-space: pre;
  overflow-x: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.error-bar {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid #e5484d;
  border-radius: var(--radius-sm);
  background: rgba(229, 72, 77, 0.08);
  color: #e5484d;
  font-size: var(--font-size-xs);
  line-height: 1.5;
}
.channel-hint {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
}
.cancel-bar {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.warn-bar {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid #f5a623;
  border-radius: var(--radius-sm);
  background: rgba(245, 166, 35, 0.08);
  color: #b7791f;
  font-size: var(--font-size-xs);
}
.warn-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.warn-list {
  margin: 4px 0 0 20px;
  padding: 0;
  line-height: 1.6;
}
.extra-block {
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  overflow: hidden;
}
.extra-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  user-select: none;
}
.extra-head:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.extra-copy {
  margin-left: auto;
  color: var(--text-muted);
}
.sql-block {
  margin: 0;
  padding: 10px;
  background: #1e2329;
  color: #e6eaed;
  overflow-x: auto;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.cite-list {
  padding: 4px 10px 10px;
}
.cite-item {
  padding: 8px 0;
  border-top: 1px dashed var(--border);
}
.cite-item:first-child {
  border-top: none;
}
.cite-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-primary);
  margin-bottom: 4px;
}
.cite-id {
  font-size: 10px;
  color: var(--text-muted);
}
.cite-excerpt {
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  line-height: 1.6;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.bubble-actions {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}
.act {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.act:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.act:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: transparent;
  color: var(--text-muted);
}
.act.danger:hover {
  background: rgba(229, 72, 77, 0.1);
  color: #e5484d;
}

.write-bar {
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid rgba(230, 160, 60, 0.35);
  border-radius: 8px;
  background: rgba(230, 160, 60, 0.08);
}
.write-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.write-text { opacity: 0.85; }
.write-countdown { color: #d99a3c; }
.write-undo {
  margin-left: auto;
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid rgba(230, 160, 60, 0.55);
  background: transparent;
  color: #d99a3c;
  cursor: pointer;
  font-size: 12px;
}
.write-undo:disabled { opacity: 0.5; cursor: default; }
.write-error { margin-top: 4px; color: #d26b6b; font-size: 12px; }
</style>
