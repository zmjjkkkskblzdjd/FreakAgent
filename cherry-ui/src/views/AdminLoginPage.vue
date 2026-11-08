<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
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
  const account = form.account.trim()
  try {
    await auth.login({ account, password: form.password })
    
    if (!auth.isDbAdmin && !auth.isKbAdmin) {
      await auth.logout()
      error.value = `账号「${account}」没有管理权限（需数据库管理或知识库管理），已自动退出。`
      return
    }
    router.replace({ name: 'settings' })
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
        <span class="brand-dot admin"></span>
        <div>
          <h1>AllFusion</h1>
          <p class="text-muted">管理端登录</p>
        </div>
        <span class="admin-badge">ADMIN</span>
      </div>

      <p class="hint text-muted">
        仅限拥有「数据库管理」或「知识库管理」域的账号登录；普通成员请使用普通登录入口。
      </p>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span>邮箱或用户名</span>
          <input v-model="form.account" class="input" autocomplete="username"
                 placeholder="管理员账号" autofocus />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="form.password" class="input" type="password"
                 autocomplete="current-password" placeholder="请输入密码" />
        </label>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="submitting">
          <Icon name="key" :size="16" />
          {{ submitting ? '验证管理权限…' : '登录管理端' }}
        </button>
      </form>

      <p class="switch">
        不是管理员？
        <router-link :to="{ name: 'login' }">返回普通登录</router-link>
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
  max-width: 400px;
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
  margin-bottom: 14px;
}
.brand-dot {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent), #0fa3a0);
  box-shadow: var(--shadow-sm);
  flex-shrink: 0;
}
.brand-dot.admin {
  background: linear-gradient(135deg, #1d4ed8, #0ea5e9);
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
.admin-badge {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid #1d4ed8;
  color: #1d4ed8;
  font-size: 10px;
  letter-spacing: 0.08em;
  font-weight: 600;
}
.hint {
  margin: 0 0 18px;
  font-size: var(--font-size-xs);
  line-height: 1.6;
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
</style>
