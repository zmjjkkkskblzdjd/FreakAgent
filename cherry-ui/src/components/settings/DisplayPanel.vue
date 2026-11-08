<script setup>
import Icon from '@/components/common/Icon.vue'
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()

const themes = [
  { id: 'light', name: '亮色', icon: 'sun' },
  { id: 'dark', name: '暗色', icon: 'moon' }
]

const fontSizes = [
  { value: 12, label: '小' },
  { value: 14, label: '默认' },
  { value: 16, label: '大' }
]

const languages = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' }
]
</script>

<template>
  <div class="display-panel">
    <div class="panel-head">
      <h2 class="panel-title">显示</h2>
      <p class="panel-desc text-muted">主题、字号与语言偏好。</p>
    </div>

    <div class="field">
      <label class="field-label">主题</label>
      <div class="theme-row">
        <button
          v-for="t in themes"
          :key="t.id"
          class="theme-btn"
          :class="{ active: settings.theme === t.id }"
          @click="settings.setTheme(t.id)"
        >
          <Icon :name="t.icon" :size="16" />
          <span>{{ t.name }}</span>
        </button>
      </div>
    </div>

    <div class="field">
      <label class="field-label">字体大小</label>
      <div class="font-row">
        <button
          v-for="f in fontSizes"
          :key="f.value"
          class="font-btn"
          :class="{ active: settings.fontSize === f.value }"
          @click="settings.fontSize = f.value"
        >
          {{ f.label }}
        </button>
      </div>
    </div>

    <div class="field">
      <label class="field-label">语言</label>
      <select class="input select" v-model="settings.language">
        <option v-for="l in languages" :key="l.value" :value="l.value">{{ l.label }}</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.display-panel {
  padding: 20px 24px 40px;
  max-width: 640px;
}
.panel-head {
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
}
.field {
  margin-bottom: 20px;
}
.field-label {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-weight: 500;
}
.theme-row,
.font-row {
  display: flex;
  gap: 8px;
}
.theme-btn,
.font-btn {
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
}
.theme-btn:hover,
.font-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.theme-btn.active,
.font-btn.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.select {
  height: 36px;
  cursor: pointer;
  width: 200px;
}
</style>
