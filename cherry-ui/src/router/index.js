import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginPage.vue'),
    meta: { public: true }
  },
  {
    path: '/admin/login',
    name: 'adminLogin',
    component: () => import('@/views/AdminLoginPage.vue'),
    meta: { public: true }
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/RegisterPage.vue'),
    meta: { public: true }
  },
  {
    path: '/onboarding',
    name: 'onboarding',
    component: () => import('@/views/OnboardingPage.vue')
  },
  {
    path: '/',
    component: () => import('@/components/layout/AppLayout.vue'),
    children: [
      { path: '', redirect: '/chat' },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/ChatPage.vue')
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgePage.vue')
      },
      {
        path: 'business',
        name: 'business',
        component: () => import('@/views/BusinessPage.vue')
      },
      {
        path: 'datasources',
        name: 'datasources',
        component: () => import('@/views/DataSourcePage.vue')
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/SettingsPage.vue')
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  
  if (!auth.initialized) await auth.fetchMe()

  if (!auth.isLoggedIn) {
    return to.meta.public ? true : { name: 'login', query: { redirect: to.fullPath } }
  }
  
  if (to.name === 'adminLogin') {
    return (auth.isDbAdmin || auth.isKbAdmin)
      ? { name: 'settings' }
      : { name: 'login' }
  }
  if (to.meta.public) return { name: 'chat' }

  
  const settings = useSettingsStore()
  if (!settings.onboardingLoaded) settings.loadOnboarding()
  
  if (!settings.providersLoaded) await settings.loadProviders()
  if (!settings.onboardingCompleted && to.name !== 'onboarding') {
    return { name: 'onboarding' }
  }
  if (settings.onboardingCompleted && to.name === 'onboarding') {
    return { name: 'chat' }
  }
  return true
})

export default router
