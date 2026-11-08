<script setup>
import { ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()


const open = ref(false)
const creating = ref(false)
const error = ref('')
const blankForm = () => ({
  name: '',
  base_url: '',
  completions_path: '/chat/completions',
  api_key: '',
  models: ''
})
const form = ref(blankForm())

async function submit() {
  error.value = ''
  const name = form.value.name.trim()
  const baseUrl = form.value.base_url.trim()
  if (!name || !baseUrl) {
    error.value = '名称与请求地址必填'
    return
  }
  creating.value = true
  try {
    const modelNames = form.value.models.split(/[,，\s]+/).filter(Boolean)
    await settings.createCustomProvider(
      {
        name,
        provider_type: 'custom',
        base_url: baseUrl,
        completions_path: form.value.completions_path.trim() || '/chat/completions',
        api_key: form.value.api_key.trim() || null,
        enabled: 1
      },
      modelNames
    )
    form.value = blankForm()
    open.value = false
  } catch (e) {
    error.value = e?.message || '创建失败'
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div v-if="!open" class="add-card" role="button" tabindex="0"
       @click="open = true" @keydown.enter="open = true">
    <span class="add-plus">+</span>
    <div class="add-text">
      <strong>自定义供应商</strong>
      <p class="text-muted">自填请求地址与模型名，接内置目录之外的模型</p>
    </div>
  </div>

  <div v-else class="create-card card">
    <div class="create-head">
      <strong>新增自定义供应商</strong>
      <button class="btn btn-ghost" @click="open = false">收起</button>
    </div>
    <div class="form-grid">
      <label class="field">
        <span>名称</span>
        <input class="input" v-model="form.name" placeholder="如 自建 Qwen 网关" />
      </label>
      <label class="field">
        <span>请求地址</span>
        <input class="input" v-model="form.base_url" placeholder="https://your-gateway/v1" />
      </label>
      <label class="field">
        <span>对话路径</span>
        <input class="input" v-model="form.completions_path" placeholder="/chat/completions" />
      </label>
      <label class="field">
        <span>API Key（可选）</span>
        <input class="input" v-model="form.api_key" type="password" placeholder="留空则稍后在卡片里录入" />
      </label>
      <label class="field field-wide">
        <span>模型名（逗号或空格分隔，可选）</span>
        <input class="input" v-model="form.models" placeholder="qwen3.8-max, qwen3.7-plus" />
      </label>
    </div>
    <div v-if="error" class="form-error">{{ error }}</div>
    <div class="form-actions">
      <button class="btn btn-primary" :disabled="creating" @click="submit">
        {{ creating ? '创建中…' : '创建' }}
      </button>
      <button class="btn btn-ghost" @click="open = false">取消</button>
    </div>
  </div>
</template>

<style scoped>
.add-card {
  min-height: 148px;
  padding: 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px dashed var(--border-strong, #cbd5e1);
  border-radius: var(--radius-lg, 12px);
  cursor: pointer;
  background: transparent;
  transition: border-color 0.15s, background 0.15s;
}
.add-card:hover,
.add-card:focus-visible {
  border-color: var(--accent);
  background: var(--accent-soft);
  outline: none;
}
.add-plus {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px dashed currentColor;
  color: var(--accent);
  font-size: 22px;
  line-height: 1;
}
.add-text strong {
  font-size: var(--font-size-base);
}
.add-text p {
  margin: 4px 0 0;
  font-size: var(--font-size-xs);
}
.create-card {
  grid-column: 1 / -1;
  padding: 16px;
}
.create-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
}
.field-wide {
  grid-column: 1 / -1;
}
.field .input {
  height: 32px;
}
.form-error {
  margin-top: 8px;
  font-size: var(--font-size-xs);
  color: var(--danger, #dc2626);
}
.form-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
