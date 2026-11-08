<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loggingOut = ref(false)

const navs = [
  { name: 'chat', icon: 'chat', label: '聊天' },
  { name: 'knowledge', icon: 'book', label: '知识库' },
  { name: 'business', icon: 'tag', label: '业务库' },
  { name: 'datasources', icon: 'database', label: '数据源' },
  { name: 'settings', icon: 'settings', label: '设置' }
]

async function onLogout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await auth.logout()
    router.replace({ name: 'login' })
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand" :title="auth.tenant?.name || 'AllFusion'">
      <span class="brand-dot"></span>
    </div>

    <nav class="nav">
      <router-link
        v-for="item in navs"
        :key="item.name"
        :to="{ name: item.name }"
        class="nav-item"
        :title="item.label"
      >
        <Icon :name="item.icon" :size="20" />
      </router-link>
    </nav>

    <div class="bottom">
      <div class="avatar" :title="`${auth.displayName}（${auth.user?.role || ''}）`">
        {{ auth.initial }}
      </div>
      <router-link :to="{ name: 'settings' }" class="nav-item" title="设置">
        <Icon name="settings" :size="20" />
      </router-link>
      <button class="nav-item" title="退出登录" :disabled="loggingOut" @click="onLogout">
        <Icon name="logout" :size="20" />
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 0;
}
.brand {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--accent), #0fa3a0);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  box-shadow: var(--shadow-sm);
}
.brand-dot {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  background: #fff;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.nav-item {
  position: relative;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  background: none;
  border: 0;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.nav-item.router-link-active {
  color: var(--accent);
  background: var(--accent-soft);
}
.nav-item.router-link-active::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 3px;
  background: var(--accent);
}

.bottom {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--font-size-sm);
  font-weight: 600;
  user-select: none;
}
</style>
