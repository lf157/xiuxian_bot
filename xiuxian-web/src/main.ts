import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './styles/global.css'

import { usePlayerStore } from './stores/player'
import { getTwaUser, resolveOrCreatePlayerIdByTelegram } from './api/client'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

// ── Bootstrap: detect user from Telegram WebApp ──
const twaUser = getTwaUser()
if (twaUser) {
  const player = usePlayerStore()
  ;(async () => {
    try {
      const userId = await resolveOrCreatePlayerIdByTelegram()
      if (!userId) return
      player.setUserId(userId)
      await player.init()
    } catch (err) {
      console.error('[bootstrap] failed to resolve player id', err)
    }
  })()
}
