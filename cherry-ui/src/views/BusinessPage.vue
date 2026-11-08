<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'
import {
  createBusinessTerm, deleteBusinessTerm, listBusinessTerms,
  setBusinessRecall, syncBusinessTerms, updateBusinessTerm
} from '@/api/business'

const auth = useAuthStore()
const isDbAdmin = computed(() => auth.isDbAdmin)

const canCreate = computed(() => auth.isLoggedIn)
const showActions = computed(() => items.value.some((t) => t.can_edit))
const ownerText = (t) => {
  if (t.is_public) return '公共'
  return t.user_id === auth.user?.id ? '我的' : '—'
}

const items = ref([])
const loading = ref(false)
const errorMsg = ref('')
const notice = ref('')
const noticeOk = ref(true)
const keyword = ref('')

const dialogOpen = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  term: '', description: '', synonyms: '',
  formula: '', related_tables: '', unit: '', is_recall: true, is_public: false
})

const syncing = ref(false)
const busyId = ref(0)

const STATUS_LABEL = {
  COMPLETED: '已完成', FAILED: '失败', PROCESSING: '处理中', PENDING: '等待中'
}
const statusText = (s) => STATUS_LABEL[s] || '未知'

function flash(text, ok = true) {
  notice.value = text
  noticeOk.value = ok
  window.setTimeout(() => { notice.value = '' }, 3600)
}

function fmtTime(sec) {
  if (!sec) return '—'
  return new Date(sec * 1000).toLocaleString()
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const r = await listBusinessTerms(keyword.value.trim())
    items.value = r?.terms || []
  } catch (e) {
    errorMsg.value = e?.message || '业务术语加载失败'
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    term: '', description: '', synonyms: '',
    formula: '', related_tables: '', unit: '', is_recall: true, is_public: false
  })
  formError.value = ''
}

function openCreate() {
  resetForm()
  editingId.value = null
  dialogOpen.value = true
}

function openEdit(t) {
  resetForm()
  Object.assign(form, {
    term: t.term,
    description: t.description || '',
    synonyms: t.synonyms || '',
    formula: t.formula || '',
    related_tables: t.related_tables || '',
    unit: t.unit || '',
    is_recall: !!t.is_recall
  })
  editingId.value = t.id
  dialogOpen.value = true
}

function closeDialog() {
  dialogOpen.value = false
}

