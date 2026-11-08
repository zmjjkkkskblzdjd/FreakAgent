<script setup>
import { onMounted } from 'vue'
import KnowledgeList from '@/components/knowledge/KnowledgeList.vue'
import KnowledgeView from '@/components/knowledge/KnowledgeView.vue'
import { useKnowledgeStore } from '@/stores/knowledge'

const knowledge = useKnowledgeStore()

onMounted(() => knowledge.load())
</script>

<template>
  <div class="knowledge-page">
    <KnowledgeList />
    <KnowledgeView />
  </div>
</template>

<style scoped>
.knowledge-page {
  display: flex;
  height: 100%;
  width: 100%;
}
</style>
