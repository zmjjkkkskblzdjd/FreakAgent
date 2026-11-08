<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '@/components/common/Icon.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import KbGrantDialog from '@/components/knowledge/KbGrantDialog.vue'
import KbDescriptionDialog from '@/components/knowledge/KbDescriptionDialog.vue'
import { useKnowledgeStore } from '@/stores/knowledge'

const knowledge = useKnowledgeStore()

const menuFor = ref(null)
const renamingId = ref(null)
const renameValue = ref('')
const deletingKb = ref(null)
const grantsKb = ref(null)
const descKb = ref(null)

const hint = ref('')
let hintTimer = null
function showHint(text) {
  hint.value = text
  clearTimeout(hintTimer)
  hintTimer = setTimeout(() => (hint.value = ''), 2000)
}

function toggleMenu(id) {
  menuFor.value = menuFor.value === id ? null : id
}


function onDocClick() {
  menuFor.value = null
}
watch(menuFor, (v) => {
  if (v) document.addEventListener('click', onDocClick)
  else document.removeEventListener('click', onDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  clearTimeout(hintTimer)
})

async function startRename(kb) {
  menuFor.value = null
  renamingId.value = kb.id
  renameValue.value = kb.name
  await nextTick()
  document.querySelector('.rename-input')?.focus()
}

function confirmRename(kb) {
  if (renamingId.value !== kb.id) return
  const name = renameValue.value.trim()
  if (name && name !== kb.name) knowledge.renameBase(kb.id, name)
  renamingId.value = null
}

function startDelete(kb) {
  menuFor.value = null
  deletingKb.value = kb
}

function doDelete() {
  const kb = deletingKb.value
  deletingKb.value = null
  knowledge.deleteBase(kb.id)
}

function openGrants(kb) {
  menuFor.value = null
  grantsKb.value = kb
}

function openDescription(kb) {
  menuFor.value = null
  descKb.value = kb
}

function onDescriptionSaved() {
  descKb.value = null
  showHint('描述已保存')
}
</script>

<template>
  <div class="knowledge-list">
    <div class="kl-head">
      <span class="title">知识库</span>
      <button class="new-btn" title="新建知识库" @click="knowledge.newBase()">
        <Icon name="plus" :size="18" />
      </button>
    </div>
    <div class="kl-body scroll">
      <div
        v-for="kb in knowledge.bases"
        :key="kb.id"
        class="kb-card"
        :class="{ active: kb.id === knowledge.currentKbId, 'menu-open': menuFor === kb.id }"
        @click="knowledge.selectBase(kb.id)"
      >
        <div class="kb-icon"><Icon name="book" :size="18" /></div>
        <div class="kb-info">
          <input
            v-if="renamingId === kb.id"
            class="rename-input"
            v-model="renameValue"
            @click.stop
            @keydown.enter="confirmRename(kb)"
            @keydown.esc="renamingId = null"
            @blur="confirmRename(kb)"
          />
          <div v-else class="kb-name">{{ kb.name }}</div>
          <div class="kb-desc" :class="{ empty: !kb.description }" :title="kb.description">
            {{ kb.description || '未填写描述' }}
          </div>
          <div class="kb-meta text-muted">{{ kb.docCount }} 文档 · {{ kb.updateTime }}</div>
        </div>
        <button
          v-if="kb.canWrite || kb.canDelete || kb.canManageGrants"
          class="icon-btn more-btn"
          title="更多"
          @click.stop="toggleMenu(kb.id)"
        >
          <Icon name="more" :size="14" />
        </button>
        <div v-if="menuFor === kb.id" class="kb-menu" @click.stop>
          <button class="kb-menu-item" v-if="kb.canManageGrants" @click="openGrants(kb)">
            <Icon name="key" :size="12" /> 授权
          </button>
          <button class="kb-menu-item" v-if="kb.canWrite" @click="startRename(kb)">
            <Icon name="edit" :size="12" /> 重命名
          </button>
          <button class="kb-menu-item" v-if="kb.canWrite" @click="openDescription(kb)">
            <Icon name="tag" :size="12" /> 编辑描述
          </button>
          <button
            class="kb-menu-item danger"
            v-if="kb.canDelete && kb.id !== 'default'"
            @click="startDelete(kb)"
          >
            <Icon name="trash" :size="12" /> 删除
          </button>
        </div>
      </div>
      <div v-if="!knowledge.bases.length" class="empty text-muted">暂无知识库，点击 + 新建</div>
    </div>

    <ConfirmDialog
      v-if="deletingKb"
      title="删除知识库"
      :message="`确认删除知识库「${deletingKb.name}」？其下 ${deletingKb.docCount} 个文档将一并删除`"
      @confirm="doDelete"
      @cancel="deletingKb = null"
    />

    <KbGrantDialog v-if="grantsKb" :kb="grantsKb" @close="grantsKb = null" />

    <KbDescriptionDialog
      v-if="descKb"
      :kb="descKb"
      @close="descKb = null"
      @saved="onDescriptionSaved"
    />

    <transition name="hint-fade">
      <div v-if="hint" class="kl-hint">
        <span class="hint-ico"><Icon name="check" :size="13" /></span> {{ hint }}
      </div>
    </transition>
  </div>
</template>

<style scoped>
.knowledge-list {
  width: var(--secondary-w);
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.kl-head {
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
.kl-body {
  flex: 1;
  padding: 8px 8px 16px;
}
.kb-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-bottom: 4px;
}
.kb-card:hover {
  background: var(--bg-hover);
}
.kb-card.active {
  background: var(--accent-soft);
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
.kb-card.active .kb-icon {
  background: var(--accent);
  color: #fff;
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
.kb-card.active .kb-name {
  color: var(--accent);
}
.kb-desc {
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kb-desc.empty {
  color: var(--text-muted);
}
.kb-meta {
  font-size: var(--font-size-xs);
  margin-top: 2px;
}
.rename-input {
  width: 100%;
  height: 24px;
  padding: 0 6px;
  border: 1px solid var(--accent);
  border-radius: 4px;
  background: var(--bg-base);
  color: var(--text-primary);
  font-size: var(--font-size-sm);
  font-weight: 600;
  outline: none;
}

.icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  flex-shrink: 0;
}
.more-btn {
  opacity: 0;
}
.kb-card:hover .more-btn,
.kb-card.menu-open .more-btn {
  opacity: 1;
}
.icon-btn:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.kb-menu {
  position: absolute;
  top: 34px;
  right: 8px;
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  padding: 4px;
  z-index: 20;
  min-width: 110px;
}
.kb-menu-item {
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
.kb-menu-item:hover {
  background: var(--bg-hover);
}
.kb-menu-item.danger {
  color: var(--danger);
}
.kb-menu-item.danger:hover {
  background: rgba(224, 88, 79, 0.1);
}
.empty {
  padding: 16px;
  text-align: center;
  font-size: var(--font-size-sm);
}

.kl-hint {
  position: fixed;
  left: 50%;
  bottom: 28px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 999px;
  background: var(--bg-base);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  z-index: 300;
  pointer-events: none;
}
.hint-ico {
  display: inline-flex;
  color: var(--success);
}
.hint-fade-enter-active,
.hint-fade-leave-active {
  transition: opacity 0.18s, transform 0.18s;
}
.hint-fade-enter-from,
.hint-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}
</style>
