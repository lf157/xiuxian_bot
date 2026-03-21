<script setup lang="ts">
import IcBook from '~icons/mdi/library-shelves'
import IcMonster from '~icons/mdi/paw'
import IcItem from '~icons/mdi/diamond-stone'
import IcRefresh from '~icons/mdi/refresh'
import IcSearch from '~icons/mdi/magnify'
import { get } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

type CodexKind = 'monsters' | 'items'

interface MonsterRecord {
  monster_id: string
  kills: number
  first_seen_at: number
  last_seen_at: number
}

interface ItemRecord {
  item_id: string
  total_obtained: number
  first_seen_at: number
  last_seen_at: number
}

const player = usePlayerStore()

const loading = ref(false)
const errorText = ref('')
const keyword = ref('')
const activeKind = ref<CodexKind>('monsters')
const monsters = ref<MonsterRecord[]>([])
const items = ref<ItemRecord[]>([])

const filteredMonsters = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  if (!key) return monsters.value
  return monsters.value.filter((m) => m.monster_id.toLowerCase().includes(key))
})

const filteredItems = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  if (!key) return items.value
  return items.value.filter((it) => it.item_id.toLowerCase().includes(key))
})

const activeRows = computed(() => {
  return activeKind.value === 'monsters' ? filteredMonsters.value : filteredItems.value
})

const activeCountText = computed(() => {
  if (activeKind.value === 'monsters') {
    const totalKills = filteredMonsters.value.reduce((sum, m) => sum + Number(m.kills || 0), 0)
    return `已收录 ${filteredMonsters.value.length} 只，累计击败 ${totalKills}`
  }

  const totalObtained = filteredItems.value.reduce((sum, it) => sum + Number(it.total_obtained || 0), 0)
  return `已收录 ${filteredItems.value.length} 件，累计获得 ${totalObtained}`
})

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await loadCodex()
})

function fmtTime(ts: number) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function prettyId(raw: string) {
  if (!raw) return '-'
  return raw.replace(/_/g, ' ').replace(/\b\w/g, (s) => s.toUpperCase())
}

async function loadCodex() {
  if (!player.userId) {
    errorText.value = '未识别到角色，请重新打开 MiniApp'
    return
  }

  loading.value = true
  errorText.value = ''

  try {
    const [m, i] = await Promise.all([
      get<{ monsters?: MonsterRecord[] }>(`/api/codex/${player.userId}?kind=monsters`),
      get<{ items?: ItemRecord[] }>(`/api/codex/${player.userId}?kind=items`),
    ])

    monsters.value = Array.isArray(m.monsters) ? m.monsters : []
    items.value = Array.isArray(i.items) ? i.items : []
  } catch (e: any) {
    errorText.value = e?.body?.message || '图鉴加载失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="codex-page">
    <div class="card card--decorated codex-header">
      <div class="codex-header__title-wrap">
        <IcBook class="icon icon--main" />
        <div>
          <div class="codex-header__title">图鉴</div>
          <div class="codex-header__desc">收集你遇见过的怪物与道具</div>
        </div>
      </div>

      <button class="btn btn-ghost" :disabled="loading" @click="loadCodex">
        <IcRefresh class="icon" />
        刷新
      </button>

      <hr class="divider">

      <div class="tabs">
        <button
          class="tab"
          :class="{ active: activeKind === 'monsters' }"
          @click="activeKind = 'monsters'"
        >
          <IcMonster class="icon" />
          怪物
        </button>
        <button
          class="tab"
          :class="{ active: activeKind === 'items' }"
          @click="activeKind = 'items'"
        >
          <IcItem class="icon" />
          道具
        </button>
      </div>

      <div class="search-box">
        <IcSearch class="icon" />
        <input v-model="keyword" type="text" placeholder="按 ID 搜索，例如 wolf / spirit" />
      </div>

      <div class="codex-summary">{{ activeCountText }}</div>
    </div>

    <div v-if="loading" class="card loading-box">
      <div class="loading-spinner"></div>
      <p>图鉴整理中…</p>
    </div>

    <div v-else-if="errorText" class="card error-box">
      <h3>图鉴加载失败</h3>
      <p>{{ errorText }}</p>
      <button class="btn btn-primary" @click="loadCodex">重试</button>
    </div>

    <div v-else-if="activeRows.length === 0" class="card empty-box">
      <p>当前分类暂无记录</p>
      <p class="empty-tip">继续探索和战斗后会逐步解锁</p>
    </div>

    <div v-else class="list-wrap">
      <div
        v-for="row in activeRows"
        :key="activeKind === 'monsters' ? row.monster_id : row.item_id"
        class="card codex-item fade-in"
      >
        <div class="codex-item__top">
          <div class="codex-item__name">
            {{ prettyId(activeKind === 'monsters' ? row.monster_id : row.item_id) }}
          </div>
          <div class="codex-item__count">
            <template v-if="activeKind === 'monsters'">击败 {{ Number((row as MonsterRecord).kills || 0) }}</template>
            <template v-else>获得 {{ Number((row as ItemRecord).total_obtained || 0) }}</template>
          </div>
        </div>

        <div class="codex-item__meta">
          <span>初见 {{ fmtTime(Number(row.first_seen_at || 0)) }}</span>
          <span>最近 {{ fmtTime(Number(row.last_seen_at || 0)) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.codex-page {
  padding: var(--space-lg);
  padding-bottom: 84px;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.codex-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.codex-header__title-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
}

.codex-header__title {
  font-size: 1.02rem;
  font-weight: 700;
}

.codex-header__desc {
  font-size: 0.72rem;
  color: var(--ink-light);
}

.codex-summary {
  font-size: 0.78rem;
  color: var(--ink-mid);
}

.icon {
  font-size: 1rem;
}

.icon--main {
  font-size: 1.35rem;
}

.tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-sm);
}

.tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-xs);
  border: 1px solid var(--paper-deeper);
  border-radius: var(--radius-sm);
  background: var(--paper-dark);
  height: 34px;
  font-size: 0.82rem;
  color: var(--ink-mid);
}

.tab.active {
  background: rgba(184, 134, 11, 0.12);
  color: var(--gold);
  border-color: rgba(184, 134, 11, 0.4);
}

.search-box {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  border: 1px solid var(--paper-deeper);
  border-radius: var(--radius-sm);
  background: #f8f4ed;
  padding: 0 10px;
  height: 36px;
}

.search-box input {
  flex: 1;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink-dark);
  font-size: 0.82rem;
  font-family: var(--font-main);
}

.loading-box,
.error-box,
.empty-box {
  min-height: 180px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-sm);
}

.empty-tip {
  color: var(--ink-light);
  font-size: 0.78rem;
}

.list-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.codex-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}

.codex-item__top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-sm);
}

.codex-item__name {
  font-size: 0.88rem;
  font-weight: 700;
  color: var(--ink-black);
}

.codex-item__count {
  font-size: 0.78rem;
  color: var(--gold);
  white-space: nowrap;
}

.codex-item__meta {
  display: flex;
  justify-content: space-between;
  gap: var(--space-sm);
  font-size: 0.72rem;
  color: var(--ink-light);
}

@media (min-width: 760px) {
  .list-wrap {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
