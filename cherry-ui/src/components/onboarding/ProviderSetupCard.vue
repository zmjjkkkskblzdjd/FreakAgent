<script setup>
import { computed, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps({
  provider: { type: Object, required: true }
})

const auth = useAuthStore()
const settings = useSettingsStore()


const readonly = computed(() => !auth.isDbAdmin)
const showKey = ref(false)
const showModels = ref(true)
const keyInput = ref('')
const manualModel = ref('')
const saving = ref(false)
const saveError = ref('')
const testError = ref('')


const candidates = ref([])


function isSelected(modelId) {
  if (auth.isDbAdmin) return props.provider.selectedModels.includes(modelId)
  const s = settings.modelPrefSelected
  return !!s && s.provider_id === props.provider.id && s.model === modelId
}





const MAX_LISTED_MODELS = 5

const visibleModels = computed(() => {
  if (auth.isDbAdmin) {
    if (props.provider.providerType === 'custom') {
      const picked = props.provider.models.filter((m) =>
        props.provider.selectedModels.includes(m.id))
      const temp = candidates.value.filter((m) =>
        !props.provider.selectedModels.includes(m.id))
      return [...picked, ...temp]
    }
    return props.provider.models.slice(0, MAX_LISTED_MODELS)
  }
  const names = new Map(props.provider.models.map((m) => [m.id, m.name || m.id]))
  return settings.modelPrefOptions
    .filter((o) => o.provider_id === props.provider.id)
    .map((o) => ({ id: o.model, name: names.get(o.model) || o.model }))
})


function isCandidate(modelId) {
  return auth.isDbAdmin
    && props.provider.providerType === 'custom'
    && candidates.value.some((m) => m.id === modelId)
}


const foldedCount = computed(() => {
  if (!auth.isDbAdmin || props.provider.providerType === 'custom') return 0
  return Math.max(0, props.provider.models.length - MAX_LISTED_MODELS)
})

const selectedCount = computed(() => (
  auth.isDbAdmin
    ? props.provider.selectedModels.length
    : visibleModels.value.filter((m) => isSelected(m.id)).length
))


async function saveKey() {
  const key = keyInput.value.trim()
  if (!key) return
  saving.value = true
  saveError.value = ''
  testError.value = ''
  try {
    await settings.saveProviderKey(props.provider.id, key)
    keyInput.value = ''
  } catch (e) {
    saveError.value = e?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function runTest() {
  testError.value = ''
  try {
    const r = await settings.testProvider(props.provider.id)
    if (!r?.ok) testError.value = r?.hint || r?.error || '连接失败'
  } catch (e) {
    testError.value = e?.message || '测试失败'
  }
}



async function fetchModels() {
  showModels.value = true
  const before = new Set(props.provider.models.map((m) => m.id))
  await settings.fetchModels(props.provider.id)
  if (props.provider.providerType !== 'custom') return
  candidates.value = props.provider.models.filter((m) =>
    !before.has(m.id) && !props.provider.selectedModels.includes(m.id))
}


async function addManualModel() {
  const name = manualModel.value.trim()
  if (!name) return
  const ok = await settings.addModel(props.provider.id, name)
  if (ok) manualModel.value = ''
}

function toggleModel(modelId) {
  if (auth.isDbAdmin) {
    settings.toggleModel(props.provider.id, modelId)
    return
  }
  settings.selectModelByKey(`${props.provider.id}:${modelId}`)
}
</script>

<template>
  <div class="provider-card card" :class="{ enabled: provider.enabled }">
    <div class="provider-head">
      <span class="logo" :style="{ background: provider.color }">
        {{ provider.name.charAt(0) }}
      </span>
      <div class="provider-meta">
        <div class="provider-name">{{ provider.name }}</div>
        <div class="provider-desc text-muted">
          {{ provider.providerType === 'custom' ? provider.baseUrl : provider.desc }}
        </div>
      </div>
      <button
        class="toggle"
        :class="{ on: provider.enabled }"
        :disabled="readonly"
        :title="readonly ? '由管理员统一启停' : '启用/停用'"
        @click="settings.toggleProvider(provider.id)"
      >
        <span class="dot" />
      </button>
    </div>

    <div class="provider-key">
      <Icon name="key" :size="16" />
      <input
        class="input key-input"
        :type="showKey ? 'text' : 'password'"
        :disabled="readonly"
        :placeholder="
          readonly
            ? (provider.apiKeyMasked ? `由管理员配置（${provider.apiKeyMasked}）` : '由管理员配置')
            : (provider.apiKeyMasked
              ? `已保存 ${provider.apiKeyMasked}（留空不修改）`
              : '输入 API Key')
        "
        v-model="keyInput"
        @keyup.enter="saveKey"
      />
      <button v-if="!readonly" class="icon-btn" @click="showKey = !showKey" :title="showKey ? '隐藏' : '显示'">
        <Icon name="eye" :size="16" />
      </button>
      <button
        v-if="!readonly"
        class="btn btn-ghost save-btn"
        :disabled="!keyInput.trim() || saving"
        @click="saveKey"
      >
        <span v-if="saving" class="spin"><Icon name="refresh" :size="14" /></span>
        <span v-else>保存</span>
      </button>
      <button
        v-if="!readonly"
        class="btn btn-ghost test-btn"
        :class="{ testing: provider.testing, ok: provider.connected }"
        @click="runTest"
      >
        <span v-if="provider.testing" class="spin"><Icon name="refresh" :size="14" /></span>
        <span v-else-if="provider.connected" class="text-success"><Icon name="check" :size="14" /> 已连接</span>
        <span v-else>测试</span>
      </button>
    </div>

    <div v-if="saveError || testError" class="key-error">{{ saveError || testError }}</div>

    <a
      v-if="provider.keyUrl"
      class="key-link"
      :href="provider.keyUrl"
      target="_blank"
      rel="noopener noreferrer"
      :title="`前往${provider.name}控制台获取 API Key`"
    >
      <Icon name="external-link" :size="12" />
      <span>获取 API Key</span>
    </a>

    <button class="expand" @click="showModels = !showModels">
      <Icon :name="showModels ? 'chevron-down' : 'chevron-right'" :size="14" />
      <span>模型列表 ({{ visibleModels.length }})</span>
      <span class="text-muted picked">已选 {{ selectedCount }}</span>
    </button>

    <div v-if="!readonly" class="model-tools">
      <button class="btn btn-ghost refresh-btn" :disabled="provider.fetchingModels" @click="fetchModels">
        <span v-if="provider.fetchingModels" class="spin"><Icon name="refresh" :size="14" /></span>
        <span v-else>拉取模型</span>
      </button>
      <span v-if="provider.modelsSource" class="text-muted source-hint">来自 {{ provider.modelsSource }}</span>
    </div>
    <div v-if="provider.modelsError" class="key-error">{{ provider.modelsError }}</div>

    <div v-show="showModels" class="model-list">
      <label v-for="m in visibleModels" :key="m.id" class="model-item">
        <input
          type="checkbox"
          :checked="isSelected(m.id)"
          @change="toggleModel(m.id)"
        />
        <span class="model-name">{{ m.name }}</span>
        <span v-if="isCandidate(m.id)" class="candidate-tag">候选</span>
        <span class="text-muted model-id">{{ m.id }}</span>
      </label>
      <div v-if="foldedCount" class="text-muted empty-models">
        仅显示最新 {{ MAX_LISTED_MODELS }} 个，另有 {{ foldedCount }} 个较早模型未列出
      </div>
      <div v-if="!visibleModels.length" class="text-muted empty-models">
        {{ readonly
          ? '管理员尚未为这家服务商勾选可用模型'
          : (provider.providerType === 'custom'
            ? '尚未勾选模型：手动添加模型名即可，此处只展示已勾选的'
            : '暂无模型，可点“拉取模型”或手动添加') }}
      </div>
      <div v-if="!readonly" class="manual-add">
        <input
          class="input manual-input"
          v-model="manualModel"
          placeholder="手动添加模型名（如 glm-4.6）"
          @keyup.enter="addManualModel"
        />
        <button class="btn btn-ghost" :disabled="!manualModel.trim()" @click="addManualModel">
          添加
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.provider-card {
  padding: 16px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.provider-card.enabled {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent-soft);
}
.provider-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 600;
  font-size: 16px;
  flex-shrink: 0;
}
.provider-meta {
  flex: 1;
  min-width: 0;
}
.provider-name {
  font-weight: 600;
  font-size: var(--font-size-base);
}
.provider-desc {
  font-size: var(--font-size-xs);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}


.toggle {
  width: 40px;
  height: 22px;
  border-radius: 11px;
  background: var(--border-strong);
  position: relative;
  transition: background 0.2s;
  flex-shrink: 0;
}
.toggle .dot {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: var(--shadow-sm);
  transition: left 0.2s;
}
.toggle.on {
  background: var(--accent);
}
.toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.toggle.on .dot {
  left: 20px;
}

.provider-key {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  color: var(--text-muted);
}
.key-input {
  flex: 1;
  height: 32px;
}
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
}
.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.test-btn {
  height: 32px;
  padding: 0 12px;
  font-size: var(--font-size-xs);
}
.save-btn {
  height: 32px;
  padding: 0 12px;
  font-size: var(--font-size-xs);
}
.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.test-btn.ok {
  color: var(--success);
  border-color: var(--success);
}
.key-error {
  margin-top: 8px;
  font-size: var(--font-size-xs);
  color: var(--danger, #dc2626);
}

.key-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.key-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.expand {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
}
.expand:hover {
  color: var(--accent);
}
.picked {
  margin-left: auto;
}

.model-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.refresh-btn {
  height: 26px;
  padding: 0 10px;
  font-size: var(--font-size-xs);
}
.refresh-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.source-hint {
  font-size: var(--font-size-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.manual-add {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 0 8px;
}
.empty-models {
  padding: 4px 8px;
  font-size: var(--font-size-xs);
}
.manual-input {
  flex: 1;
  height: 28px;
  font-size: var(--font-size-xs);
}

.model-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.model-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.model-item:hover {
  background: var(--bg-hover);
}
.model-item input {
  accent-color: var(--accent);
}
.model-name {
  font-size: var(--font-size-sm);
}
.model-id {
  font-size: var(--font-size-xs);
  margin-left: auto;
}
.candidate-tag {
  flex-shrink: 0;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px dashed var(--border-strong);
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  line-height: 1.6;
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
.text-success {
  color: var(--success);
}
</style>
