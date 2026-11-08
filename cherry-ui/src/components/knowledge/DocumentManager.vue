<script setup>
import { computed, ref, watch } from 'vue'
import Icon from '@/components/common/Icon.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import RecallTestDrawer from './RecallTestDrawer.vue'
import { useKnowledgeStore } from '@/stores/knowledge'

const knowledge = useKnowledgeStore()

const dragOver = ref(false)
const showRecall = ref(false)
const fileInput = ref(null)
const base = computed(() => knowledge.currentBase)
const docs = computed(() => base.value?.docs || [])
const currentDoc = computed(() => knowledge.currentDoc)

async function uploadFiles(fileList) {
  const files = Array.from(fileList || [])
  for (const f of files) await knowledge.upload(f)
}

function onDrop(e) {
  e.preventDefault()
  dragOver.value = false
  if (knowledge.uploading) return
  uploadFiles(e.dataTransfer?.files)
}
function onPick() {
  if (knowledge.uploading) return
  fileInput.value?.click()
}
function onFileChange(e) {
  uploadFiles(e.target.files)
  e.target.value = '' 
}


watch(
  () => [knowledge.currentKbId, knowledge.currentDocId],
  ([kbId, docId]) => {
    if (kbId && docId) knowledge.loadChunks(kbId, docId)
    else knowledge.chunks = []
  },
  { immediate: true }
)

const deletingDoc = ref(null)
function onDeleteDoc(d) {
  deletingDoc.value = d
}
async function doDeleteDoc() {
  const d = deletingDoc.value
  deletingDoc.value = null
  await knowledge.deleteDoc(d.id)
}
</script>

<template>
  <div class="doc-manager" v-if="base">
    <header class="dm-topbar">
      <div class="dm-title">
        <Icon name="book" :size="16" />
        <span>{{ base.name }}</span>
      </div>
      <div class="dm-tools">
        <select class="select" title="嵌入模型">
          <option>{{ base.embeddingModel }}</option>
        </select>
        <button class="btn btn-ghost" title="检索设置（占位）">
          <Icon name="search" :size="14" /> 检索设置
        </button>
        <button class="btn btn-ghost recall-btn" title="召回测试" @click="showRecall = true">
          <Icon name="sparkles" :size="14" /> 召回测试
        </button>
      </div>
    </header>

    <RecallTestDrawer v-if="showRecall" :kb="base" @close="showRecall = false" />

    <ConfirmDialog
      v-if="deletingDoc"
      title="删除文档"
      :message="`确认删除文档「${deletingDoc.name}」？其分块与索引将一并移除`"
      @confirm="doDeleteDoc"
      @cancel="deletingDoc = null"
    />

    <div class="dm-body">
      <div class="left-col">
        <div
          v-if="base.canWrite"
          class="dropzone"
          :class="{ active: dragOver, busy: knowledge.uploading }"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop="onDrop"
          @click="onPick"
        >
          <div class="drop-icon">
            <Icon :name="knowledge.uploading ? 'refresh' : 'upload'" :size="28" :class="{ spin: knowledge.uploading }" />
          </div>
          <div class="drop-title">{{ knowledge.uploading ? '上传并索引中…' : '拖拽文件到此处上传' }}</div>
          <div class="drop-desc text-muted">
            {{ knowledge.uploading ? '解析需数秒，完成后状态自动更新' : '或点击选择文件（PDF / DOCX / PPTX / XLSX / 图片）' }}
          </div>
          <input
            ref="fileInput"
            class="hidden-file"
            type="file"
            multiple
            accept=".pdf,.docx,.pptx,.xlsx,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp"
            @click.stop
            @change="onFileChange"
          />
        </div>
        <div v-else class="readonly-bar text-muted">
          <Icon name="eye" :size="14" /> 只读知识库：可检索与查看，上传/删除需管理员授予写权限
        </div>

        <div v-if="knowledge.error" class="error-bar">
          <Icon name="close" :size="13" />
          <span>{{ knowledge.error }}</span>
        </div>

        <div class="doc-table card">
          <div class="table-head">
            <div class="col-name">文件名</div>
            <div class="col-size">大小</div>
            <div class="col-chunks">分块</div>
            <div class="col-status">状态</div>
            <div class="col-act"></div>
          </div>
          <div
            v-for="d in docs"
            :key="d.id"
            class="table-row"
            :class="{ active: d.id === knowledge.currentDocId }"
            @click="knowledge.selectDoc(d.id)"
          >
            <div class="col-name"><Icon name="file" :size="14" /> <span>{{ d.name }}</span></div>
            <div class="col-size text-muted">{{ d.size }}</div>
            <div class="col-chunks">{{ d.chunks || '—' }}</div>
            <div class="col-status">
              <span
                class="status"
                :class="d.status === '已索引' ? 'ok' : d.status === '失败' ? 'fail' : 'pending'"
                :title="d.error || d.status"
              >{{ d.status }}</span>
            </div>
            <div class="col-act">
              <button
                v-if="base.canWrite"
                class="icon-btn"
                title="删除文档"
                @click.stop="onDeleteDoc(d)"
              ><Icon name="trash" :size="14" /></button>
            </div>
          </div>
          <div v-if="!docs.length" class="table-empty text-muted">暂无文档，请上传</div>
        </div>
      </div>

      <aside class="preview-panel card" v-if="currentDoc">
        <div class="preview-head">
          <span class="ph-title">{{ currentDoc.name }} · 分块预览</span>
          <span class="ph-count text-muted" v-if="!knowledge.chunksLoading">共 {{ knowledge.chunks.length }} 块（展示前 8 块）</span>
        </div>
        <div class="preview-body scroll">
          <div v-if="knowledge.chunksLoading" class="preview-hint">
            <Icon name="refresh" :size="16" class="spin" />
            <span>加载分块…</span>
          </div>
          <template v-else-if="knowledge.chunks.length">
            <div v-for="(c, i) in knowledge.chunks" :key="c.chunk_id" class="chunk">
              <div class="chunk-idx">分块 #{{ i + 1 }}</div>
              <div class="chunk-text">{{ c.content }}</div>
            </div>
          </template>
          <div v-else class="preview-hint">
            <span>{{ currentDoc.error || (currentDoc.status === '已索引' ? '暂无分块' : currentDoc.status) }}</span>
          </div>
        </div>
      </aside>
      <aside class="preview-panel-empty" v-else>
        <div class="empty-icon"><Icon name="file" :size="36" /></div>
        <div class="text-muted">选择左侧文档查看分块预览</div>
      </aside>
    </div>
  </div>

  <div v-else class="empty">
    <div class="empty-icon"><Icon name="book" :size="40" /></div>
    <div class="empty-title">未选择知识库</div>
    <div class="empty-desc text-muted">从左侧选择一个知识库或点击 + 新建</div>
  </div>
