<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/components/common/Icon.vue'
import { getCaptcha } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const form = reactive({
  email: '',
  username: '',
  password: '',
  confirm: '',
  captchaCode: ''
})
const captcha = reactive({ enabled: false, captchaId: '', svg: '' })
const submitting = ref(false)
const error = ref('')

async function refreshCaptcha() {
  try {
    const data = await getCaptcha()
    captcha.enabled = !!data?.enabled
    captcha.captchaId = data?.captcha_id || ''
    captcha.svg = data?.svg || ''
    form.captchaCode = ''
  } catch {
    captcha.enabled = false
  }
}

onMounted(refreshCaptcha)

async function submit() {
  error.value = ''
  if (!form.email.trim() || !form.username.trim()) {
    error.value = '请填写邮箱与用户名'
    return
  }
  if (form.password.length < 8) {
    error.value = '密码至少 8 位'
    return
  }
  if (form.password !== form.confirm) {
    error.value = '两次输入的密码不一致'
    return
  }
  if (captcha.enabled && !form.captchaCode.trim()) {
    error.value = '请填写验证码'
    return
  }
  submitting.value = true
  try {
    await auth.register({
      email: form.email.trim(),
      username: form.username.trim(),
      password: form.password,
      captcha_id: captcha.captchaId || null,
      captcha_code: form.captchaCode || null
    })
    router.replace('/chat')
  } catch (e) {
    error.value = e.message || '注册失败'
    refreshCaptcha()
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
          <p class="text-muted">注册后加入现有工作空间（普通成员）</p>
        </div>
      </div>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span>邮箱</span>
          <input v-model="form.email" class="input" type="email"
                 autocomplete="email" placeholder="you@example.com" autofocus />
        </label>
        <label class="field">
          <span>用户名</span>
          <input v-model="form.username" class="input"
                 autocomplete="username" placeholder="2–32 位" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="form.password" class="input" type="password"
                 autocomplete="new-password" placeholder="至少 8 位，字母/数字/符号两类以上" />
        </label>
        <label class="field">
          <span>确认密码</span>
          <input v-model="form.confirm" class="input" type="password"
                 autocomplete="new-password" placeholder="再输入一次" />
        </label>

        <div v-if="captcha.enabled" class="captcha-row">
          <label class="field captcha-input">
            <span>验证码</span>
            <input v-model="form.captchaCode" class="input" placeholder="计算结果"
                   inputmode="numeric" />
          </label>
          <button type="button" class="captcha-img" title="点击刷新验证码"
                  @click="refreshCaptcha" v-html="captcha.svg"></button>
        </div>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="submitting">
          <Icon name="sparkles" :size="16" />
          {{ submitting ? '创建中…' : '注册并登录' }}
        </button>
      </form>

      <p class="switch">
        已有账号？
        <router-link :to="{ name: 'login' }">直接登录</router-link>
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
  margin-bottom: 20px;
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
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
}
.captcha-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}
.captcha-input {
  flex: 1;
}
.captcha-img {
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #f8fafc;
  cursor: pointer;
  line-height: 0;
  overflow: hidden;
}
.captcha-img :deep(svg) {
  display: block;
  height: 44px;
  width: 140px;
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
