<script setup>
import { computed, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'

const props = defineProps({
  interrupt: { type: Object, required: true },
  sending: { type: Boolean, default: false }
})
const emit = defineEmits(['resume'])

const schema = computed(() => props.interrupt?.schema || {})
const kind = computed(() => props.interrupt?.kind || '')
const laneName = computed(() => schema.value.lane_name || schema.value.lane || '')
const editQuestion = ref(schema.value.question || '')
const answer = ref('')


const OPERATION_TEXT = { insert: '插入', update: '更新', delete: '删除' }
const operationText = computed(() => OPERATION_TEXT[schema.value.operation] || schema.value.operation || '写入')
const targetText = computed(() =>
  [schema.value.database, schema.value.table].filter(Boolean).join('.'))
const channelText = computed(() => {
  const t = schema.value.target
  if (t === 'mysql') return 'MySQL'
  if (t === 'clickhouse') return 'ClickHouse'
  return t || ''
})
const hasPrecheck = computed(() =>
  !!schema.value.table || schema.value.preview === 'dry_run' || schema.value.preview === 'transactional')

const isTransactional = computed(() => schema.value.preview === 'transactional')
const previewRows = computed(() => schema.value.preview_rows || [])
const beforeRows = computed(() => schema.value.before_rows || [])
const previewCols = computed(() => {
  const keys = []
  for (const row of [...beforeRows.value, ...previewRows.value]) {
    for (const k of Object.keys(row || {})) {
      if (!keys.includes(k)) keys.push(k)
    }
  }
  return keys.slice(0, 6)
})
function cellText(v) {
  return v === null || v === undefined ? '—' : String(v)
}

function choose(action) {
  emit('resume', { type: 'clarify', kind: 'route', action })
}
function approveLane() {
  emit('resume', {
    type: 'lane_confirm',
    lane: props.interrupt.lane,
    lane_action: 'approve'
  })
}
function editLane() {
  emit('resume', {
    type: 'lane_confirm',
    lane: props.interrupt.lane,
    lane_action: 'edit',
    lane_question: editQuestion.value
  })
}
function rejectLane() {
  emit('resume', {
    type: 'lane_confirm',
    lane: props.interrupt.lane,
    lane_action: 'reject'
  })
}
function grantWrite(granted) {
  emit('resume', { type: 'approve_write', granted })
}
function submitContent() {
  if (!answer.value.trim()) return
  emit('resume', { type: 'clarify', kind: 'content', answer: answer.value.trim() })
}
</script>

<template>
  <div class="interrupt-card">
    <div class="ic-head">
      <Icon name="sparkles" :size="14" />
      <span>{{ kind === 'approve_write' ? '写入审批' : kind === 'route' ? '需要确认意图' : '需要确认' }}</span>
    </div>

    
    <template v-if="kind === 'route'">
      <div v-if="schema.question" class="ic-text">{{ schema.question }}</div>
      <div v-if="schema.rationale" class="ic-muted">{{ schema.rationale }}</div>
      <div class="ic-actions">
        <button
          v-for="opt in schema.options || []"
          :key="opt.action"
          class="ic-btn"
          :disabled="sending"
          @click="choose(opt.action)"
        >{{ opt.label }}</button>
      </div>
    </template>

    
    <template v-else-if="kind === 'lane_confirm'">
      <div class="ic-muted">车道：{{ laneName }}（{{ schema.index }}/{{ schema.total }}）</div>
      <div v-if="schema.original" class="ic-text">原问题：{{ schema.original }}</div>
      <div v-if="schema.missing" class="ic-muted">缺失：{{ schema.missing }}</div>
      <textarea v-model="editQuestion" class="ic-input" rows="2" placeholder="可修改后确认" />
      <div class="ic-actions">
        <button class="ic-btn primary" :disabled="sending" @click="approveLane">按<span class="ic-inline">此</span>确认</button>
        <button class="ic-btn" :disabled="sending" @click="editLane">改为上面答案</button>
        <button class="ic-btn" :disabled="sending" @click="rejectLane">跳过此车道</button>
      </div>
    </template>

    
    <template v-else-if="kind === 'approve_write'">
      <div class="ic-muted">
        {{ isTransactional
          ? '网关已在事务内真实执行预览并回滚（未落库）；批准后立即执行，1 小时内可撤回。'
          : '网关预检已完成（预估行数为权威复核值），批准后立即执行。' }}
      </div>
      <div v-if="hasPrecheck" class="ic-grid">
        <div v-if="targetText" class="ic-row">
          <span class="ic-label">目标</span>
          <span>{{ channelText ? channelText + ' · ' : '' }}{{ targetText }}</span>
        </div>
        <div class="ic-row">
          <span class="ic-label">操作</span>
          <span>{{ operationText }}</span>
        </div>
        <div v-if="schema.est_rows !== null && schema.est_rows !== undefined" class="ic-row">
          <span class="ic-label">预估影响</span>
          <span>{{ schema.est_rows }} 行</span>
        </div>
        <div v-if="isTransactional && schema.affected_rows" class="ic-row">
          <span class="ic-label">实际影响</span>
          <span>{{ schema.affected_rows }} 行（预览实测）</span>
        </div>
        <div v-if="schema.max_write_rows" class="ic-row">
          <span class="ic-label">单次上限</span>
          <span>{{ schema.max_write_rows }} 行（超出网关直接拒绝）</span>
        </div>
      </div>
      <pre v-if="schema.sql" class="ic-sql"><code>{{ schema.sql }}</code></pre>

      
      <template v-if="isTransactional">
        <div v-if="beforeRows.length" class="ic-preview">
          <div class="ic-preview-title">变更前（将恢复为这些值）</div>
          <table class="ic-table">
            <thead><tr><th v-for="c in previewCols" :key="c">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in beforeRows" :key="i">
                <td v-for="c in previewCols" :key="c">{{ cellText(row[c]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="previewRows.length" class="ic-preview">
          <div class="ic-preview-title">变更后（预览实际结果）</div>
          <table class="ic-table">
            <thead><tr><th v-for="c in previewCols" :key="c">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in previewRows" :key="i">
                <td v-for="c in previewCols" :key="c">{{ cellText(row[c]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="schema.preview_note" class="ic-note">{{ schema.preview_note }}</div>
      </template>

      <div class="ic-actions">
        <button class="ic-btn primary" :disabled="sending" @click="grantWrite(true)">批准执行</button>
        <button class="ic-btn" :disabled="sending" @click="grantWrite(false)">拒绝</button>
      </div>
    </template>

    
    <template v-else-if="kind === 'content'">
      <div v-if="schema.question" class="ic-text">{{ schema.question }}</div>
      <textarea v-model="answer" class="ic-input" rows="2" placeholder="请输入补充说明" />
      <div class="ic-actions">
        <button class="ic-btn primary" :disabled="sending || !answer.trim()" @click="submitContent">提交</button>
      </div>
    </template>

    <template v-else>
      <pre class="ic-sql"><code>{{ JSON.stringify(interrupt, null, 2) }}</code></pre>
    </template>
  </div>
</template>

<style scoped>
.interrupt-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}
.ic-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}
.ic-text {
  font-size: var(--font-size-sm);
  margin-bottom: 6px;
  white-space: pre-wrap;
}
.ic-muted {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  margin-bottom: 6px;
}
.ic-inline { display: none; }
.ic-grid {
  margin: 6px 0 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-base);
}
.ic-row {
  display: flex;
  gap: 8px;
  font-size: var(--font-size-xs);
  line-height: 1.8;
}
.ic-label {
  flex: 0 0 64px;
  color: var(--text-muted);
}
.ic-input {
  width: 100%;
  margin: 6px 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-base);
  color: var(--text-primary);
  font-size: var(--font-size-sm);
  resize: vertical;
}
.ic-preview { margin: 8px 0; }
.ic-preview-title {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  margin-bottom: 4px;
}
.ic-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-xs);
  background: var(--bg-base);
}
.ic-table th,
.ic-table td {
  border: 1px solid var(--border);
  padding: 4px 6px;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}
.ic-table th { color: var(--text-muted); font-weight: 600; }
.ic-note {
  margin-top: 6px;
  font-size: var(--font-size-xs);
  line-height: 1.6;
  color: var(--text-muted);
}
.ic-sql {
  margin: 6px 0;
  padding: 10px;
  background: #1e2329;
  color: #e6eaed;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.ic-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.ic-btn {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-base);
  color: var(--text-primary);
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.ic-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.ic-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.ic-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
