<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'
import {
  addGrants, createDataSource, deleteDataSource, getMirror, initDataSource,
  listDataSources, listDataSourceTables, listGrants, listMembers, removeGrants,
  resyncMirror, selectTables, startMirror, stopMirror, testDataSource, updateDataSource
} from '@/api/datasources'

const auth = useAuthStore()
const canWrite = computed(() => auth.isDbAdmin)

const MIRROR_STATUS_TEXT = {
  none: '未建立', stopped: '已停止', running: '同步中', failed: '故障'
}
const PHASE_TEXT = {
  pending: '待快照', snapshot: '快照中', cdc: '已同步', drift: '结构漂移',
  failed: '故障'
}
const mirrorStatusText = (s) => MIRROR_STATUS_TEXT[s] || s || '—'
const phaseText = (p) => PHASE_TEXT[p] || p || '—'

const items = ref([])
const loading = ref(false)
const listError = ref('')

const editing = ref(null)          
const form = reactive({
  name: '', db_type: 'mysql', host: '', port: 3306, database_name: '',
  schema_name: '', username: '', password: '', cdc_username: '', cdc_password: '',
  max_rows: 1000, query_timeout_sec: 30
})
const saving = ref(false)
const formError = ref('')
const formTest = ref('')           

const testingId = ref(0)
const testMsg = reactive({})       
const initingId = ref(0)
const initMsg = reactive({})       

const tablesFor = ref(null)
const tables = ref([])
const tablesLoading = ref(false)
const tablesError = ref('')

const selectedBefore = ref({})
const selectedDraft = reactive({})
const savingSelection = ref(false)
const selectMsg = ref('')


const mirrorFor = ref(null)
const mirror = ref(null)
const mirrorLoading = ref(false)
const mirrorError = ref('')
const mirrorBusy = ref('')


const selectedTableCount = computed(
  () => (mirror.value?.selected_tables?.length ?? 0)
    || (mirror.value?.tables?.length ?? 0)
)


const grantsFor = ref(null)
const grants = ref([])
const members = ref([])
const grantUserId = ref(0)
const grantDraft = reactive({})
const grantsLoading = ref(false)
const grantsError = ref('')
const grantsMsg = ref('')
const grantDraftCount = computed(
  () => Object.values(grantDraft).filter(Boolean).length
)

const DB_TYPES = [
  { id: 'mysql', name: 'MySQL' },
  { id: 'postgresql', name: 'PostgreSQL' }
]

async function load() {
  loading.value = true
  listError.value = ''
  try {
    const data = await listDataSources()
    items.value = data?.datasources || []
  } catch (e) {
    listError.value = e?.message || '数据源加载失败'
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    name: '', db_type: 'mysql', host: '', port: 3306, database_name: '',
    schema_name: '', username: '', password: '', cdc_username: '', cdc_password: '',
    max_rows: 1000, query_timeout_sec: 30
  })
  formError.value = ''
  formTest.value = ''
}

function openCreate() {
  resetForm()
  editing.value = {}
}

function openEdit(ds) {
  resetForm()
  Object.assign(form, {
    name: ds.name, db_type: ds.db_type, host: ds.host, port: ds.port,
    database_name: ds.database_name, schema_name: ds.schema_name || '',
    username: ds.username, password: '', cdc_username: ds.cdc_username || '',
    cdc_password: '', max_rows: ds.max_rows,
    query_timeout_sec: ds.query_timeout_sec
  })
  editing.value = {
    id: ds.id, has_password: ds.has_password,
    has_cdc_password: ds.has_cdc_password
  }
}

function closeForm() {
  editing.value = null
}

function onTypeChange() {
  const defaults = { mysql: 3306, postgresql: 5432 }
  if ([3306, 5432].includes(Number(form.port))) {
    form.port = defaults[form.db_type]
  }
}

