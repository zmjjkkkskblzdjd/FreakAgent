<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = reactive({ account: '', password: '' })
const submitting = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  if (!form.account.trim() || !form.password) {
    error.value = '请输入账号与密码'
    return
  }
  submitting.value = true
  try {
    await auth.login({ account: form.account.trim(), password: form.password })
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/chat'
    router.replace(redirect)
  } catch (e) {
    error.value = e.message || '登录失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="brand">
        <span class="brand-dot"></span>
        <div>
          <h1>AllFusion</h1>
          <p class="text-muted">自然语言问数 · 登录</p>
        </div>
      </div>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span>邮箱或用户名</span>
          <input v-model="form.account" class="input" autocomplete="username"
                 placeholder="you@example.com" autofocus />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="form.password" class="input" type="password"
                 autocomplete="current-password" placeholder="请输入密码" />
        </label>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="submitting">
          <Icon name="key" :size="16" />
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="switch">
        还没有账号？
        <router-link :to="{ name: 'register' }">注册新账号</router-link>
      </p>
      <p class="switch admin-switch">
        <router-link :to="{ name: 'adminLogin' }">管理员登录</router-link>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(900px 500px at 15% 0%, var(--accent-soft), transparent 60%),
    radial-gradient(700px 420px at 95% 100%, rgba(19, 181, 177, 0.10), transparent 60%),
    var(--bg-base);
}
.auth-card {
  width: 100%;
  max-width: 380px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 28px 26px 22px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}
.brand-dot {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent), #0fa3a0);
  box-shadow: var(--shadow-sm);
}
.brand h1 {
  font-size: var(--font-size-xl);
  margin: 0 0 2px;
  color: var(--text-primary);
}
.brand p {
  margin: 0;
  font-size: var(--font-size-xs);
}
.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.submit {
  margin-top: 4px;
  height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.error {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--danger);
}
.switch {
  margin: 18px 0 0;
  text-align: center;
  font-size: var(--font-size-sm);
  color: var(--text-muted);
}
.switch a {
  color: var(--accent);
}
.admin-switch {
  margin-top: 8px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  font-size: var(--font-size-xs);
}
</style>
