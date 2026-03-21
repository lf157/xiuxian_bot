<script setup lang="ts">
const route = useRoute()
const router = useRouter()

const tabs = [
  { path: '/',         icon: '🏯', label: '洞府' },
  { path: '/map',      icon: '🗺️', label: '地图' },
  { path: '/cultivate', icon: '🧘', label: '修炼' },
  { path: '/story',    icon: '📜', label: '仙卷' },
  { path: '/bag',      icon: '🎒', label: '乾坤袋' },
  { path: '/more',     icon: '☯',  label: '更多' },
]

function isActive(path: string) {
  if (path === '/') return route.path === '/'
  if (path === '/more') {
    return route.path === '/more'
      || route.path.startsWith('/relations')
      || route.path.startsWith('/codex')
      || route.path.startsWith('/sect')
      || route.path.startsWith('/leaderboard')
  }
  return route.path.startsWith(path)
}
</script>

<template>
  <nav class="navbar">
    <button
      v-for="tab in tabs" :key="tab.path"
      class="navbar__tab" :class="{ active: isActive(tab.path) }"
      @click="router.push(tab.path)"
    >
      <span class="navbar__icon">{{ tab.icon }}</span>
      <span class="navbar__label">{{ tab.label }}</span>
      <span v-if="isActive(tab.path)" class="navbar__dot"></span>
    </button>
  </nav>
</template>

<style scoped>
.navbar {
  position: fixed; bottom: 0; left: 0; right: 0;
  display: flex;
  background: var(--paper);
  border-top: 1px solid var(--paper-deeper);
  padding-bottom: env(safe-area-inset-bottom, 0);
  z-index: 100;
  box-shadow: 0 -2px 8px rgba(0,0,0,0.04);
}
.navbar__tab {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  gap: 1px; padding: 8px 0 6px;
  color: var(--ink-light);
  transition: color var(--duration-fast);
  position: relative;
  -webkit-tap-highlight-color: transparent;
}
.navbar__tab.active { color: var(--ink-dark); }
.navbar__icon { font-size: 1.2rem; }
.navbar__label { font-size: 0.6rem; font-weight: 600; letter-spacing: 1px; }
.navbar__dot {
  position: absolute; bottom: 4px;
  width: 4px; height: 4px; border-radius: 50%;
  background: var(--cinnabar);
}
</style>
