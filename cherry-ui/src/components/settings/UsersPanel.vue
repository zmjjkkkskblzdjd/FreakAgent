<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'
import { listUsers, updateUser, createUser, resetUserPassword } from '@/api/admin'

const auth = useAuthStore()
const canEditRole = computed(() => auth.isOwner)   
const canEditStatus = computed(() => auth.isDbAdmin || auth.isOwner)

const ROLE_OPTIONS = [
  { id: 'owner', name: '租户所有者' },
  { id: 'admin', name: '管理员' },
  { id: 'member', name: '成员' }
]
const SCOPE_OPTIONS = [
  { id: 'db_admin', name: '数据库管理', desc: '数据源 / 模型 / 镜像 / 用户与租户配置' },
  { id: 'kb_admin', name: '知识库管理', desc: '全部知识库可见 / 写 default / 分配授权' }
]
const ROLE_TEXT = Object.fromEntries(ROLE_OPTIONS.map((r) => [r.id, r.name]))

const LIMIT = 50
const users = ref([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const message = ref('')
const savingId = ref(0)
const draft = reactive({})       

function resetDraft() {
  for (const key of Object.keys(draft)) delete draft[key]
  for (const u of users.value) {
    draft[u.id] = { role: u.role, scopes: [...(u.scopes || [])], status: u.status }
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listUsers({ limit: LIMIT, offset: offset.value })
    users.value = data?.users || []
    total.value = Number(data?.total || users.value.length)
    resetDraft()
  } catch (e) {
    error.value = e?.message || '成员加载失败'
  } finally {
    loading.value = false
  }
}

function prev() {
  offset.value = Math.max(0, offset.value - LIMIT)
  load()
}

function next() {
  if (offset.value + LIMIT >= total.value) return
  offset.value += LIMIT
  load()
}

function dirty(u) {
  const d = draft[u.id]
  if (!d) return false
  if (Number(d.status) !== Number(u.status)) return true
  if (!canEditRole.value) return false
  if (d.role !== u.role) return true
  const before = [...(u.scopes || [])].sort().join(',')
  return [...d.scopes].sort().join(',') !== before
}

async function save(u) {
  const d = draft[u.id]
  if (!d || !dirty(u)) return
  const body = {}
  if (Number(d.status) !== Number(u.status)) body.status = Number(d.status)
  if (canEditRole.value) {
    if (d.role !== u.role) body.role = d.role
    const before = [...(u.scopes || [])].sort().join(',')
    if ([...d.scopes].sort().join(',') !== before) body.scopes = [...d.scopes]
  }
  savingId.value = u.id
  error.value = ''
  message.value = ''
  try {
    await updateUser(u.id, body)
    await load()
    message.value = `已更新 ${u.username}`
  } catch (e) {
    error.value = e?.message || '保存失败'
  } finally {
    savingId.value = 0
  }
}

function permHint(u) {
  const d = draft[u.id]
  if (!d) return ''
  const scopes = (u.role === 'owner' ? ['db_admin', 'kb_admin'] : d.scopes)
  if (!scopes.length) return '普通成员：仅默认知识库只读'
  return scopes.map((s) => SCOPE_OPTIONS.find((o) => o.id === s)?.name || s).join(' + ')
}


const createOpen = ref(false)
const createSaving = ref(false)
const createError = ref('')
const createForm = reactive({ email: '', username: '', password: '' })

function openCreate() {
  Object.assign(createForm, { email: '', username: '', password: '' })
  createError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  createError.value = ''
  if (!createForm.email.trim() || !createForm.username.trim()) {
    createError.value = '请填写邮箱与用户名'
    return
  }
  if (createForm.password.length < 8) {
    createError.value = '密码至少 8 位'
    return
  }
  createSaving.value = true
  try {
    const r = await createUser({
      email: createForm.email.trim(),
      username: createForm.username.trim(),
      password: createForm.password
    })
    createOpen.value = false
    message.value = `已创建成员 ${r?.user?.username || createForm.username}`
    await load()
  } catch (e) {
    createError.value = e?.message || '创建失败'
  } finally {
    createSaving.value = false
  }
}

onMounted(load)


const canResetPassword = computed(() => auth.isOwner)
const pwdOpen = ref(false)
const pwdTarget = ref(null)
const pwdSaving = ref(false)
const pwdError = ref('')
const pwdForm = reactive({ password: '' })
const pwdResult = ref('')          

function openPwd(u) {
  pwdTarget.value = u
  pwdForm.password = ''
  pwdError.value = ''
  pwdResult.value = ''
  pwdOpen.value = true
}

function closePwd() {
  if (pwdSaving.value) return
  pwdOpen.value = false
}

async function submitPwd() {
  pwdError.value = ''
  const pwd = pwdForm.password.trim()
  if (pwd && pwd.length < 8) {
    pwdError.value = '新密码至少 8 位'
    return
  }
  pwdSaving.value = true
  try {
    const r = await resetUserPassword(pwdTarget.value.id, pwd || null)
    message.value = `已重置 ${pwdTarget.value.username} 的密码`
    if (pwdTarget.value.id === auth.user?.id) {
      message.value += '（自己的密码已变更，请重新登录）'
    }
    if (r?.temp_password) {
      pwdResult.value = r.temp_password
    } else {
      pwdOpen.value = false
    }
    await load()
  } catch (e) {
    pwdError.value = e?.message || '重置失败'
  } finally {
    pwdSaving.value = false
  }
}
</script>

<template>
  <div class="users-panel">
    <div class="panel-head">
      <div>
        <h2 class="panel-title">用户与权限</h2>
        <p class="panel-desc text-muted">
          角色只表示层级；管理权按「管理域」单独授予——数据库管理与知识库管理彼此独立，
          一个人可以只有其中一种。租户所有者天然拥有全部管理域，也是唯一能分配权限的人。
        </p>
      </div>
      <div class="head-actions">
        <button v-if="canEditStatus" class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" /> 新建用户
        </button>
        <button class="btn btn-ghost" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" /> 刷新
        </button>
      </div>
    </div>

    <div v-if="!canEditRole" class="notice">
      <Icon name="eye" :size="14" />
      你可以启用/禁用成员，但角色与管理域只有租户所有者能调整。
    </div>
    <div v-if="error" class="error-line">{{ error }}</div>
    <div v-if="message" class="msg info">{{ message }}</div>

    <div v-if="loading" class="empty text-muted">加载中…</div>
    <div v-else-if="!users.length" class="empty text-muted">暂无成员。</div>
    <div v-else class="user-list">
      <div class="user-head">
        <span class="c-who">成员</span>
        <span class="c-role">角色</span>
        <span class="c-scopes">管理域</span>
        <span class="c-status">状态</span>
        <span class="c-last">最近登录</span>
        <span class="c-act"></span>
      </div>
      <div v-for="u in users" :key="u.id" class="user-row" :class="{ self: u.id === auth.user?.id }">
        <div class="c-who">
          <div class="name">
            {{ u.username }}
            <span v-if="u.id === auth.user?.id" class="chip sm">我</span>
          </div>
          <div class="sub text-muted">{{ u.email }}</div>
        </div>

        <div class="c-role">
          <select
            v-if="canEditRole && u.id !== auth.user?.id"
            class="input"
            v-model="draft[u.id].role"
          >
            <option v-for="r in ROLE_OPTIONS" :key="r.id" :value="r.id">{{ r.name }}</option>
          </select>
          <span v-else class="text-muted">{{ ROLE_TEXT[u.role] || u.role }}</span>
        </div>

        <div class="c-scopes">
          <template v-if="u.role === 'owner'">
            <span class="chip on">全域超管</span>
          </template>
          <template v-else-if="canEditRole">
            <label v-for="s in SCOPE_OPTIONS" :key="s.id" class="scope-pick" :title="s.desc">
              <input type="checkbox" :value="s.id" v-model="draft[u.id].scopes" />
              <span>{{ s.name }}</span>
            </label>
          </template>
          <template v-else>
            <span class="text-muted">{{ permHint(u) }}</span>
          </template>
        </div>

        <div class="c-status">
          <label v-if="canEditStatus && u.id !== auth.user?.id" class="switch">
            <input type="checkbox" :checked="!!Number(draft[u.id].status)"
                   @change="draft[u.id].status = $event.target.checked ? 1 : 0" />
            <span>{{ Number(draft[u.id].status) ? '启用' : '禁用' }}</span>
          </label>
          <span v-else class="chip" :class="Number(u.status) ? 'on' : 'off'">
            {{ Number(u.status) ? '启用' : '禁用' }}
          </span>
        </div>

        <div class="c-last text-muted">{{ u.last_login_at ? u.last_login_at.slice(0, 19).replace('T', ' ') : '—' }}</div>

        <div class="c-act">
          <button v-if="canResetPassword" class="btn btn-ghost btn-sm" @click="openPwd(u)">
            改密
          </button>
          <button
            v-if="canEditRole || canEditStatus"
            class="btn btn-primary btn-sm"
            :disabled="!dirty(u) || savingId === u.id"
            @click="save(u)"
          >
            <span v-if="savingId === u.id" class="spin"><Icon name="refresh" :size="12" /></span>
            <span v-else>保存</span>
          </button>
        </div>
      </div>
    </div>

    <div class="pager">
      <span class="hint text-muted">共 {{ total }} 人 · 第 {{ total ? offset + 1 : 0 }}-{{ Math.min(offset + LIMIT, total) }}</span>
      <div class="spacer" />
      <button class="btn btn-ghost btn-sm" :disabled="offset === 0 || loading" @click="prev">上一页</button>
      <button class="btn btn-ghost btn-sm" :disabled="offset + LIMIT >= total || loading" @click="next">下一页</button>
    </div>

    <div v-if="createOpen" class="dialog-mask" @click.self="createOpen = false">
      <div class="dialog">
        <h3 class="dialog-title">新建成员</h3>
        <p class="dialog-hint text-muted">
          账号会加入当前租户，角色固定为「成员」，不带任何管理域；加入后可在列表里再调整。
        </p>
        <label class="field">
          <span>邮箱</span>
          <input v-model="createForm.email" class="input" type="email"
                 autocomplete="off" placeholder="user@example.com" />
        </label>
        <label class="field">
          <span>用户名</span>
          <input v-model="createForm.username" class="input"
                 autocomplete="off" placeholder="2–32 位" />
        </label>
        <label class="field">
          <span>初始密码</span>
          <input v-model="createForm.password" class="input" type="password"
                 autocomplete="new-password"
                 placeholder="至少 8 位，字母/数字/符号两类以上" />
        </label>
        <p v-if="createError" class="error-line">{{ createError }}</p>
        <div class="dialog-act">
          <button class="btn btn-ghost" @click="createOpen = false">取消</button>
          <button class="btn btn-primary" :disabled="createSaving" @click="submitCreate">
            {{ createSaving ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>
    <div v-if="pwdOpen" class="dialog-mask" @click.self="closePwd">
      <div class="dialog">
        <h3 class="dialog-title">重置密码 · {{ pwdTarget?.username }}</h3>
        <p class="dialog-hint text-muted">
          仅租户所有者可操作。留空则由系统生成一次性临时密码；重置会立即让该成员已登录的会话失效。
        </p>
        <label class="field">
          <span>新密码（可留空自动生成）</span>
          <input v-model="pwdForm.password" class="input" type="password"
                 autocomplete="new-password"
                 placeholder="至少 8 位，字母/数字/符号两类以上" />
        </label>
        <p v-if="pwdError" class="error-line">{{ pwdError }}</p>
        <p v-if="pwdResult" class="msg info">
          临时密码：<code class="pw">{{ pwdResult }}</code>（只显示这一次，请立即转告，并让对方自行修改）
        </p>
        <div class="dialog-act">
          <button class="btn btn-ghost" :disabled="pwdSaving" @click="closePwd">取消</button>
          <button v-if="pwdResult" class="btn btn-primary" @click="pwdOpen = false">完成</button>
          <button v-else class="btn btn-primary" :disabled="pwdSaving" @click="submitPwd">
            {{ pwdSaving ? '重置中…' : '重置密码' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.users-panel {
  padding: 24px;
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.panel-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  margin: 0;
}
.panel-desc {
  font-size: var(--font-size-sm);
  margin-top: 4px;
  max-width: 620px;
}
.notice {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
}
.error-line {
  color: var(--danger);
  font-size: var(--font-size-xs);
  margin: 8px 0;
}
.msg.info {
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin: 8px 0;
}
.empty {
  padding: 32px 0;
  font-size: var(--font-size-sm);
}
.user-list {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.user-head,
.user-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.4fr) 130px minmax(200px, 1.6fr) 100px 150px 128px;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  font-size: var(--font-size-sm);
}
.user-head {
  background: var(--bg-elevated);
  color: var(--text-muted);
  font-size: var(--font-size-xs);
}
.user-row + .user-row {
  border-top: 1px solid var(--border);
}
.user-row:hover {
  background: var(--bg-hover);
}
.user-row.self .name {
  color: var(--accent);
}
.name {
  font-weight: 500;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sub,
.c-last {
  font-size: var(--font-size-xs);
}
.c-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.scope-pick {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  cursor: pointer;
}
.switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  cursor: pointer;
}
.chip {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-muted);
  border: 1px solid var(--border);
}
.chip.on {
  color: var(--success);
  border-color: var(--success);
}
.chip.off {
  color: var(--danger);
  border-color: var(--danger);
}
.chip.sm {
  font-size: 11px;
  padding: 1px 6px;
}
.input {
  height: 30px;
  padding: 0 8px;
}
.btn-sm {
  height: 28px;
  padding: 0 10px;
  font-size: var(--font-size-xs);
}
.c-act {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.pw {
  font-family: var(--font-mono, monospace);
  font-size: var(--font-size-sm);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--bg-base, #fff);
  border: 1px solid var(--border);
  user-select: all;
}
.pager {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}
.spacer {
  flex: 1;
}
.hint {
  font-size: var(--font-size-xs);
}
.spin {
  display: inline-flex;
  animation: spin 1s linear infinite;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.dialog-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.dialog {
  width: 100%;
  max-width: 420px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.dialog-title {
  margin: 0;
  font-size: var(--font-size-base);
  font-weight: 600;
}
.dialog-hint {
  margin: 0;
  font-size: var(--font-size-xs);
  line-height: 1.6;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.dialog-act {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
