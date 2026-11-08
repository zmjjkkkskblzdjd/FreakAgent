<script setup>
import Icon from '@/components/common/Icon.vue'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/auth'
import { computed } from 'vue'

const settings = useSettingsStore()
const auth = useAuthStore()

const baseGroups = [
  {
    title: '模型',
    items: [
      { id: 'providers', name: '模型服务', icon: 'robot' },
      { id: 'defaultModel', name: '默认模型', icon: 'sparkles' }
    ]
  },
  {
    title: '外观',
    items: [
      { id: 'display', name: '显示', icon: 'sun' }
    ]
  },
  {
    title: '其他',
    items: [
      { id: 'shortcuts', name: '快捷键', icon: 'tag' },
      { id: 'about', name: '关于', icon: 'globe' }
    ]
  }
]


const groups = computed(() => {
  if (!auth.isDbAdmin) return baseGroups
  return [
    ...baseGroups,
    { title: '管理', items: [{ id: 'users', name: '用户与权限', icon: 'user' }] }
  ]
})

const props = defineProps({
  active: { type: String, default: 'providers' }
})
const emit = defineEmits(['update:active'])

function select(id) {
  emit('update:active', id)
}
</script>

<template>
  <div class="settings-nav">
    <div class="sn-head">
      <span class="title">设置</span>
    </div>
    <div class="sn-body scroll">
      <div v-for="g in groups" :key="g.title" class="nav-group">
        <div class="group-title">{{ g.title }}</div>
        <div
          v-for="i in g.items"
          :key="i.id"
          class="nav-item"
          :class="{ active: active === i.id }"
          @click="select(i.id)"
        >
          <Icon :name="i.icon" :size="16" />
          <span>{{ i.name }}</span>
        </div>
      </div>

      <div class="reset-area" v-if="settings.onboardingCompleted">
        <button class="btn btn-ghost btn-sm" @click="settings.resetOnboarding()">
          <Icon name="refresh" :size="12" /> 重走首次配置
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-nav {
  width: var(--secondary-w);
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.sn-head {
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
}
.title {
  font-weight: 600;
  font-size: var(--font-size-lg);
}
.sn-body {
  flex: 1;
  padding: 10px 8px 16px;
}
.nav-group {
  margin-bottom: 12px;
}
.group-title {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  padding: 6px 10px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.nav-item:hover {
  background: var(--bg-hover);
}
.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.reset-area {
  padding: 8px 10px;
  margin-top: 12px;
}
.btn-sm {
  font-size: var(--font-size-xs);
  padding: 4px 8px;
}
</style>
