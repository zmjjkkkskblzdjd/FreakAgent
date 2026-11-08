<script setup>
import { computed, onMounted } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useChatStore } from '@/stores/chat'
import { useSettingsStore } from '@/stores/settings'

const chat = useChatStore()
const settings = useSettingsStore()


const sending = computed(() => chat.isSending(chat.currentSessionId))

onMounted(() => chat.loadDatasources())

function onEnter(e) {
  if (e.shiftKey) return
  e.preventDefault()
  if (sending.value) return          
  chat.sendMessage(chat.draftInput)
}
function onSubmit() {
  if (sending.value) {
    chat.cancelSession(chat.currentSessionId)
    return
  }
  chat.sendMessage(chat.draftInput)
}

function onDatasourceChange(e) {
  const v = e.target.value
  chat.selectDatasource(v === '' ? null : Number(v))
}

function noop() {}
</script>

<template>
  <div class="chat-input">
    <div class="input-wrap">
      <textarea
        class="input textarea"
        v-model="chat.draftInput"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        @keydown.enter="onEnter"
        rows="3"
      />
    </div>
    <div class="toolbar">
      <div class="left-tools">
        <select class="select model-select" :value="settings.currentModelKey" @change="settings.selectModelByKey($event.target.value)" title="当前模型（仅列管理员已配置的服务商模型）">
          <option v-for="m in settings.routeModels" :key="m.key" :value="m.key">{{ m.providerName }} · {{ m.name }}</option>
          <option v-if="!settings.routeModels.length" value="">未配置模型（请联系管理员）</option>
        </select>
        <select
          class="select datasource-select"
          :value="chat.datasourceId ?? ''"
          title="数据源（本轮读哪套库）"
          @change="onDatasourceChange"
        >
          <option value="">
            {{ settings.datasourceFallback ? '默认分析库（未选择时兜底）' : '未选择数据源（本轮不查库）' }}
          </option>
          <option v-for="d in chat.datasources" :key="d.id" :value="String(d.id)">
            {{ d.name }}（{{ d.database_name }}）
          </option>
        </select>
        <button class="icon-btn" title="附件（占位）" @click="noop"><Icon name="paperclip" :size="16" /></button>
        <button
          class="icon-btn"
          :class="{ active: chat.kbPanelOpen }"
          title="知识库"
          @click="chat.toggleKbPanel()"
        >
          <Icon name="book" :size="16" />
        </button>
        <button
          class="icon-btn"
          :class="{ active: settings.webEnabled }"
          :title="settings.webEnabled ? '联网搜索：已开启（点击关闭）' : '联网搜索：已关闭（点击开启）'"
          @click="settings.setWebEnabled(!settings.webEnabled)"
        >
          <Icon name="globe" :size="16" />
        </button>
      </div>
      <button
        v-if="!sending"
        class="btn btn-primary send-btn"
        :disabled="!chat.draftInput.trim()"
        @click="onSubmit"
      >
        <Icon name="send" :size="16" />
        <span>发送</span>
      </button>
      <button
        v-else
        class="btn send-btn stop-btn"
        title="终止本次调用"
        @click="onSubmit"
      >
        <Icon name="stop" :size="14" />
        <span>终止</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-input {
  border-top: 1px solid var(--border);
  background: var(--bg-base);
  padding: 10px 16px 14px;
}
.input-wrap {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-elevated);
  padding: 8px 10px;
  transition: border-color 0.15s;
}
.input-wrap:focus-within {
  border-color: var(--accent);
}
.textarea {
  width: 100%;
  border: none;
  background: transparent;
  resize: none;
  outline: none;
  font-size: var(--font-size-sm);
  line-height: 1.6;
  color: var(--text-primary);
  min-height: 44px;
  max-height: 200px;
}
.textarea::placeholder {
  color: var(--text-muted);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.left-tools {
  display: flex;
  align-items: center;
  gap: 6px;
}
.model-select {
  height: 30px;
  font-size: var(--font-size-xs);
  padding: 0 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  max-width: 220px;
}
.datasource-select {
  height: 30px;
  font-size: var(--font-size-xs);
  padding: 0 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  max-width: 200px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  border: 1px solid transparent;
}
.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.icon-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.send-btn {
  display: flex;
  align-items: center;
  gap: 6px;
}
.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.stop-btn {
  background: #e5484d;
  color: #fff;
}
.stop-btn:hover {
  background: #d13b40;
}
</style>