async function save() {
  if (!form.term.trim()) {
    formError.value = '业务名词不能为空'
    return
  }
  saving.value = true
  formError.value = ''
  const body = {
    term: form.term.trim(),
    description: form.description.trim(),
    synonyms: form.synonyms.trim(),
    formula: form.formula.trim(),
    related_tables: form.related_tables.trim(),
    unit: form.unit.trim(),
    is_recall: !!form.is_recall,
    is_public: !!(form.is_public && isDbAdmin.value && !editingId.value)
  }
  try {
    const r = editingId.value
      ? await updateBusinessTerm(editingId.value, body)
      : await createBusinessTerm(body)
    dialogOpen.value = false
    await load()
    if (r?.sync && r.sync.ok === false) {
      flash(r.sync.hint || '已保存，但向量同步失败', false)
    } else {
      flash(editingId.value ? '更新成功' : '创建成功')
    }
  } catch (e) {
    formError.value = e?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function toggleRecall(t) {
  busyId.value = t.id
  try {
    const r = await setBusinessRecall(t.id, !t.is_recall)
    await load()
    flash(r?.sync?.ok === false
      ? (r.sync.hint || '向量同步失败')
      : (t.is_recall ? '已取消召回' : '已设为召回'), r?.sync?.ok !== false)
  } catch (e) {
    flash(e?.message || '操作失败', false)
  } finally {
    busyId.value = 0
  }
}

async function retryEmbedding(t) {
  busyId.value = t.id
  try {
    const r = await syncBusinessTerms()
    await load()
    flash(r?.ok ? `已同步 ${r.count} 条术语到向量库` : (r?.hint || '同步失败'), !!r?.ok)
  } catch (e) {
    flash(e?.message || '同步失败', false)
  } finally {
    busyId.value = 0
  }
}

async function remove(t) {
  if (!window.confirm(`确认删除业务术语「${t.term}」？此操作不可恢复。`)) return
  busyId.value = t.id
  try {
    await deleteBusinessTerm(t.id)
    await load()
    flash('删除成功')
  } catch (e) {
    flash(e?.message || '删除失败', false)
  } finally {
    busyId.value = 0
  }
}

async function syncAll() {
  if (!window.confirm('将清空并重新灌入本租户全部业务术语向量，确认同步？')) return
  syncing.value = true
  try {
    const r = await syncBusinessTerms()
    await load()
    flash(r?.ok ? `已同步 ${r.count} 条术语到向量库` : (r?.hint || '同步失败'), !!r?.ok)
  } catch (e) {
    flash(e?.message || '同步失败', false)
  } finally {
    syncing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="biz-page scroll">
    <div class="panel-head">
      <div>
        <h2 class="panel-title">业务库</h2>
        <p class="panel-desc text-muted">
          维护业务术语、口径与同义词。你自己的术语只对你生效；租户公共术语全员可见、由管理员维护，
          召回的条目会进入向量库，供问数与分析时统一语义。
        </p>
      </div>
      <div class="head-actions">
        <button class="btn btn-ghost" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" /> 刷新
        </button>
        <button v-if="isDbAdmin" class="btn btn-ghost" :disabled="syncing" @click="syncAll">
          <span v-if="syncing" class="spin"><Icon name="refresh" :size="14" /></span>
          <span v-else><Icon name="upload" :size="14" /> 同步到向量库</span>
        </button>
        <button v-if="canCreate" class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" /> 添加业务知识
        </button>
      </div>
    </div>

    <div v-if="!isDbAdmin" class="notice">
      <Icon name="eye" :size="14" /> 你可以维护自己的术语；租户公共术语由管理员维护，仅可查看。
    </div>
    <div v-if="errorMsg" class="error-line">{{ errorMsg }}</div>
    <div v-if="notice" class="msg" :class="noticeOk ? 'ok' : 'bad'">{{ notice }}</div>

    <div class="toolbar card">
      <div class="search-box">
        <Icon name="search" :size="15" />
        <input
          v-model="keyword"
          class="search-input"
          placeholder="搜索业务名词、描述或同义词…"
          @keyup.enter="load"
        />
        <button v-if="keyword" class="clear-btn" @click="keyword = ''; load()">
          <Icon name="close" :size="13" />
        </button>
      </div>
      <span class="count text-muted">{{ loading ? '加载中…' : `共 ${items.length} 条` }}</span>
    </div>

    <div class="table-card card">
      <table class="biz-table">
        <thead>
          <tr>
            <th style="width: 64px">ID</th>
            <th style="width: 70px">归属</th>
            <th style="width: 16%">业务名词</th>
            <th style="width: 24%">描述</th>
            <th style="width: 17%">同义词</th>
            <th style="width: 110px">向量化状态</th>
            <th style="width: 96px">召回</th>
            <th style="width: 150px">更新时间</th>
            <th v-if="showActions" style="width: 140px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in items" :key="t.id">
            <td class="text-muted">{{ t.id }}</td>
            <td>
              <span class="chip" :class="t.is_public ? 'recall-on' : ''">{{ ownerText(t) }}</span>
            </td>
            <td class="term" :title="t.term">
              {{ t.term }}<span v-if="t.unit" class="unit">{{ t.unit }}</span>
            </td>
            <td>
              <div class="ellipsis" :title="t.description">{{ t.description || '—' }}</div>
              <div v-if="t.formula" class="cell-sub ellipsis" :title="`口径：${t.formula}`">
                口径：{{ t.formula }}
              </div>
            </td>
            <td class="ellipsis" :title="t.synonyms">{{ t.synonyms || '—' }}</td>
            <td>
              <span class="chip" :class="'st-' + (t.embedding_status || 'PENDING')">
                {{ statusText(t.embedding_status) }}
              </span>
              <span
                v-if="t.embedding_status === 'FAILED' && t.error_msg"
                class="err-hint"
                :title="t.error_msg"
              >!</span>
            </td>
            <td>
              <span class="chip" :class="t.is_recall ? 'recall-on' : ''">
                {{ t.is_recall ? '召回中' : '未召回' }}
              </span>
            </td>
            <td class="text-muted">{{ fmtTime(t.updated_at) }}</td>
            <td v-if="showActions">
              <div v-if="t.can_edit" class="row-actions">
                <button class="act-btn" title="编辑" @click="openEdit(t)">
                  <Icon name="edit" :size="14" />
                </button>
                <button
                  v-if="t.embedding_status === 'FAILED'"
                  class="act-btn warn"
                  title="重试向量化"
                  :disabled="busyId === t.id"
                  @click="retryEmbedding(t)"
                >
                  <Icon name="refresh" :size="14" />
                </button>
                <button
                  class="act-btn"
                  :title="t.is_recall ? '取消召回' : '设为召回'"
                  :disabled="busyId === t.id"
                  @click="toggleRecall(t)"
                >
                  <Icon :name="t.is_recall ? 'eye' : 'check'" :size="14" />
                </button>
                <button class="act-btn danger" title="删除" :disabled="busyId === t.id" @click="remove(t)">
                  <Icon name="trash" :size="14" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="!loading && !items.length" class="empty">
        <div class="empty-icon"><Icon name="tag" :size="44" /></div>
        <p class="empty-title">暂无业务知识</p>
        <p class="empty-desc text-muted">点击「添加业务知识」配置你自己的业务术语</p>
        <button v-if="canCreate" class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" /> 添加业务知识
        </button>
      </div>
    </div>

    <div v-if="dialogOpen" class="overlay" @click.self="closeDialog">
      <div class="dialog card">
        <div class="dialog-head">
          <h3>{{ editingId ? '编辑业务知识' : '添加业务知识' }}</h3>
          <button class="icon-btn" @click="closeDialog"><Icon name="close" :size="16" /></button>
        </div>

        <div class="form">
          <label class="field">
            <span class="label">业务名词 <span class="req">*</span></span>
            <input class="input" v-model="form.term" maxlength="128"
                   placeholder="例如：月活用户、GMV" />
          </label>
          <label class="field">
            <span class="label">描述 <span class="req">*</span></span>
            <textarea class="input textarea" v-model="form.description" rows="3"
                      placeholder="说明该术语的含义、统计口径与用法"></textarea>
          </label>
          <label class="field">
            <span class="label">同义词</span>
            <textarea class="input textarea" v-model="form.synonyms" rows="2"
                      placeholder="多个同义词用逗号分隔，例如：MAU, 月活, 月活跃用户数"></textarea>
          </label>
          <label class="field">
            <span class="label">业务口径</span>
            <textarea class="input textarea" v-model="form.formula" rows="2"
                      placeholder="SQL 表达式或统计口径，例如：SUM(pay_amount)/10000"></textarea>
          </label>
          <div class="field-row">
            <label class="field">
              <span class="label">相关表</span>
              <input class="input" v-model="form.related_tables"
                     placeholder="逗号分隔，例如：dwd_order, dim_shop" />
            </label>
            <label class="field field-unit">
              <span class="label">单位</span>
              <input class="input" v-model="form.unit" placeholder="元 / 万元 / %" />
            </label>
          </div>
          <label class="check-row">
            <input type="checkbox" v-model="form.is_recall" />
            <span>加入召回（同步后进入向量库）</span>
          </label>
          <label v-if="isDbAdmin && !editingId" class="check-row">
            <input type="checkbox" v-model="form.is_public" />
            <span>设为租户公共术语（全员可见，仅管理员可改；不勾选只对你生效）</span>
          </label>
        </div>

        <div v-if="formError" class="error-line">{{ formError }}</div>

        <div class="dialog-foot">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="closeDialog">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="save">
            <span v-if="saving" class="spin"><Icon name="refresh" :size="14" /></span>
            <span v-else>{{ editingId ? '保存更新' : '立即创建' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.biz-page {
  height: 100%;
  overflow-y: auto;
  padding: 20px 24px 40px;
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
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
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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
.msg {
  font-size: var(--font-size-xs);
  margin-bottom: 10px;
}
.msg.ok { color: var(--success); }
.msg.bad { color: var(--danger); }

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  max-width: 420px;
  padding: 0 10px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-base);
  color: var(--text-muted);
}
.search-input {
  flex: 1;
  height: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--font-size-sm);
}
.clear-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: var(--text-muted);
  background: none;
}
.clear-btn:hover { background: var(--bg-hover); }
.count {
  font-size: var(--font-size-xs);
  flex-shrink: 0;
}

.table-card {
  padding: 0;
  overflow: hidden;
}
.biz-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.biz-table th,
.biz-table td {
  padding: 10px 12px;
  text-align: left;
  font-size: var(--font-size-sm);
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.biz-table th {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-elevated);
  white-space: nowrap;
}
.biz-table tbody tr:hover {
  background: var(--bg-hover);
}
.biz-table tbody tr:last-child td {
  border-bottom: 0;
}
.term {
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}
.cell-sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-muted);
}
.unit {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
}
.chip {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  background: var(--bg-elevated);
  white-space: nowrap;
}
.chip.st-COMPLETED,
.chip.recall-on {
  color: var(--success);
  border-color: var(--success);
}
.chip.st-FAILED {
  color: var(--danger);
  border-color: var(--danger);
}
.chip.st-PENDING,
.chip.st-PROCESSING {
  color: var(--warning);
  border-color: var(--warning);
}
.err-hint {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--danger);
  color: #fff;
  font-size: 10px;
  cursor: help;
}
.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}
.act-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  border: 0;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
}
.act-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.act-btn.warn:hover { color: var(--warning); }
.act-btn.danger:hover { color: var(--danger); }
.act-btn:disabled { opacity: 0.5; cursor: default; }

.empty {
  padding: 56px 0 64px;
  text-align: center;
}
.empty-icon {
  width: 76px;
  height: 76px;
  margin: 0 auto 14px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-soft);
  color: var(--accent);
}
.empty-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.empty-desc {
  font-size: var(--font-size-sm);
  margin-bottom: 16px;
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
  width: 620px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 80px);
  background: var(--bg-base);
  display: flex;
  flex-direction: column;
  padding: 16px 18px;
}
.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
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
.icon-btn:hover { background: var(--bg-hover); }
.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-row {
  display: flex;
  gap: 10px;
}
.field-row .field { flex: 1; }
.field-row .field-unit { flex: 0 0 110px; }
.label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.req { color: var(--danger); }
.textarea {
  resize: vertical;
  min-height: 60px;
  font-family: inherit;
  line-height: 1.5;
  padding-top: 6px;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  cursor: pointer;
}
.check-row input {
  width: 14px;
  height: 14px;
  accent-color: var(--accent);
}
.dialog-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}
.spacer { flex: 1; }
.spin {
  display: inline-flex;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
