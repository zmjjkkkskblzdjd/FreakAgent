<script setup>
import Icon from './Icon.vue'

defineProps({
  title: { type: String, default: '确认删除' },
  message: { type: String, required: true },
  confirmText: { type: String, default: '删除' },
  cancelText: { type: String, default: '取消' }
})
const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <teleport to="body">
    <div class="confirm-mask" @click.self="emit('cancel')">
      <div class="confirm-dialog">
        <div class="cd-icon"><Icon name="trash" :size="22" /></div>
        <div class="cd-title">{{ title }}</div>
        <div class="cd-message">{{ message }}</div>
        <div class="cd-actions">
          <button class="btn btn-ghost" @click="emit('cancel')">{{ cancelText }}</button>
          <button class="btn cd-danger" @click="emit('confirm')">
            <Icon name="trash" :size="14" /> {{ confirmText }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped>
.confirm-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.confirm-dialog {
  width: 360px;
  max-width: 90vw;
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  padding: 24px;
  text-align: center;
}
.cd-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto 12px;
  border-radius: 12px;
  background: rgba(224, 88, 79, 0.12);
  color: var(--danger);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cd-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  margin-bottom: 8px;
}
.cd-message {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  line-height: 1.6;
  margin-bottom: 20px;
}
.cd-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}
.cd-danger {
  background: var(--danger);
  color: #fff;
}
.cd-danger:hover {
  background: #c94a42;
}
</style>
