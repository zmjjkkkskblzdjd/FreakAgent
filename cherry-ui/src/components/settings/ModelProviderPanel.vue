<script setup>
import { computed } from 'vue'
import ProviderSetupCard from '@/components/onboarding/ProviderSetupCard.vue'
import CustomProviderCard from './CustomProviderCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const auth = useAuthStore()
const settings = useSettingsStore()



const visibleProviders = computed(() => (
  auth.isDbAdmin
    ? settings.providers
    : settings.providers.filter((p) =>
        settings.modelPrefOptions.some((o) => o.provider_id === p.id))
))
</script>

<template>
  <div class="provider-panel">
    <div class="panel-head">
      <div>
        <h2 class="panel-title">模型服务</h2>
        <p class="panel-desc text-muted">
          <template v-if="auth.isDbAdmin">
            内置目录仅作参考；点「拉取模型」可按已保存的 Key 获取服务商最新列表，也可手动添加模型名。
            勾选的模型会写入租户路由，成员即可在其中自由选择。
          </template>
          <template v-else>
            以下服务商由管理员统一配置（API Key 不开放在成员端）。勾选一个模型即设为你自己的默认模型，
            只对本账号生效。
          </template>
        </p>
      </div>
    </div>

    <div class="provider-grid">
      <ProviderSetupCard
        v-for="p in visibleProviders"
        :key="p.id"
        :provider="p"
      />
      <CustomProviderCard v-if="auth.isDbAdmin" />
    </div>

    <div v-if="!visibleProviders.length" class="hint-box">
      暂无可用模型：请让管理员在「模型服务」中启用服务商、填入 API Key 并勾选模型。
    </div>
  </div>
</template>

<style scoped>
.provider-panel {
  padding: 20px 24px 40px;
}
.panel-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
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
  max-width: 720px;
}
.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 14px;
}
.hint-box {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--font-size-xs);
}
</style>
