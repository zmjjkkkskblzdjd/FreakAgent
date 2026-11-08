<script setup>
import { nextTick, ref, watch } from 'vue'
import Icon from '@/components/common/Icon.vue'
import MessageBubble from './MessageBubble.vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
const scroller = ref(null)


watch(
  () => [chat.currentSessionId, chat.currentSession?.messages.length],
  async () => {
    await nextTick()
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  },
  { immediate: true }
)
</script>

<template>
  <div class="message-list scroll" ref="scroller">
    <template v-if="chat.currentSession && chat.currentSession.messages.length">
      <div class="list-inner">
        <MessageBubble
          v-for="m in chat.currentSession.messages"
          :key="m.id"
          :message="m"
        />
        <div v-if="chat.isSending(chat.currentSessionId)" class="typing">
          <div class="dot" />
          <div class="dot" />
          <div class="dot" />
        </div>
      </div>
    </template>
    <div v-else class="empty">
      <div class="empty-icon"><Icon name="chat" :size="40" /></div>
      <div class="empty-title">开始对话吧</div>
      <div class="empty-desc text-muted">在下方输入框提问，助手会为你解答</div>
    </div>
  </div>
</template>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  background: var(--bg-base);
}
.list-inner {
  max-width: 920px;
  margin: 0 auto;
  padding: 24px 24px 8px;
}
.empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-secondary);
}
.empty-icon {
  width: 72px;
  height: 72px;
  border-radius: 18px;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
}
.empty-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
}
.empty-desc {
  font-size: var(--font-size-sm);
}
.typing {
  display: inline-flex;
  gap: 4px;
  padding: 10px 14px;
  background: var(--assistant-bubble);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  border-top-left-radius: 4px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: blink 1.2s infinite ease-in-out;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.3; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
}
</style>
