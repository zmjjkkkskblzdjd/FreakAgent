<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import ProviderSetupCard from './ProviderSetupCard.vue'
import CustomProviderCard from '@/components/settings/CustomProviderCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const router = useRouter()
const auth = useAuthStore()
const settings = useSettingsStore()
const step = ref('welcome')


const canConfig = computed(() => auth.isDbAdmin)

function finish(skip) {
  settings.completeOnboarding(skip)
  router.push('/chat')
}
</script>

<template>
  <div class="onboarding">
    
    <div v-if="step === 'welcome'" class="welcome">
      <div class="logo-big">
        <Icon name="sparkles" :size="36" />
      </div>
      <h1>欢迎使用 Cherry UI</h1>
      <p v-if="canConfig" class="subtitle text-secondary">
        自然语言问数助手。开始前请先配置至少一个模型服务商，<br />
        也可稍后在「设置」中补充。当前仅支持国产模型。
      </p>
      <p v-else class="subtitle text-secondary">
        自然语言问数助手。模型服务由管理员统一配置，<br />
        你可以直接开始使用，稍后在「设置 → 默认模型」里选择自己使用的模型。
      </p>
      <div class="welcome-actions">
        <button v-if="canConfig" class="btn btn-primary" @click="step = 'setup'">
          <Icon name="settings" :size="16" /> 开始配置
        </button>
        <button v-else class="btn btn-primary" @click="finish(false)">
          <Icon name="check" :size="16" /> 开始使用
        </button>
        <button v-if="canConfig" class="btn btn-text" @click="finish(true)">
          跳过 <Icon name="skip" :size="14" />
        </button>
      </div>
    </div>

    
    <div v-else class="setup">
      <header class="setup-head">
        <div>
          <h2>配置模型服务商</h2>
          <p class="text-muted">勾选要启用的服务商，填入 API Key 并选择模型</p>
        </div>
        <button class="btn btn-text" @click="step = 'welcome'">
          <Icon name="chevron-right" :size="14" style="transform: rotate(180deg)" /> 返回
        </button>
      </header>

      <div class="card-grid scroll">
        <ProviderSetupCard
          v-for="p in settings.providers"
          :key="p.id"
          :provider="p"
        />
        <CustomProviderCard />
      </div>

      <footer class="setup-foot">
        <span class="text-muted">
          已启用 {{ settings.enabledProviders.length }} 家服务商
        </span>
        <div class="foot-actions">
          <button class="btn btn-ghost" @click="finish(true)">跳过</button>
          <button class="btn btn-primary" @click="finish(false)">
            <Icon name="check" :size="16" /> 完成
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.onboarding {
  min-height: 100vh;
  background: var(--bg-base);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
}


.welcome {
  text-align: center;
  max-width: 520px;
}
.logo-big {
  width: 76px;
  height: 76px;
  border-radius: 20px;
  background: linear-gradient(135deg, var(--accent), #0fa3a0);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 24px;
  box-shadow: var(--shadow-lg);
}
.welcome h1 {
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 12px;
}
.subtitle {
  font-size: var(--font-size-base);
  line-height: 1.7;
  margin-bottom: 32px;
}
.welcome-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}


.setup {
  width: 100%;
  max-width: 920px;
  height: 86vh;
  display: flex;
  flex-direction: column;
}
.setup-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
}
.setup-head h2 {
  font-size: 20px;
  font-weight: 700;
}
.card-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 16px;
  align-content: start;
  padding-right: 4px;
}
.setup-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 20px;
  margin-top: 12px;
  border-top: 1px solid var(--border);
}
.foot-actions {
  display: flex;
  gap: 12px;
}
</style>
