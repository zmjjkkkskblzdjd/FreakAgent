<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import SummaryModelPicker from './SummaryModelPicker.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useSettingsStore } from '@/stores/settings'

const emit = defineEmits(['close', 'created'])

const knowledge = useKnowledgeStore()
const settings = useSettingsStore()
const router = useRouter()

const name = ref('')
const useSummary = ref(false)
const modelKey = ref('')
const pickerOpen = ref(false)
const saving = ref(false)
const error = ref('')
const inputEl = ref(null)

const models = computed(() =>
  settings.allEnabledModels.map((m) => ({
    key: `${m.providerId}:${m.id}`,
    id: m.id,
    name: m.name || m.id,
    providerName: m.providerName
  })))

const selectedLabel = computed(() => {
  const m = models.value.find((x) => x.key === modelKey.value)
  return m ? `${m.name} · ${m.providerName}` : (modelKey.value || '')
})

function toggleSummary() {
  if (useSummary.value) {
    useSummary.value = false
    modelKey.value = ''
    return
  }
  useSummary.value = true
  pickerOpen.value = true
}

function onPicked(key) {
  pickerOpen.value = false
  if (!key) {
    useSummary.value = false
    modelKey.value = ''
    return
  }
  modelKey.value = key
  useSummary.value = true
}

function openModelConfig() {
  pickerOpen.value = false
  emit('close')
  router.push({ name: 'settings' })
}

async function submit() {
  if (saving.value) return
  const title = name.value.trim()
  if (!title) {
    error.value = '请输入知识库名称'
    inputEl.value?.focus()
    return
  }
  if (useSummary.value && !modelKey.value) {
    error.value = '请选择文档摘要模型'
    pickerOpen.value = true
    return
  }
  saving.value = true
  error.value = ''
  const base = await knowledge.newBase(title, undefined, {
    summary_enabled: useSummary.value,
    summary_model: useSummary.value ? modelKey.value : ''
  })
  saving.value = false
  if (base) emit('created', base)
  else error.value = knowledge.error || '创建失败'
}

onMounted(() => {
  if (!settings.providersLoaded) settings.loadProviders()
  inputEl.value?.focus()
})
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog card">
      <div class="dialog-head">
        <h3>新建知识库</h3>
        <button class="icon-btn" @click="emit('close')"><Icon name="close" :size="16" /></button>
      </div>

      <div class="field">
        <label class="field-label">名称</label>
        <input
          ref="inputEl"
          v-model="name"
          class="input"
          placeholder="请输入知识库名称"
          @keydown.enter="submit"
        />
      </div>

      <div class="summary-row">
        <div class="summary-text">
          <div class="summary-title">文档摘要</div>
          <div class="hint text-muted">开启后上传文档时自动生成摘要；默认不使用</div>
        </div>
        <button
          type="button"
          class="switch"
          :class="{ on: useSummary }"
          @click="toggleSummary"
        >
          <span class="knob" />
        </button>
      </div>

      <div v-if="useSummary" class="model-row">
        <button type="button" class="model-btn" @click="pickerOpen = true">
          <Icon name="sparkles" :size="14" />
          <span class="model-label">{{ selectedLabel || '选择摘要模型' }}</span>
          <Icon name="settings" :size="14" class="model-cfg" />
        </button>
      </div>

      <div v-if="error" class="error-line">{{ error }}</div>

      <div class="dialog-foot">
        <div class="spacer" />
        <button class="btn btn-ghost" @click="emit('close')">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="submit">
          <span v-if="saving" class="spin"><Icon name="refresh" :size="13" /></span>
          <span v-else>创建</span>
        </button>
      </div>
    </div>

    <SummaryModelPicker
      v-if="pickerOpen"
      :models="models"
      :model-value="modelKey"
      @select="onPicked"
      @close="pickerOpen = false"
      @config="openModelConfig"
    />
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
  width: 480px;
  max-width: calc(100vw - 48px);
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
.icon-btn:hover {
  background: var(--bg-hover);
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
}
.summary-title {
  font-size: var(--font-size-sm);
  font-weight: 500;
}
.hint {
  font-size: var(--font-size-xs);
  margin: 2px 0 0;
}
.switch {
  width: 38px;
  height: 22px;
  border-radius: 999px;
  background: var(--border);
  padding: 2px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
  transition: background 0.2s;
}
.switch.on {
  background: var(--accent);
}
.knob {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s;
}
.switch.on .knob {
  transform: translateX(16px);
}
.model-row {
  margin-top: 10px;
}
.model-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  background: var(--bg-base);
}
.model-btn:hover {
  background: var(--bg-hover);
}
.model-label {
  flex: 1;
  text-align: left;
  font-size: var(--font-size-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-cfg {
  color: var(--text-muted);
}
.error-line {
  color: var(--danger);
  font-size: var(--font-size-xs);
  margin-top: 10px;
}
.dialog-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}
.spacer {
  flex: 1;
}
.spin {
  display: inline-flex;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
