<script setup>
import { computed, ref, watch } from 'vue'
import Icon from '@/components/common/Icon.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { useAssistantStore } from '@/stores/assistant'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps({
  assistantId: { type: String, required: true }
})
const emit = defineEmits(['close'])

const assistant = useAssistantStore()
const knowledge = useKnowledgeStore()
const settings = useSettingsStore()


const draft = ref(null)
watch(
  () => props.assistantId,
  (id) => {
    const a = assistant.byId(id)
    draft.value = a ? JSON.parse(JSON.stringify(a)) : null
  },
  { immediate: true }
)

const modelOptions = computed(() => settings.allEnabledModels)

function save() {
  assistant.update(props.assistantId, draft.value)
  emit('close')
}
function reset() {
  const a = assistant.byId(props.assistantId)
  if (a) draft.value = JSON.parse(JSON.stringify(a))
}
const showDeleteConfirm = ref(false)
function remove() {
  showDeleteConfirm.value = true
}
function doRemove() {
  assistant.deleteAssistant(props.assistantId)
  emit('close')
}
function toggleKb(id) {
  const arr = draft.value.knowledgeIds || (draft.value.knowledgeIds = [])
  const idx = arr.indexOf(id)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(id)
}
</script>

<template>
  <teleport to="body">
    <div v-if="draft" class="drawer-mask" @click.self="emit('close')">
      <aside class="drawer scroll">
        <header class="drawer-head">
          <div class="title">
            <span class="avatar" :style="{ background: draft.color }">{{ draft.avatar }}</span>
            <span>助手设置</span>
          </div>
          <button class="close" @click="emit('close')"><Icon name="close" :size="16" /></button>
        </header>

        <div class="drawer-body">
          <div class="field">
            <label class="field-label">名称</label>
            <input class="input" v-model="draft.name" placeholder="助手名称" />
          </div>

          <div class="field">
            <label class="field-label">简介</label>
            <input class="input" v-model="draft.desc" placeholder="一句话简介" />
          </div>

          <div class="field">
            <label class="field-label">系统提示词</label>
            <textarea
              class="input textarea"
              v-model="draft.prompt"
              rows="6"
              placeholder="你是一个专业的助手…"
            />
          </div>

          <div class="field">
            <label class="field-label">温度 temperature: {{ draft.temperature }}</label>
            <input type="range" class="range" min="0" max="2" step="0.1" v-model.number="draft.temperature" />
          </div>

          <div class="field">
            <label class="field-label">Top P: {{ draft.topP }}</label>
            <input type="range" class="range" min="0" max="1" step="0.05" v-model.number="draft.topP" />
          </div>

          <div class="field">
            <label class="field-label">最大 Tokens</label>
            <input class="input" type="number" v-model.number="draft.maxTokens" min="1" />
          </div>

          <div class="field">
            <label class="field-label">关联模型</label>
            <select class="input select" v-model="draft.model">
              <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.providerName }} · {{ m.name }}</option>
              <option v-if="!modelOptions.length" value="">未配置模型</option>
            </select>
          </div>

          <div class="field">
            <label class="field-label">关联知识库</label>
            <div class="kb-list">
              <label
                v-for="kb in knowledge.bases"
                :key="kb.id"
                class="kb-item"
                :class="{ checked: (draft.knowledgeIds || []).includes(kb.id) }"
                @click="toggleKb(kb.id)"
              >
                <span class="checkbox">
                  <Icon v-if="(draft.knowledgeIds || []).includes(kb.id)" name="check" :size="12" />
                </span>
                <span class="kb-name">{{ kb.name }}</span>
                <span class="text-muted kb-meta">{{ kb.docCount }} 文档</span>
              </label>
              <div v-if="!knowledge.bases.length" class="text-muted">暂无知识库</div>
            </div>
          </div>
        </div>

        <footer class="drawer-foot">
          <button class="btn btn-danger" @click="remove">
            <Icon name="trash" :size="14" /> 删除
          </button>
          <div class="spacer" />
          <button class="btn btn-ghost" @click="reset">
            <Icon name="refresh" :size="14" /> 重置
          </button>
          <button class="btn btn-primary" @click="save">
            <Icon name="check" :size="14" /> 保存
          </button>
        </footer>
      </aside>
    </div>
  </teleport>

  <ConfirmDialog
    v-if="showDeleteConfirm"
    title="删除助手"
    :message="`确认删除助手「${draft?.name}」？其下所有对话也会被删除`"
    @confirm="doRemove"
    @cancel="showDeleteConfirm = false"
  />
</template>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 100;
}
.drawer {
  width: 480px;
  max-width: 92vw;
  height: 100vh;
  background: var(--bg-base);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.drawer-head {
  height: 56px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
}
.title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--font-size-base);
  font-weight: 600;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}
.close {
  width: 30px;
  height: 30px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.drawer-body {
  flex: 1;
  padding: 20px;
}
.field {
  margin-bottom: 18px;
}
.field-label {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-bottom: 6px;
  font-weight: 500;
}
.textarea {
  width: 100%;
  min-height: 120px;
  resize: vertical;
  font-family: var(--font-family);
  line-height: 1.6;
}
.range {
  width: 100%;
  accent-color: var(--accent);
}
.select {
  height: 36px;
  cursor: pointer;
}
.kb-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kb-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  background: var(--bg-elevated);
}
.kb-item:hover {
  border-color: var(--accent);
}
.kb-item.checked {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.checkbox {
  width: 16px;
  height: 16px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: var(--bg-base);
}
.kb-item.checked .checkbox {
  background: var(--accent);
  border-color: var(--accent);
}
.kb-name {
  flex: 1;
  font-size: var(--font-size-sm);
}
.kb-meta {
  font-size: var(--font-size-xs);
}
.drawer-foot {
  display: flex;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
  background: var(--bg-elevated);
  flex-shrink: 0;
}
.spacer {
  flex: 1;
}
.btn-danger {
  background: transparent;
  color: var(--danger);
  border: 1px solid var(--danger);
}
.btn-danger:hover {
  background: rgba(224, 88, 79, 0.1);
}
</style>
