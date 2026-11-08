import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/http'
import { useAuthStore } from './stores/auth'
import './assets/styles/variables.css'
import './assets/styles/themes.css'
import './assets/styles/base.css'

const pinia = createPinia()
const app = createApp(App)
app.use(pinia)
app.use(router)


const auth = useAuthStore(pinia)
setUnauthorizedHandler(() => {
  auth.clear()
  if (router.currentRoute.value.name !== 'login') {
    router.replace({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

app.mount('#app')
