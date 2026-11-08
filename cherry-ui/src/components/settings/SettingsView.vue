<script setup>
import { computed, ref } from 'vue'
import SettingsNav from './SettingsNav.vue'
import ModelProviderPanel from './ModelProviderPanel.vue'
import DefaultModelPanel from './DefaultModelPanel.vue'
import DisplayPanel from './DisplayPanel.vue'
import UsersPanel from './UsersPanel.vue'
import Icon from '@/components/common/Icon.vue'


const active = ref('providers')

const placeholderPanels = {
  shortcuts: '快捷键（占位）',
  about: '关于 Cherry UI（占位）'
}

const placeholderText = computed(() => placeholderPanels[active.value] || '')
</script>

<template>
  <div class="settings-view">
    <SettingsNav v-model:active="active" />
    <main class="settings-main scroll">
      <ModelProviderPanel v-show="active === 'providers'" />
      <DefaultModelPanel v-show="active === 'defaultModel'" />
      <DisplayPanel v-show="active === 'display'" />
      <UsersPanel v-if="active === 'users'" />
      <div v-show="['shortcuts', 'about'].includes(active)" class="placeholder">
        <div class="placeholder-icon"><Icon name="settings" :size="36" /></div>
        <div class="placeholder-text">{{ placeholderText }}</div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.settings-view {
  flex: 1;
  display: flex;
  background: var(--bg-base);
  min-width: 0;
}
.settings-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}
.placeholder {
  padding: 60px 24px;
  text-align: center;
  color: var(--text-secondary);
}
.placeholder-icon {
  width: 72px;
  height: 72px;
  margin: 0 auto 12px;
  border-radius: 18px;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
}
.placeholder-text {
  font-size: var(--font-size-sm);
}
</style>
