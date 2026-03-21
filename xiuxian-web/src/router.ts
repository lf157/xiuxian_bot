import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('@/views/Home.vue') },
    { path: '/map', component: () => import('@/views/WorldMap.vue') },
    { path: '/cultivate', component: () => import('@/views/Cultivate.vue') },
    { path: '/story', component: () => import('@/views/Story.vue') },
    { path: '/relations', component: () => import('@/views/Relations.vue') },
    { path: '/codex', component: () => import('@/views/Codex.vue') },
    { path: '/sect', component: () => import('@/views/Sect.vue') },
    { path: '/leaderboard', component: () => import('@/views/Leaderboard.vue') },
    { path: '/bag', component: () => import('@/views/Bag.vue') },
    { path: '/more', component: () => import('@/views/More.vue') },
  ],
})

export default router
