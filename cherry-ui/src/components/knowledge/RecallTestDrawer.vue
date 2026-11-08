<script setup>
import { computed, ref } from 'vue'
import Icon from '@/components/common/Icon.vue'
import { searchKb } from '@/api/kb'

const props = defineProps({
  kb: { type: Object, required: true }
})
const emit = defineEmits(['close'])

const query = ref('')
const topK = ref(3)
const threshold = ref(0.5)
const searching = ref(false)
const results = ref(null)
const error = ref('')

async function runSearch() {
  if (!query.value.trim() || searching.value) return
  searching.value = true
  results.value = null
  error.value = ''
  try {
    const { results: hits } = await searchKb(props.kb.id, {
      query: query.value.trim(),
      topK: topK.value,
      threshold: threshold.value
    })
    results.value = hits.map((h, i) => ({
      docName: h.metadata?.source || 'unknown',
      chunkIdx: i + 1,
      hitType: h.metadata?.hit_type || '',
      score: h.score,
      text: h.content || '（空分块）'
    }))
  } catch (e) {
    error.value = e.message
    results.value = []
  } finally {
    searching.value = false
  }
}

const hasQuery = computed(() => query.value.trim().length > 0)
</script>

<template>
  <teleport to="body">
    <div class="recall-mask" @click.self="emit('close')">
      <aside class="recall-drawer">
        <header class="rd-head">
          <div class="title">
            <Icon name="sparkles" :size="16" />
            <span>召回测试 · {{ kb.name }}</span>
          </div>
          <button class="close" @click="emit('close')"><Icon name="close" :size="16" /></button>
        </header>

        <div class="rd-body scroll">
          <div class="field">
            <label class="field-label">测试查询</label>
            <textarea
              v-model="query"
              class="input textarea"
              rows="3"
              placeholder="输入一句自然语言查询，回车或点击「测试」"
              @keydown.enter.exact.prevent="runSearch"
            />
          </div>

          <div class="params">
            <div class="param">
              <label class="field-label">Top-K</label>
              <input class="input" type="number" v-model.number="topK" min="1" max="20" />
            </div>
            <div class="param">
              <label class="field-label">相似度阈值</label>
              <input class="input" type="number" v-model.number="threshold" min="0" max="1" step="0.05" />
            </div>
            <button class="btn btn-primary run-btn" :disabled="!hasQuery || searching" @click="runSearch">
              <span v-if="searching" class="spin"><Icon name="refresh" :size="14" /></span>
              <span v-else><Icon name="sparkles" :size="14" /></span>
              {{ searching ? '检索中…' : '测试' }}
            </button>
          </div>

          <div class="result-area">
            <div v-if="searching" class="hint">
              <Icon name="refresh" :size="18" class="spin" />
              <span>正在检索…</span>
            </div>
            <div v-else-if="results === null" class="hint">
              <Icon name="search" :size="18" />
              <span>输入查询后点击「测试」开始召回</span>
            </div>
            <div v-else-if="error" class="hint error">
              <Icon name="close" :size="18" />
              <span>{{ error }}</span>
            </div>
            <div v-else-if="!results.length" class="hint">
              <Icon name="close" :size="18" />
              <span>无命中（相似度低于阈值 {{ threshold }}）</span>
            </div>
            <div v-else class="results">
              <div class="result-summary text-muted">命中 {{ results.length }} 条分块</div>
              <div v-for="(r, i) in results" :key="i" class="result-item">
                <div class="r-head">
                  <span class="r-idx">#{{ i + 1 }}</span>
                  <span class="r-doc">{{ r.docName }} · 分块 {{ r.chunkIdx }}</span>
                  <span v-if="r.hitType" class="r-type">{{ r.hitType }}</span>
                  <span class="r-score" :class="{ high: r.score >= 0.8, mid: r.score >= 0.6 && r.score < 0.8 }">
                    {{ r.score }}
                  </span>
                </div>
                <div class="r-text">{{ r.text }}</div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </teleport>
</template>

<style scoped>
.recall-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 100;
}
.recall-drawer {
  width: 560px;
  max-width: 92vw;
  height: 100vh;
  background: var(--bg-base);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
}
.rd-head {
  height: 56px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
}
.title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--accent);
}
.close {
  width: 30px;
  height: 30px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.rd-body {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}
.field {
  margin-bottom: 14px;
}
.field-label {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-bottom: 6px;
  font-weight: 500;
}
.textarea {
  width: 100%;
  resize: vertical;
  font-family: var(--font-family);
  line-height: 1.6;
}
.params {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 10px;
  align-items: end;
  margin-bottom: 18px;
}
.param .input {
  height: 34px;
}
.run-btn {
  height: 34px;
  padding: 0 18px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.run-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.spin {
  display: inline-flex;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.result-area {
  border-top: 1px solid var(--border);
  padding-top: 14px;
}
.hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 20px;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
}
.hint.error {
  color: var(--danger);
}
.result-summary {
  font-size: var(--font-size-xs);
  margin-bottom: 10px;
}
.result-item {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  margin-bottom: 10px;
}
.r-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: var(--font-size-xs);
}
.r-idx {
  color: var(--accent);
  font-weight: 700;
}
.r-doc {
  flex: 1;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.r-score {
  font-family: ui-monospace, monospace;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--bg-active);
  color: var(--text-secondary);
}
.r-type {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
}
.r-score.mid {
  background: rgba(245, 166, 35, 0.14);
  color: var(--warning);
}
.r-score.high {
  background: rgba(43, 182, 115, 0.14);
  color: var(--success);
}
.r-text {
  font-size: var(--font-size-xs);
  line-height: 1.6;
  color: var(--text-secondary);
}
</style>