</template>

<style scoped>
.doc-manager {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
  min-width: 0;
}
.dm-topbar {
  height: var(--topbar-h);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
}
.dm-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-base);
  font-weight: 600;
}
.dm-tools {
  display: flex;
  gap: 8px;
  align-items: center;
}
.select {
  height: 30px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
}
.dm-body {
  flex: 1;
  display: flex;
  gap: 16px;
  padding: 16px 20px;
  overflow: hidden;
}
.left-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}
.dropzone {
  border: 2px dashed var(--border-strong);
  border-radius: var(--radius);
  padding: 22px;
  text-align: center;
  cursor: pointer;
  background: var(--bg-elevated);
  transition: all 0.15s;
}
.readonly-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-elevated);
  font-size: 12px;
}
.dropzone:hover,
.dropzone.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.dropzone.busy {
  cursor: progress;
  border-color: var(--accent);
  background: var(--accent-soft);
}
.hidden-file {
  display: none;
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.error-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: rgba(224, 88, 79, 0.1);
  color: var(--danger);
  font-size: var(--font-size-xs);
}
.drop-icon {
  color: var(--accent);
  margin-bottom: 8px;
}
.drop-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
}
.drop-desc {
  font-size: var(--font-size-xs);
  margin-top: 4px;
}
.doc-table {
  flex: 1;
  overflow: auto;
  padding: 0;
}
.table-head,
.table-row {
  display: grid;
  grid-template-columns: 1fr 70px 60px 80px 40px;
  gap: 8px;
  padding: 10px 14px;
  align-items: center;
}
.table-head {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg-elevated);
}
.table-row {
  font-size: var(--font-size-sm);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.table-row:hover {
  background: var(--bg-hover);
}
.table-row.active {
  background: var(--accent-soft);
}
.col-name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.col-name span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.col-status .status {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
}
.status.ok {
  background: rgba(43, 182, 115, 0.14);
  color: var(--success);
}
.status.pending {
  background: rgba(245, 166, 35, 0.14);
  color: var(--warning);
}
.status.fail {
  background: rgba(224, 88, 79, 0.14);
  color: var(--danger);
  cursor: help;
}
.col-act {
  display: flex;
  justify-content: flex-end;
}
.icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.icon-btn:hover {
  background: var(--bg-active);
  color: var(--danger);
}
.table-empty {
  padding: 20px;
  text-align: center;
  font-size: var(--font-size-sm);
}

.preview-panel {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.preview-head {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--font-size-sm);
  font-weight: 600;
}
.ph-title {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ph-count {
  font-size: 11px;
  font-weight: 400;
  flex-shrink: 0;
}
.preview-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 16px;
  color: var(--text-muted);
  font-size: var(--font-size-xs);
  text-align: center;
}
.preview-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}
.chunk {
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}
.chunk-idx {
  font-size: 11px;
  color: var(--accent);
  font-weight: 600;
  margin-bottom: 6px;
}
.chunk-text {
  font-size: var(--font-size-xs);
  line-height: 1.6;
  color: var(--text-secondary);
}
.preview-panel-empty {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-secondary);
}
.preview-panel-empty .empty-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-secondary);
}
.empty-icon {
  width: 72px;
  height: 72px;
  border-radius: 18px;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
}
.empty-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}
.empty-desc {
  font-size: var(--font-size-sm);
}
</style>
