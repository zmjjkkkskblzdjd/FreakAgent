<script setup>
import DocumentManager from './DocumentManager.vue'
</script>

<template>
  <div class="knowledge-view">
    <DocumentManager />
  </div>
</template>

<style scoped>
.knowledge-view {
  flex: 1;
  min-width: 0;
  display: flex;
  background: var(--bg-base);
}
</style>
