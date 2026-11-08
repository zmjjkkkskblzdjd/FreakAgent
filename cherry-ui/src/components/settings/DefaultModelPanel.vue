<script setup>
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()
const models = settings.allEnabledModels
</script>

<template>
  <div class="default-panel">
    <div class="panel-head">
      <h2 class="panel-title">默认模型</h2>
      <p class="panel-desc text-muted">选择各项场景使用的默认模型（选项取自已启用的服务商模型），并设置未选择知识库 / 数据源时是否兜底。</p>
    </div>

    <div class="field">
      <label class="field-label">默认对话模型</label>
      <select
        class="input select"
        :value="settings.currentModelKey"
        @change="settings.selectModelByKey($event.target.value)"
      >
        <option v-for="m in settings.routeModels" :key="m.key" :value="m.key">{{ m.providerName }} · {{ m.name }}</option>
        <option v-if="!settings.routeModels.length" value="">未配置（请联系管理员）</option>
      </select>
      <em class="text-muted field-hint">仅列出管理员已配置 API Key 并启用的模型；选择会保存到你自己的账号，对本账号所有对话生效。</em>
      <div v-if="settings.modelPrefError" class="pref-error">{{ settings.modelPrefError }}</div>
    </div>

    <div class="field">
      <label class="field-label">默认翻译模型</label>
      <select class="input select" v-model="settings.defaultTranslateModel">
        <option v-for="m in models" :key="m.id" :value="m.id">{{ m.providerName }} · {{ m.name }}</option>
        <option v-if="!models.length" value="">未配置</option>
      </select>
    </div>

    <div class="field">
      <label class="field-label">检索兜底</label>
      <label class="toggle-row">
        <input
          type="checkbox"
          :checked="settings.kbFallback"
          @change="settings.setKbFallback($event.target.checked)"
        />
        <span>
          未选择知识库时兜底检索默认知识库
          <em class="text-muted">关闭后，不勾选知识库即本轮不做文档检索</em>
        </span>
      </label>
      <label class="toggle-row">
        <input
          type="checkbox"
          :checked="settings.datasourceFallback"
          @change="settings.setDatasourceFallback($event.target.checked)"
        />
        <span>
          未选择数据源时兜底查询默认分析库
          <em class="text-muted">关闭后，不选数据源即本轮不做业务库查询</em>
        </span>
      </label>
    </div>

    <div v-if="!models.length" class="hint">
      当前没有可用模型：请让管理员在「模型服务」中启用服务商、填入 API Key 并勾选模型。
    </div>
  </div>
</template>

<style scoped>
.default-panel {
  padding: 20px 24px 40px;
  max-width: 640px;
}
.panel-head {
  margin-bottom: 16px;
}
.panel-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  margin: 0;
}
.panel-desc {
  font-size: var(--font-size-sm);
  margin-top: 4px;
}
.field {
  margin-bottom: 18px;
}
.field-label {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  margin-bottom: 6px;
  font-weight: 500;
}
.select {
  height: 36px;
  cursor: pointer;
}
.field-hint {
  display: block;
  font-style: normal;
  font-size: var(--font-size-xs);
  margin-top: 4px;
}
.pref-error {
  margin-top: 6px;
  font-size: var(--font-size-xs);
  color: var(--danger, #dc2626);
}
.toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  cursor: pointer;
  margin-bottom: 8px;
}
.toggle-row input {
  margin-top: 2px;
}
.toggle-row em {
  display: block;
  font-style: normal;
  font-size: var(--font-size-xs);
  margin-top: 2px;
}
.hint {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--font-size-xs);
}
</style>
