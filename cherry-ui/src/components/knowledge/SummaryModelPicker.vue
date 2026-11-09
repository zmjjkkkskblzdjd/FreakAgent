<script setup>
import { computed, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'

const props = defineProps({
  models: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' }
})
const emit = defineEmits(['select', 'close', 'config'])

const query = ref('')

const groups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const out = []
  const byProvider = new Map()
  for (const m of props.models) {
    const label = m.name || m.id
    if (q && !`${label} ${m.providerName || ''}`.toLowerCase().includes(q)) continue
    const pname = m.providerName || '自定义服务商'
    if (!byProvider.has(pname)) {
      const g = { name: pname, items: [] }
      byProvider.set(pname, g)
      out.push(g)
    }
    byProvider.get(pname).items.push(m)
  }
  return out
})

function pick(key) {
  emit('select', key)
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="picker card">
      <div class="picker-head">
        <h3>选择摘要模型</h3>
        <button class="icon-btn" @click="emit('close')"><Icon name="close" :size="16" /></button>
      </div>

      <div class="search-row">
        <Icon name="search" :size="15" class="search-icon" />
        <input v-model="query" class="input search-input" placeholder="搜索模型" autofocus />
      </div>

      <div class="model-list">
        <button class="model-item" :class="{ active: !modelValue }" @click="pick('')">
          <span class="model-name">不使用</span>
          <span class="model-desc">关闭文档摘要</span>
          <Icon v-if="!modelValue" name="check" :size="15" class="check" />
        </button>

        <div v-for="g in groups" :key="g.name" class="group">
          <div class="group-title">{{ g.name }}</div>
          <button
            v-for="m in g.items"
            :key="m.key"
            class="model-item"
            :class="{ active: m.key === modelValue }"
            @click="pick(m.key)"
          >
            <span class="model-name">{{ m.name || m.id }}</span>
            <span class="model-desc">{{ g.name }}</span>
            <Icon v-if="m.key === modelValue" name="check" :size="15" class="check" />
          </button>
        </div>

        <div v-if="!groups.length" class="empty text-muted">
          还没有可用的模型，请先到模型服务配置并勾选模型。
        </div>
      </div>

      <div class="picker-foot">
        <button class="btn btn-ghost config-btn" @click="emit('config')">
          <Icon name="settings" :size="14" />
          配置自定义模型
        </button>
        <button class="btn btn-ghost" @click="emit('close')">取消</button>
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
  z-index: 70;
}
.picker {
  width: 520px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
  padding: 14px 16px;
}
.picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.picker-head h3 {
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
.search-row {
  position: relative;
  margin-bottom: 8px;
}
.search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}
.search-input {
  padding-left: 32px;
}
.model-list {
  flex: 1;
  overflow-y: auto;
  min-height: 120px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 6px;
}
.group-title {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  padding: 8px 8px 4px;
}
.model-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  text-align: left;
  color: var(--text-primary);
}
.model-item:hover {
  background: var(--bg-hover);
}
.model-item.active {
  background: var(--bg-hover);
}
.model-name {
  font-size: var(--font-size-sm);
}
.model-desc {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}
.check {
  margin-left: auto;
  color: var(--accent);
}
.empty {
  padding: 20px 8px;
  font-size: var(--font-size-xs);
  text-align: center;
}
.picker-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.config-btn {
  color: var(--accent);
}
</style>
