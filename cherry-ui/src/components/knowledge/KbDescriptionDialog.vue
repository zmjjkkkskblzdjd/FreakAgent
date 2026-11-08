<script setup>
import { onMounted, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useKnowledgeStore } from '@/stores/knowledge'

const props = defineProps({
  kb: { type: Object, required: true }
})
const emit = defineEmits(['close', 'saved'])

const knowledge = useKnowledgeStore()
const value = ref(props.kb.description || '')
const saving = ref(false)
const error = ref('')
const inputEl = ref(null)

async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  const ok = await knowledge.setDescription(props.kb.id, value.value)
  saving.value = false
  if (ok) emit('saved')
  else error.value = knowledge.error || '描述保存失败'
}

onMounted(() => inputEl.value?.focus())
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog card">
      <div class="dialog-head">
        <h3>编辑描述 · {{ kb.name }}</h3>
        <button class="icon-btn" @click="emit('close')"><Icon name="close" :size="16" /></button>
      </div>

      <p class="hint text-muted">
        用一两句话说明这个库收录什么内容，会帮助系统判断该不该检索它。
      </p>
      <textarea
        ref="inputEl"
        v-model="value"
        class="input desc-input"
        placeholder="例如：产品使用手册与常见问题解答"
        @keydown.ctrl.enter="save"
      />
      <div v-if="error" class="error-line">{{ error }}</div>

      <div class="dialog-foot">
        <div class="spacer" />
        <button class="btn btn-ghost" @click="emit('close')">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          <span v-if="saving" class="spin"><Icon name="refresh" :size="13" /></span>
          <span v-else>保存</span>
        </button>
      </div>
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
  margin: 0 0 8px;
}
.desc-input {
  height: auto;
  min-height: 88px;
  padding: 8px 12px;
  line-height: 1.5;
  resize: vertical;
}
.error-line {
  color: var(--danger);
  font-size: var(--font-size-xs);
  margin-top: 8px;
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
.spin {
  display: inline-flex;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
