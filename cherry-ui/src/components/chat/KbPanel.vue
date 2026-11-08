<script setup>
import { computed, onMounted, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { useChatStore } from '@/stores/chat'
import { useKnowledgeStore } from '@/stores/knowledge'

const chat = useChatStore()
const knowledge = useKnowledgeStore()

const query = ref('')


onMounted(() => {
  if (!knowledge.bases.length) knowledge.load()
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return knowledge.bases
  return knowledge.bases.filter((b) => b.name.toLowerCase().includes(q))
})
</script>

<template>
  <aside class="kb-panel">
    <header class="kbp-head">
      <span class="title">知识库</span>
      <div class="head-right">
        <div class="search-box">
          <Icon name="search" :size="14" />
          <input v-model="query" class="search-input" placeholder="搜索知识库" />
        </div>
        <button class="close-btn" title="关闭" @click="chat.toggleKbPanel()">
          <Icon name="close" :size="16" />
        </button>
      </div>
    </header>

    <div class="kbp-body scroll">
      <div
        v-for="kb in filtered"
        :key="kb.id"
        class="kb-item"
        :class="{ checked: chat.selectedKbIds.includes(kb.id) }"
        @click="chat.toggleKbSelect(kb.id)"
      >
        <div class="kb-icon"><Icon name="book" :size="16" /></div>
        <div class="kb-info">
          <div class="kb-name">{{ kb.name }}</div>
          <div class="kb-meta text-muted">{{ kb.docCount }} 文档 · {{ kb.updateTime }}</div>
        </div>
        <span class="checkbox">
          <Icon v-if="chat.selectedKbIds.includes(kb.id)" name="check" :size="12" />
        </span>
      </div>
      <div v-if="!filtered.length" class="empty text-muted">
        {{ query ? '未找到匹配的知识库' : '暂无知识库' }}
      </div>
    </div>
  </aside>
</template>

<style scoped>
.kb-panel {
  width: 320px;
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.kbp-head {
  height: var(--topbar-h);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 12px 0 18px;
  border-bottom: 1px solid var(--border);
}
.title {
  font-weight: 600;
  font-size: var(--font-size-lg);
  flex-shrink: 0;
}
.head-right {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-base);
  color: var(--text-muted);
  min-width: 0;
}
.search-box:focus-within {
  border-color: var(--accent);
}
.search-input {
  width: 110px;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--font-size-xs);
  color: var(--text-primary);
}
.search-input::placeholder {
  color: var(--text-muted);
}
.close-btn {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  flex-shrink: 0;
}
.close-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.kbp-body {
  flex: 1;
  padding: 10px 10px 16px;
}
.kb-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  border: 1px solid transparent;
}
.kb-item:hover {
  background: var(--bg-hover);
}
.kb-item.checked {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.kb-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--bg-active);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.kb-info {
  flex: 1;
  min-width: 0;
}
.kb-name {
  font-size: var(--font-size-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kb-meta {
  font-size: var(--font-size-xs);
  margin-top: 2px;
}

.checkbox {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid var(--border-strong);
  background: var(--bg-base);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
  margin-left: auto;
}
.kb-item.checked .checkbox {
  background: var(--accent);
  border-color: var(--accent);
}
.empty {
  padding: 24px 16px;
  text-align: center;
  font-size: var(--font-size-sm);
}
</style>