function payload() {
  const body = {
    name: form.name.trim(),
    db_type: form.db_type,
    host: form.host.trim(),
    port: Number(form.port) || undefined,
    database_name: form.database_name.trim(),
    schema_name: form.schema_name.trim() || null,
    username: form.username.trim(),
    max_rows: Number(form.max_rows) || 1000,
    query_timeout_sec: Number(form.query_timeout_sec) || 30
  }
  if (form.password) body.password = form.password
  const cdcUser = form.cdc_username.trim()
  if (cdcUser) body.cdc_username = cdcUser
  else if (editing.value?.id) body.cdc_username = ''   
  if (form.cdc_password) body.cdc_password = form.cdc_password
  return body
}

async function save() {
  if (!form.name.trim() || !form.host.trim()
      || !form.database_name.trim() || !form.username.trim()) {
    formError.value = '名称、主机、数据库、用户名均为必填'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    if (editing.value?.id) {
      await updateDataSource(editing.value.id, payload())
    } else {
      await createDataSource(payload())
    }
    editing.value = null
    await load()
  } catch (e) {
    formError.value = e?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function testInForm() {
  if (!form.host.trim() || !form.database_name.trim() || !form.username.trim()) {
    formTest.value = '请先填写主机、数据库、用户名'
    return
  }
  formTest.value = '连接中…'
  try {
    const body = { ...payload() }
    if (editing.value?.id) body.id = editing.value.id
    const r = await testDataSource(body)
    formTest.value = r?.ok ? `连接成功（${r.latency_ms} ms）` : (r?.hint || '连接失败')
  } catch (e) {
    formTest.value = e?.message || '连接失败'
  }
}

async function runTest(ds) {
  testingId.value = ds.id
  testMsg[ds.id] = null
  try {
    const r = await testDataSource({ id: ds.id })
    testMsg[ds.id] = r?.ok
      ? { ok: true, text: `已连接 ${r.latency_ms} ms` }
      : { ok: false, text: r?.hint || '连接失败' }
  } catch (e) {
    testMsg[ds.id] = { ok: false, text: e?.message || '连接失败' }
  } finally {
    testingId.value = 0
  }
}

async function runInit(ds) {
  initingId.value = ds.id
  initMsg[ds.id] = '初始化中…'
  try {
    const r = await initDataSource(ds.id)
    if (r?.ok) {
      initMsg[ds.id] = `已同步 ${r.table_count} 张表`
      await load()
    } else {
      initMsg[ds.id] = r?.hint || '初始化失败'
    }
  } catch (e) {
    initMsg[ds.id] = e?.message || '初始化失败'
  } finally {
    initingId.value = 0
  }
}

async function openTables(ds) {
  tablesFor.value = ds
  tables.value = []
  tablesError.value = ''
  selectMsg.value = ''
  tablesLoading.value = true
  try {
    const r = await listDataSourceTables(ds.id)
    tables.value = r?.tables || []
    const before = {}
    for (const key of Object.keys(selectedDraft)) delete selectedDraft[key]
    for (const t of tables.value) {
      before[t.table_name] = !!t.selected
      selectedDraft[t.table_name] = !!t.selected
    }
    selectedBefore.value = before
  } catch (e) {
    tablesError.value = e?.message || '表结构加载失败'
  } finally {
    tablesLoading.value = false
  }
}

function closeTables() {
  tablesFor.value = null
}


async function saveSelection() {
  if (!tablesFor.value) return
  const add = []
  const drop = []
  for (const [name, on] of Object.entries(selectedDraft)) {
    const was = !!selectedBefore.value[name]
    if (on === was) continue
    ;(on ? add : drop).push(name)
  }
  if (!add.length && !drop.length) {
    selectMsg.value = '镜像范围无变化'
    return
  }
  savingSelection.value = true
  selectMsg.value = ''
  try {
    if (add.length) await selectTables(tablesFor.value.id, add, true)
    if (drop.length) await selectTables(tablesFor.value.id, drop, false)
    for (const name of add) selectedBefore.value[name] = true
    for (const name of drop) selectedBefore.value[name] = false
    selectMsg.value = `已更新镜像范围（新增 ${add.length} / 移除 ${drop.length}）`
    await load()
  } catch (e) {
    selectMsg.value = e?.message || '保存失败'
  } finally {
    savingSelection.value = false
  }
}



function fmtTs(sec) {
  if (!sec) return '—'
  return new Date(sec * 1000).toLocaleString()
}

function fmtLag(ms) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  return `${(ms / 60000).toFixed(1)} min`
}

async function loadMirror(ds) {
  mirrorLoading.value = true
  mirrorError.value = ''
  try {
    mirror.value = await getMirror(ds.id)
  } catch (e) {
    mirrorError.value = e?.message || '镜像状态加载失败'
  } finally {
    mirrorLoading.value = false
  }
}

async function openMirror(ds) {
  mirrorFor.value = ds
  mirror.value = null
  await loadMirror(ds)
  startMirrorPoll()
}

function closeMirror() {
  stopMirrorPoll()
  mirrorFor.value = null
}


let mirrorTimer = null
function stopMirrorPoll() {
  if (mirrorTimer) {
    clearInterval(mirrorTimer)
    mirrorTimer = null
  }
}
function startMirrorPoll() {
  stopMirrorPoll()
  mirrorTimer = setInterval(() => {
    if (!mirrorFor.value || mirrorLoading.value || mirrorBusy.value) return
    const allSynced = mirror.value?.status === 'running'
      && (mirror.value?.tables || []).every((t) => t.phase === 'cdc')
    if (allSynced) {
      stopMirrorPoll()
      return
    }
    loadMirror(mirrorFor.value)
  }, 10000)
}
onBeforeUnmount(stopMirrorPoll)

async function mirrorAction(kind) {
  if (!mirrorFor.value) return
  const verb = { start: '启动镜像', stop: '停止镜像', resync: '重建（重快照）' }[kind]
  if (kind !== 'start' && !window.confirm(`确认${verb}？`)) return
  mirrorBusy.value = kind
  mirrorError.value = ''
  try {
    const fn = { start: startMirror, stop: stopMirror, resync: resyncMirror }[kind]
    await fn(mirrorFor.value.id)
    await loadMirror(mirrorFor.value)
    await load()
    startMirrorPoll()
  } catch (e) {
    
    const probs = e?.detail?.problems
    mirrorError.value = Array.isArray(probs) && probs.length
      ? `${verb}失败：${probs.join('；')}`
      : (e?.message || `${verb}失败`)
  } finally {
    mirrorBusy.value = ''
  }
}



async function openGrants(ds) {
  grantsFor.value = ds
  grants.value = []
  grantsError.value = ''
  grantsMsg.value = ''
  grantsLoading.value = true
  try {
    const [g, m, t] = await Promise.all([
      listGrants(ds.id),
      listMembers(),
      listDataSourceTables(ds.id)
    ])
    grants.value = g?.grants || []
    
    members.value = (m?.users || []).filter(
      (u) => u.role === 'member' && Number(u.status) === 1
    )
    tables.value = t?.tables || []
    grantUserId.value = members.value[0]?.id || 0
    resetGrantDraft()
  } catch (e) {
    grantsError.value = e?.message || '授权信息加载失败'
  } finally {
    grantsLoading.value = false
  }
}

function resetGrantDraft() {
  for (const key of Object.keys(grantDraft)) delete grantDraft[key]
  for (const t of tables.value) {
    grantDraft[t.table_name] = !!(t.selected && grants.value.some(
      (g) => g.user_id === grantUserId.value && g.table_name === t.table_name))
  }
}

function onGrantUserChange() {
  resetGrantDraft()
  grantsMsg.value = ''
}

function closeGrants() {
  grantsFor.value = null
}

async function saveGrants() {
  if (!grantsFor.value || !grantUserId.value) return
  const add = []
  const drop = []
  for (const [name, on] of Object.entries(grantDraft)) {
    if (!tables.value.find((t) => t.table_name === name)?.selected) continue
    const had = grants.value.some(
      (g) => g.user_id === grantUserId.value && g.table_name === name)
    if (on && !had) add.push(name)
    if (!on && had) drop.push(name)
  }
  if (!add.length && !drop.length) {
    grantsMsg.value = '授权无变化'
    return
  }
  grantsLoading.value = true
  grantsMsg.value = ''
  try {
    if (add.length) await addGrants(grantsFor.value.id, grantUserId.value, add)
    if (drop.length) await removeGrants(grantsFor.value.id, grantUserId.value, drop)
    const r = await listGrants(grantsFor.value.id)
    grants.value = r?.grants || []
    resetGrantDraft()
    grantsMsg.value = `已更新授权（新增 ${add.length} / 撤销 ${drop.length}）`
  } catch (e) {
    grantsError.value = e?.message || '授权保存失败'
  } finally {
    grantsLoading.value = false
  }
}

async function remove(ds) {
  if (!window.confirm(`确认删除数据源「${ds.name}」？已同步的表结构会一并删除。`)) return
  try {
    await deleteDataSource(ds.id)
    await load()
  } catch (e) {
    listError.value = e?.message || '删除失败'
  }
}

function columnCount(raw) {
  try {
    return JSON.parse(raw || '{}').columns?.length || 0
  } catch {
    return 0
  }
}

onMounted(load)
</script>

<template>
  <div class="ds-panel">
    <div class="panel-head">
      <div>
        <h2 class="panel-title">数据源</h2>
        <p class="panel-desc text-muted">
          连接业务数据库，初始化表结构后供问数使用。密码加密存储，页面只显示是否已保存。
        </p>
      </div>
      <button v-if="canWrite" class="btn btn-primary" @click="openCreate">
        <Icon name="plus" :size="14" /> 新增数据源
      </button>
    </div>

    <div v-if="!canWrite" class="notice">
      <Icon name="eye" :size="14" /> 当前为成员账号，仅可查看数据源配置。
    </div>
    <div v-if="listError" class="error-line">{{ listError }}</div>

    <div v-if="loading" class="empty text-muted">加载中…</div>
    <div v-else-if="!items.length" class="empty text-muted">
      暂无数据源，点击「新增数据源」开始配置。
    </div>

    <div v-else class="ds-grid">
      <div v-for="ds in items" :key="ds.id" class="ds-card card">
        <div class="ds-head">
          <span class="ds-logo"><Icon name="database" :size="18" /></span>
          <div class="ds-meta">
            <div class="ds-name">{{ ds.name }}</div>
            <div class="ds-sub text-muted">{{ ds.db_type }} · {{ ds.host }}:{{ ds.port }}</div>
          </div>
          <span class="chip" :class="ds.enabled ? 'on' : ''">
            {{ ds.enabled ? '启用' : '停用' }}
          </span>
        </div>

        <div class="ds-rows">
          <div class="row"><span class="k">数据库</span><span class="v">{{ ds.database_name }}</span></div>
          <div v-if="ds.schema_name" class="row"><span class="k">Schema</span><span class="v">{{ ds.schema_name }}</span></div>
          <div class="row"><span class="k">用户名</span><span class="v">{{ ds.username }}</span></div>
          <div class="row">
            <span class="k">密码</span>
            <span class="v">{{ ds.has_password ? '已加密保存' : '未设置' }}</span>
          </div>
          <div class="row">
            <span class="k">限制</span>
            <span class="v">{{ ds.max_rows }} 行 / {{ ds.query_timeout_sec }}s</span>
          </div>
        </div>

        <div v-if="testMsg[ds.id]" class="msg" :class="testMsg[ds.id].ok ? 'ok' : 'bad'">
          {{ testMsg[ds.id].text }}
        </div>
        <div v-if="initMsg[ds.id]" class="msg info">{{ initMsg[ds.id] }}</div>

        <div class="ds-actions">
          <button class="btn btn-ghost btn-sm" @click="openTables(ds)">
            <Icon name="file" :size="13" /> 表结构
          </button>
          <button class="btn btn-ghost btn-sm" @click="openMirror(ds)">
            <Icon name="refresh" :size="13" /> 镜像
          </button>
          <button v-if="canWrite" class="btn btn-ghost btn-sm" @click="openGrants(ds)">
            <Icon name="user" :size="13" /> 授权
          </button>
          <button v-if="canWrite" class="btn btn-ghost btn-sm" :disabled="testingId === ds.id" @click="runTest(ds)">
            <span v-if="testingId === ds.id" class="spin"><Icon name="refresh" :size="13" /></span>
            <span v-else><Icon name="check" :size="13" /> 测试</span>
          </button>
          <button v-if="canWrite" class="btn btn-ghost btn-sm" :disabled="initingId === ds.id" @click="runInit(ds)">
            <span v-if="initingId === ds.id" class="spin"><Icon name="refresh" :size="13" /></span>
            <span v-else><Icon name="download" :size="13" /> 初始化</span>
          </button>
          <button v-if="canWrite" class="btn btn-ghost btn-sm" @click="openEdit(ds)">
            <Icon name="edit" :size="13" /> 编辑
          </button>
          <button v-if="canWrite" class="btn btn-text btn-sm danger" @click="remove(ds)">
            <Icon name="trash" :size="13" />
          </button>
        </div>
      </div>
    </div>

    
    <div v-if="editing" class="overlay" @click.self="closeForm">
      <div class="dialog card">
        <div class="dialog-head">
          <h3>{{ editing.id ? '编辑数据源' : '新增数据源' }}</h3>
          <button class="icon-btn" @click="closeForm"><Icon name="close" :size="16" /></button>
        </div>

        <div class="form-grid">
          <label class="field span2">
            <span class="label">名称</span>
            <input class="input" v-model="form.name" maxlength="64" placeholder="例如：销售业务库" />
          </label>
          <label class="field">
            <span class="label">类型</span>
            <select class="input" v-model="form.db_type" @change="onTypeChange">
              <option v-for="t in DB_TYPES" :key="t.id" :value="t.id">{{ t.name }}</option>
            </select>
          </label>
          <label class="field">
            <span class="label">端口</span>
            <input class="input" v-model.number="form.port" type="number" min="1" max="65535" />
          </label>
          <label class="field span2">
            <span class="label">主机</span>
            <input class="input" v-model="form.host" placeholder="IP 或域名" />
          </label>
          <label class="field">
            <span class="label">数据库</span>
            <input class="input" v-model="form.database_name" />
          </label>
          <label class="field">
            <span class="label">Schema（PostgreSQL）</span>
            <input class="input" v-model="form.schema_name" placeholder="默认 public" />
          </label>
          <label class="field">
            <span class="label">用户名</span>
            <input class="input" v-model="form.username" autocomplete="off" />
          </label>
          <label class="field">
            <span class="label">密码</span>
            <input
              class="input"
              type="password"
              v-model="form.password"
              autocomplete="new-password"
              :placeholder="editing.id && editing.has_password ? '已保存，留空不修改' : '连接密码'"
            />
          </label>
          <label class="field">
            <span class="label">CDC 用户名</span>
            <input
              class="input"
              v-model="form.cdc_username"
              autocomplete="off"
              placeholder="复制账号，如 debezium"
            />
          </label>
          <label class="field">
            <span class="label">CDC 密码</span>
            <input
              class="input"
              type="password"
              v-model="form.cdc_password"
              autocomplete="new-password"
              :placeholder="editing.id && editing.has_cdc_password ? '已保存，留空不修改' : '复制账号密码'"
            />
          </label>
          <p class="field span2 hint text-muted">
            CDC 账号仅用于启动镜像时交给 Debezium，不参与网关直读/直写；不填则无法启动镜像。
          </p>
          <label class="field">
            <span class="label">最大行数</span>
            <input class="input" v-model.number="form.max_rows" type="number" min="1" max="10000" />
          </label>
          <label class="field">
            <span class="label">查询超时（秒）</span>
            <input class="input" v-model.number="form.query_timeout_sec" type="number" min="1" max="300" />
          </label>
        </div>

        <div v-if="formError" class="error-line">{{ formError }}</div>
        <div v-if="formTest" class="msg info">{{ formTest }}</div>

        <div class="dialog-foot">
          <button class="btn btn-ghost" @click="testInForm">测试连接</button>
          <div class="spacer" />
          <button class="btn btn-ghost" @click="closeForm">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="save">
            <span v-if="saving" class="spin"><Icon name="refresh" :size="14" /></span>
            <span v-else>保存</span>
          </button>
        </div>
      </div>
    </div>

    
    <div v-if="tablesFor" class="overlay" @click.self="closeTables">
      <div class="dialog card wide">
        <div class="dialog-head">
          <h3>表结构 · {{ tablesFor.name }}</h3>
          <button class="icon-btn" @click="closeTables"><Icon name="close" :size="16" /></button>
        </div>
        <p v-if="canWrite" class="hint text-muted">
          勾选表示纳入 CDC 镜像范围；保存后需在「镜像」里启动同步。
        </p>
        <div v-if="tablesLoading" class="empty text-muted">加载中…</div>
        <div v-else-if="tablesError" class="error-line">{{ tablesError }}</div>
        <div v-else-if="!tables.length" class="empty text-muted">
          还未初始化，点击卡片上的「初始化」同步元数据。
        </div>
        <div v-else class="table-list scroll">
          <div v-for="t in tables" :key="t.id" class="table-row">
            <input
              v-if="canWrite"
              type="checkbox"
              class="pick"
              v-model="selectedDraft[t.table_name]"
            />
            <Icon name="file" :size="14" />
            <span class="t-name">{{ t.table_name }}</span>
            <span class="t-comment text-muted">{{ t.table_comment || '' }}</span>
            <span class="t-cols text-muted">{{ columnCount(t.columns_json) }} 列</span>
          </div>
        </div>
        <div v-if="canWrite && tables.length" class="dialog-foot">
          <span class="hint text-muted">
            已选 {{ Object.values(selectedDraft).filter(Boolean).length }} / {{ tables.length }} 张
          </span>
          <span v-if="selectMsg" class="msg info">{{ selectMsg }}</span>
          <div class="spacer" />
          <button class="btn btn-ghost" @click="closeTables">关闭</button>
          <button class="btn btn-primary" :disabled="savingSelection" @click="saveSelection">
            <span v-if="savingSelection" class="spin"><Icon name="refresh" :size="14" /></span>
            <span v-else>保存镜像范围</span>
          </button>
        </div>
      </div>
    </div>

    
    <div v-if="mirrorFor" class="overlay" @click.self="closeMirror">
      <div class="dialog card wide">
        <div class="dialog-head">
          <h3>镜像状态 · {{ mirrorFor.name }}</h3>
          <button class="icon-btn" @click="closeMirror"><Icon name="close" :size="16" /></button>
        </div>

        <div v-if="mirrorLoading" class="empty text-muted">加载中…</div>
        <div v-else-if="mirrorError" class="error-line">{{ mirrorError }}</div>
        <template v-else-if="mirror">
          <div class="mirror-head">
            <span class="chip" :class="'st-' + (mirror.status || 'none')">
              {{ mirrorStatusText(mirror.status) }}
            </span>
            <span v-if="mirror.connector" class="hint text-muted">
              connector {{ mirror.connector }} · 代号 {{ mirror.generation || 1 }}
            </span>
          </div>

          <div v-if="canWrite && !mirrorFor.cdc_username" class="msg bad">
            该数据源还没有 CDC 复制账号，启动镜像会被拒绝：请先在「编辑」里填写 CDC 用户名与密码。
          </div>

          <div class="ds-rows">
            <div class="row"><span class="k">延迟</span><span class="v">{{ fmtLag(mirror.lag_ms) }}</span></div>
            <div class="row"><span class="k">快照</span><span class="v">{{ fmtTs(mirror.snapshot_at) }}</span></div>
            <div class="row"><span class="k">最近事件</span><span class="v">{{ fmtTs(mirror.last_event_at) }}</span></div>
          </div>
          <div v-if="mirror.error" class="msg bad">{{ mirror.error }}</div>

          <div v-if="!mirror.tables?.length" class="empty text-muted">
            <span v-if="selectedTableCount">
              已选中 {{ selectedTableCount }} 张表，尚未启动镜像。
            </span>
            <span v-else>还没有选中任何表：先在「表结构」里勾选并保存镜像范围。</span>
          </div>
          <div v-else class="table-list scroll">
            <div v-for="t in mirror.tables" :key="t.name" class="table-row">
              <Icon name="file" :size="14" />
              <span class="t-name">{{ t.name }}</span>
              <span class="chip sm" :class="'ph-' + (t.phase || 'pending')">
                {{ phaseText(t.phase) }}
              </span>
              <span class="spacer" />
              <span class="t-cols text-muted">{{ t.rows ?? '—' }} 行</span>
              <span class="t-cols text-muted">{{ fmtTs(t.last_event_at) }}</span>
            </div>
          </div>

          <div class="dialog-foot">
            <button class="btn btn-ghost" :disabled="mirrorLoading" @click="loadMirror(mirrorFor)">
              刷新
            </button>
            <div class="spacer" />
            <template v-if="canWrite">
              <button
                v-if="mirror.status === 'running'"
                class="btn btn-ghost danger"
                :disabled="!!mirrorBusy"
                @click="mirrorAction('stop')"
              >
                <span v-if="mirrorBusy === 'stop'" class="spin"><Icon name="refresh" :size="14" /></span>
                <span v-else>停止</span>
              </button>
              <button
                v-else
                class="btn btn-primary"
                :disabled="!!mirrorBusy || !selectedTableCount"
                @click="mirrorAction('start')"
              >
                <span v-if="mirrorBusy === 'start'" class="spin"><Icon name="refresh" :size="14" /></span>
                <span v-else>启动镜像</span>
              </button>
              <button
                class="btn btn-ghost"
                :disabled="!!mirrorBusy || !selectedTableCount"
                @click="mirrorAction('resync')"
              >
                <span v-if="mirrorBusy === 'resync'" class="spin"><Icon name="refresh" :size="14" /></span>
                <span v-else>重建（重快照）</span>
              </button>
            </template>
            <button v-else class="btn btn-ghost" @click="closeMirror">关闭</button>
          </div>
        </template>
      </div>
    </div>

    
    <div v-if="grantsFor" class="overlay" @click.self="closeGrants">
      <div class="dialog card wide">
        <div class="dialog-head">
          <h3>按表写授权 · {{ grantsFor.name }}</h3>
          <button class="icon-btn" @click="closeGrants"><Icon name="close" :size="16" /></button>
        </div>

        <div v-if="grantsLoading" class="empty text-muted">加载中…</div>
        <div v-else-if="grantsError" class="error-line">{{ grantsError }}</div>
        <template v-else>
          <div v-if="!members.length" class="empty text-muted">
            本租户还没有可授权的成员账号（owner/admin 无需授权）。
          </div>
          <template v-else>
            <label class="field">
              <span class="label">成员</span>
              <select class="input" v-model.number="grantUserId" @change="onGrantUserChange">
                <option v-for="m in members" :key="m.id" :value="m.id">
                  {{ m.username }}（{{ m.email }}）
                </option>
              </select>
            </label>
            <p class="hint text-muted">
              只能勾选已纳入镜像范围的表；未勾选保存即撤销该表权限。
            </p>
            <div v-if="!tables.length" class="empty text-muted">还没有可授权的表。</div>
            <div v-else class="table-list scroll">
              <div
                v-for="t in tables"
                :key="t.id"
                class="table-row"
                :class="{ disabled: !t.selected }"
              >
                <input
                  type="checkbox"
                  class="pick"
                  :disabled="!t.selected"
                  v-model="grantDraft[t.table_name]"
                />
                <Icon name="file" :size="14" />
                <span class="t-name">{{ t.table_name }}</span>
                <span v-if="!t.selected" class="chip sm">未镜像</span>
                <span class="t-comment text-muted">{{ t.table_comment || '' }}</span>
                <span class="t-cols text-muted">{{ columnCount(t.columns_json) }} 列</span>
              </div>
            </div>
            <div class="dialog-foot">
              <span class="hint text-muted">
                已授权 {{ grantDraftCount }} / {{ tables.filter((t) => t.selected).length }} 张
              </span>
              <span v-if="grantsMsg" class="msg info">{{ grantsMsg }}</span>
              <div class="spacer" />
              <button class="btn btn-ghost" @click="closeGrants">取消</button>
              <button class="btn btn-primary" :disabled="!grantUserId" @click="saveGrants">
                保存授权
              </button>
            </div>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ds-panel {
  padding: 20px 24px 40px;
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
  max-width: 560px;
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
.empty {
  padding: 32px 0;
  font-size: var(--font-size-sm);
}
.ds-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 14px;
}
.ds-card {
  padding: 16px;
  display: flex;
  flex-direction: column;
}
.ds-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ds-logo {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-soft);
  color: var(--accent);
  flex-shrink: 0;
}
.ds-meta {
  flex: 1;
  min-width: 0;
}
.ds-name {
  font-weight: 600;
  font-size: var(--font-size-base);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ds-sub {
  font-size: var(--font-size-xs);
  margin-top: 2px;
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
.chip.sm {
  font-size: 11px;
  padding: 1px 6px;
}
.chip.st-running,
.chip.ph-cdc {
  color: var(--success);
  border-color: var(--success);
}
.chip.st-failed,
.chip.ph-failed,
.chip.ph-drift {
  color: var(--danger);
  border-color: var(--danger);
}
.chip.ph-pending,
.chip.ph-snapshot {
  color: var(--warning);
  border-color: var(--warning);
}
.hint {
  font-size: var(--font-size-xs);
}
.mirror-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.pick {
  width: 14px;
  height: 14px;
  accent-color: var(--accent);
  cursor: pointer;
  flex-shrink: 0;
}
.table-row.disabled {
  opacity: 0.45;
}
.ds-rows {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.row {
  display: flex;
  gap: 12px;
  font-size: var(--font-size-xs);
}
.k {
  width: 64px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.v {
  color: var(--text-secondary);
  word-break: break-all;
}
.msg {
  margin-top: 8px;
  font-size: var(--font-size-xs);
}
.msg.ok {
  color: var(--success);
}
.msg.bad {
  color: var(--danger);
}
.msg.info {
  color: var(--text-secondary);
}
.ds-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.btn-sm {
  height: 28px;
  padding: 0 10px;
  font-size: var(--font-size-xs);
}
.danger {
  color: var(--danger);
}
.danger:hover {
  color: var(--danger);
  background: var(--bg-hover);
}
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
  width: 760px;
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
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 12px;
  overflow-y: auto;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field.span2 {
  grid-column: span 2;
}
.label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
select.input {
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
.table-list {
  max-height: 380px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.table-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.table-row:hover {
  background: var(--bg-hover);
}
.t-name {
  font-weight: 500;
  color: var(--text-primary);
}
.t-comment {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-xs);
}
.t-cols {
  font-size: var(--font-size-xs);
}
.spin {
  display: inline-flex;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
