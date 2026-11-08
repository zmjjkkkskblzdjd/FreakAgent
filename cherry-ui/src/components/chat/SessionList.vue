<script setup>
import { ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import AssistantDrawer from './AssistantDrawer.vue'
import { useAssistantStore } from '@/stores/assistant'
import { useChatStore } from '@/stores/chat'

const assistant = useAssistantStore()
const chat = useChatStore()

const editingId = ref(null)
const menuFor = ref(null)

function openNewAssistant() {
  const a = assistant.newAssistant()
  editingId.value = a.id
}
function openSettings(id) {
  editingId.value = id
}
function toggleMenu(id) {
  menuFor.value = menuFor.value === id ? null : id
}
function onPin(id) {
  chat.togglePin(id)
  menuFor.value = null
}
function onDelete(id) {
  deletingId.value = id
  menuFor.value = null
}
const deletingId = ref(null)
function doDeleteSession() {
  chat.deleteSession(deletingId.value)
  deletingId.value = null
}
function onNewSession(assistantId) {
  chat.newSession(assistantId)
}
</script>

<template>
  <div class="session-list">
    <div class="sl-head">
      <span class="title">对话</span>
      <button class="new-btn" title="新建助手" @click="openNewAssistant">
        <Icon name="plus" :size="18" />
      </button>
    </div>

    <div class="sl-body scroll">
      <div v-for="g in chat.groupedByAssistant" :key="g.assistant.id" class="group">
        <div class="assistant-row">
          <div class="assistant-meta">
            <span class="avatar" :style="{ background: g.assistant.color }">{{ g.assistant.avatar }}</span>
            <div class="assistant-info">
              <div class="assistant-name">{{ g.assistant.name }}</div>
              <div class="assistant-desc text-muted">{{ g.assistant.desc || '无简介' }}</div>
            </div>
          </div>
          <div class="assistant-actions">
            <button class="icon-btn" title="新建对话" @click="onNewSession(g.assistant.id)">
              <Icon name="plus" :size="14" />
            </button>
            <button class="icon-btn" title="助手设置" @click="openSettings(g.assistant.id)">
              <Icon name="settings" :size="14" />
            </button>
          </div>
        </div>

        <div
          v-for="s in g.items"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === chat.currentSessionId, pinned: s.pinned }"
          @click="chat.selectSession(s.id)"
        >
          <div class="s-head">
            <div class="s-title">{{ s.title }}</div>
            <button class="s-menu-btn" @click.stop="toggleMenu(s.id)" title="更多">
              <Icon name="more" :size="14" />
            </button>
            <div v-if="menuFor === s.id" class="s-menu" @click.stop>
              <button class="s-menu-item" @click="onPin(s.id)">
                <Icon :name="s.pinned ? 'close' : 'plus'" :size="12" />
                {{ s.pinned ? '取消置顶' : '置顶' }}
              </button>
              <button class="s-menu-item danger" @click="onDelete(s.id)">
                <Icon name="trash" :size="12" /> 删除
              </button>
            </div>
          </div>
          <div class="s-summary text-muted">{{ s.summary }}</div>
          <div class="s-time text-muted">{{ s.time }}</div>
        </div>

        <div v-if="!g.items.length" class="empty text-muted">（无对话，点 + 新建）</div>
      </div>
    </div>

    <AssistantDrawer v-if="editingId" :assistant-id="editingId" @close="editingId = null" />

    <ConfirmDialog
      v-if="deletingId"
      title="删除对话"
      message="确认删除该对话？对话内容将无法恢复"
      @confirm="doDeleteSession"
      @cancel="deletingId = null"
    />
  </div>
</template>

<style scoped>
.session-list {
  width: var(--secondary-w);
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.sl-head {
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px 0 18px;
  border-bottom: 1px solid var(--border);
}
.title {
  font-weight: 600;
  font-size: var(--font-size-lg);
}
.new-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.new-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}
.sl-body {
  flex: 1;
  padding: 8px 8px 16px;
}
.group {
  margin-bottom: 10px;
}
.assistant-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 8px 4px;
}
.assistant-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.avatar {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}
.assistant-info {
  min-width: 0;
  flex: 1;
}
.assistant-name {
  font-size: var(--font-size-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.assistant-desc {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.assistant-actions {
  display: flex;
  gap: 2px;
}
.icon-btn {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.session-item {
  position: relative;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-bottom: 2px;
  margin-left: 6px;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--accent-soft);
}
.session-item.active .s-title {
  color: var(--accent);
}
.session-item.pinned .s-title::before {
  content: '📌 ';
  font-size: 11px;
}
.s-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 4px;
}
.s-title {
  font-size: var(--font-size-sm);
  font-weight: 500;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.s-menu-btn {
  width: 20px;
  height: 20px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  opacity: 0;
  flex-shrink: 0;
}
.session-item:hover .s-menu-btn {
  opacity: 1;
}
.s-menu-btn:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}
.s-menu {
  position: absolute;
  top: 26px;
  right: 8px;
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  padding: 4px;
  z-index: 20;
  min-width: 110px;
}
.s-menu-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border-radius: 5px;
  font-size: var(--font-size-xs);
  color: var(--text-primary);
  text-align: left;
}
.s-menu-item:hover {
  background: var(--bg-hover);
}
.s-menu-item.danger {
  color: var(--danger);
}
.s-menu-item.danger:hover {
  background: rgba(224, 88, 79, 0.1);
}
.s-summary {
  font-size: var(--font-size-xs);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.s-time {
  font-size: 11px;
  margin-top: 2px;
}
.empty {
  font-size: var(--font-size-xs);
  padding: 4px 8px 8px 40px;
}
</style>
