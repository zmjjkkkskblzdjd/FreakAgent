<script setup>
import { onMounted } from 'vue'
import SessionList from '@/components/chat/SessionList.vue'
import ChatView from '@/components/chat/ChatView.vue'
import KbPanel from '@/components/chat/KbPanel.vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()


onMounted(() => chat.loadSessions())
</script>

<template>
  <div class="chat-page">
    <SessionList />
    <ChatView />
    <Transition name="kb-slide">
      <KbPanel v-if="chat.kbPanelOpen" />
    </Transition>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: 100%;
  width: 100%;
}
.kb-slide-enter-active,
.kb-slide-leave-active {
  transition: transform 0.2s ease, opacity 0.2s ease;
}
.kb-slide-enter-from,
.kb-slide-leave-to {
  transform: translateX(60px);
  opacity: 0;
}
</style>
