<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import {
  listKbGrants, listKbMembers, revokeKbGrant, setKbGrant
} from '@/api/kb'

const props = defineProps({
  kb: { type: Object, required: true }
})
const emit = defineEmits(['close'])

const PERM_TEXT = { '': '不可见', read: '只读', write: '可写' }
const ROLE_TEXT = { owner: '创建者', admin: '管理员', member: '成员' }

const members = ref([])
const creatorId = ref(0)
const grants = ref([])            
const draft = reactive({})        
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const message = ref('')

const creatorName = computed(() => {
  const c = members.value.find((m) => m.id === creatorId.value)
  return c ? `${c.username}（${c.email}）` : `#${creatorId.value}`
})


const rows = computed(() => members.value.filter((m) => m.id !== creatorId.value))

const changedCount = computed(() => rows.value.filter(
  (m) => (draft[m.id] || '') !== permOf(m.id)).length)

function permOf(userId) {
  return grants.value.find((g) => Number(g.user_id) === Number(userId))?.perm || ''
}

function resetDraft() {
  for (const key of Object.keys(draft)) delete draft[key]
  for (const m of rows.value) draft[m.id] = permOf(m.id)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [g, m] = await Promise.all([listKbGrants(props.kb.id), listKbMembers()])
    grants.value = g?.grants || []
    creatorId.value = Number(g?.creator || 0)
    members.value = m?.users || []
    resetDraft()
  } catch (e) {
    error.value = e?.message || '授权信息加载失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  const set = []
  const drop = []
  for (const m of rows.value) {
    const before = permOf(m.id)
    const after = draft[m.id] || ''
    if (before === after) continue
    if (after) set.push({ userId: m.id, perm: after })
    else drop.push(m.id)
  }
  if (!set.length && !drop.length) {
    message.value = '授权无变化'
    return
  }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    for (const s of set) await setKbGrant(props.kb.id, s.userId, s.perm)
    for (const uid of drop) await revokeKbGrant(props.kb.id, uid)
    const g = await listKbGrants(props.kb.id)
    grants.value = g?.grants || []
    resetDraft()
    message.value = `已更新授权（新增/修改 ${set.length} / 撤销 ${drop.length}）`
  } catch (e) {
    error.value = e?.message || '授权保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog card wide">
      <div class="dialog-head">
        <h3>知识库授权 · {{ kb.name }}</h3>
        <button class="icon-btn" @click="emit('close')"><Icon name="close" :size="16" /></button>
      </div>

      <div v-if="loading" class="empty text-muted">加载中…</div>
      <div v-else-if="error" class="error-line">{{ error }}</div>
      <template v-else>
        <p class="hint text-muted">
          创建者：{{ creatorName }}（天然可写）。只读 = 可检索/查看；可写 = 另可上传、删文档、改名。
          删除知识库仅创建者或知识库管理员可操作。
        </p>

        <div v-if="!rows.length" class="empty text-muted">本租户还没有其他可授权成员。</div>
        <div v-else class="grant-list scroll">
          <div v-for="m in rows" :key="m.id" class="grant-row">
            <Icon name="user" :size="14" />
            <div class="who">
              <div class="name">{{ m.username }}</div>
              <div class="sub text-muted">{{ m.email }}</div>
            </div>
            <span class="chip sm">{{ ROLE_TEXT[m.role] || m.role }}</span>
            <select class="input perm-select" v-model="draft[m.id]">
              <option value="">{{ PERM_TEXT[''] }}</option>
              <option value="read">{{ PERM_TEXT.read }}</option>
              <option value="write">{{ PERM_TEXT.write }}</option>
            </select>
          </div>
        </div>

        <div class="dialog-foot">
          <span class="hint text-muted">待变更 {{ changedCount }} 个成员</span>
          <span v-if="message" class="msg info">{{ message }}</span>
          <div class="spacer" />
          <button class="btn btn-ghost" @click="emit('close')">关闭</button>
          <button class="btn btn-primary" :disabled="saving || !changedCount" @click="save">
            <span v-if="saving" class="spin"><Icon name="refresh" :size="13" /></span>
            <span v-else>保存授权</span>
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}
.dialog {
  width: 640px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 80px);
  background: var(--bg-base);
  display: flex;
  flex-direction: column;
  padding: 16px 18px;
}
.dialog.wide {
  width: 720px;
}
.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.dialog-head h3 {
  margin: 0;
  font-size: var(--font-size-base);
  font-weight: 600;
}
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
}
.icon-btn:hover {
  background: var(--bg-hover);
}
.hint {
  font-size: var(--font-size-xs);
  margin: 0 0 10px;
}
.error-line {
  color: var(--danger);
  font-size: var(--font-size-xs);
  margin: 8px 0;
}
.empty {
  padding: 32px 0;
  font-size: var(--font-size-sm);
}
.grant-list {
  max-height: 380px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.grant-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.grant-row:hover {
  background: var(--bg-hover);
}
.who {
  flex: 1;
  min-width: 0;
}
.name {
  color: var(--text-primary);
  font-weight: 500;
}
.sub {
  font-size: var(--font-size-xs);
}
.chip {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-muted);
  border: 1px solid var(--border);
}
.chip.sm {
  font-size: 11px;
  padding: 1px 6px;
}
.perm-select {
  width: 110px;
  height: 30px;
  appearance: none;
}
.dialog-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}
.spacer {
  flex: 1;
}
.msg.info {
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
}
.spin {
  display: inline-flex;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
