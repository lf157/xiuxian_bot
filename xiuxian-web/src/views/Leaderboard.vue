<script setup lang="ts">
import IcTrophy from '~icons/mdi/trophy-outline'
import IcRefresh from '~icons/mdi/refresh'
import IcMap from '~icons/mdi/map-marker'
import IcRealm from '~icons/mdi/meditation'
import IcSect from '~icons/mdi/temple-buddhist'
import { get } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

type RankMode = 'power' | 'exp' | 'hunt'

interface LeaderboardEntry {
  user_id: string
  name: string
  rank: number
  exp: number
  power: number
  dy_times: number
  realm_name?: string
  current_map?: string
  current_map_name?: string
  sect_name?: string | null
}

const player = usePlayerStore()

const loading = ref(false)
const errorText = ref('')
const activeMode = ref<RankMode>('power')
const entries = ref<LeaderboardEntry[]>([])

const modeDefs: Array<{ value: RankMode; label: string }> = [
  { value: 'power', label: '战力榜' },
  { value: 'exp', label: '修为榜' },
  { value: 'hunt', label: '狩猎榜' },
]

const metricLabel = computed(() => {
  if (activeMode.value === 'exp') return '修为'
  if (activeMode.value === 'hunt') return '狩猎'
  return '战力'
})

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await loadLeaderboard()
})

watch(activeMode, async () => {
  await loadLeaderboard()
})

function metricValue(entry: LeaderboardEntry): string {
  if (activeMode.value === 'exp') return Number(entry.exp || 0).toLocaleString()
  if (activeMode.value === 'hunt') return Number(entry.dy_times || 0).toLocaleString()
  return Number(entry.power || 0).toLocaleString()
}

function rankBadge(index: number) {
  if (index === 0) return '🥇'
  if (index === 1) return '🥈'
  if (index === 2) return '🥉'
  return `#${index + 1}`
}

function realmText(entry: LeaderboardEntry) {
  return entry.realm_name || `境界 ${entry.rank || 1}`
}

function locationText(entry: LeaderboardEntry) {
  return entry.current_map_name || entry.current_map || '未知地带'
}

function sectText(entry: LeaderboardEntry) {
  return entry.sect_name || '散修'
}

async function loadLeaderboard() {
  loading.value = true
  errorText.value = ''
  try {
    const r = await get<{ entries?: LeaderboardEntry[] }>(`/api/leaderboard?mode=${activeMode.value}`)
    entries.value = Array.isArray(r.entries) ? r.entries : []
  } catch (e: any) {
    errorText.value = e?.body?.message || '排行榜加载失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="rank-page">
    <div class="card card--decorated rank-header">
      <div class="rank-header__top">
        <div class="rank-title-wrap">
          <IcTrophy class="icon icon--main" />
          <div>
            <div class="rank-title">玩家排行榜</div>
            <div class="rank-subtitle">展示所在地、境界、所属宗门</div>
          </div>
        </div>
        <button class="btn btn-ghost" :disabled="loading" @click="loadLeaderboard">
          <IcRefresh class="icon" />
          刷新
        </button>
      </div>

      <div class="mode-tabs">
        <button
          v-for="m in modeDefs"
          :key="m.value"
          class="mode-tab"
          :class="{ active: m.value === activeMode }"
          @click="activeMode = m.value"
        >
          {{ m.label }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="card loading-box">
      <div class="loading-spinner"></div>
      <p>榜单更新中…</p>
    </div>

    <div v-else-if="errorText" class="card error-box">
      <h3>排行榜加载失败</h3>
      <p>{{ errorText }}</p>
      <button class="btn btn-primary" @click="loadLeaderboard">重试</button>
    </div>

    <div v-else-if="entries.length === 0" class="card empty-box">
      <p>暂无排行数据</p>
    </div>

    <div v-else class="rank-list">
      <div v-for="(entry, idx) in entries" :key="entry.user_id" class="card rank-item fade-in">
        <div class="rank-item__head">
          <div class="rank-item__badge">{{ rankBadge(idx) }}</div>
          <div class="rank-item__name">{{ entry.name || `修士-${entry.user_id}` }}</div>
          <div class="rank-item__metric">{{ metricLabel }} {{ metricValue(entry) }}</div>
        </div>

        <div class="rank-item__meta">
          <span><IcMap class="icon" />{{ locationText(entry) }}</span>
          <span><IcRealm class="icon" />{{ realmText(entry) }}</span>
          <span><IcSect class="icon" />{{ sectText(entry) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rank-page {
  padding: var(--space-lg);
  padding-bottom: 84px;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.rank-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.rank-header__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
}

.rank-title-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.rank-title {
  font-size: 1.02rem;
  font-weight: 700;
}

.rank-subtitle {
  font-size: 0.72rem;
  color: var(--ink-light);
}

.icon {
  font-size: 1rem;
}

.icon--main {
  font-size: 1.35rem;
}

.mode-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-sm);
}

.mode-tab {
  height: 34px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--paper-deeper);
  background: var(--paper-dark);
  color: var(--ink-mid);
  font-size: 0.8rem;
}

.mode-tab.active {
  background: rgba(184, 134, 11, 0.12);
  color: var(--gold);
  border-color: rgba(184, 134, 11, 0.45);
}

.loading-box,
.error-box,
.empty-box {
  min-height: 160px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-sm);
}

.rank-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.rank-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}

.rank-item__head {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.rank-item__badge {
  min-width: 36px;
  font-size: 0.86rem;
  font-weight: 700;
  color: var(--ink-mid);
}

.rank-item__name {
  flex: 1;
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--ink-black);
}

.rank-item__metric {
  font-size: 0.78rem;
  color: var(--gold);
  white-space: nowrap;
}

.rank-item__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 0.76rem;
  color: var(--ink-mid);
}

.rank-item__meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

@media (min-width: 760px) {
  .rank-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
