<script setup>
import { computed } from 'vue'
import Icon from '@/components/common/Icon.vue'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import { useChatStore } from '@/stores/chat'
import { useSettingsStore } from '@/stores/settings'

const chat = useChatStore()
const settings = useSettingsStore()

const title = computed(() => chat.currentSession?.title || '新对话')
const currentAssistant = computed(() => chat.currentAssistant)
</script>

<template>
  <div class="chat-view">
    <header class="cv-topbar">
      <div class="cv-title">
        <span v-if="currentAssistant" class="avatar" :style="{ background: currentAssistant.color }">
          {{ currentAssistant.avatar }}
        </span>
        <Icon v-else name="chat" :size="16" />
        <span>{{ title }}</span>
      </div>
      <select
        class="select model-top"
        :value="settings.currentModelKey"
        @change="settings.selectModelByKey($event.target.value)"
        title="当前会话模型（仅列管理员已配置的服务商模型）"
      >
        <option v-for="m in settings.routeModels" :key="m.key" :value="m.key">{{ m.name }}</option>
        <option v-if="!settings.routeModels.length" value="">未配置模型</option>
      </select>
    </header>
    <MessageList />
    <ChatInput />
  </div>
</template>

<style scoped>
.chat-view {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
}
.cv-topbar {
  height: var(--topbar-h);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-base);
}
.cv-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
}
.avatar {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}
.model-top {
  height: 30px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
}
</style>
