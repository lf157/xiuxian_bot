<script setup lang="ts">
import IcSect from '~icons/mdi/temple-buddhist'
import IcRefresh from '~icons/mdi/refresh'
import IcBuff from '~icons/mdi/sword-cross'
import IcLeaf from '~icons/mdi/leaf'
import IcShield from '~icons/mdi/shield'
import IcBattle from '~icons/mdi/lightning-bolt'
import { get } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

interface SectInfo {
  sect_id: string
  name: string
  description?: string
  level?: number
  sect_level?: number
  exp?: number
  max_members?: number
  membership_kind?: string
  role?: string
  branch?: { display_name?: string }
}

interface SectBuffs {
  in_sect?: boolean
  sect_name?: string | null
  membership_kind?: string | null
  branch_name?: string | null
  cultivation_pct?: number
  stat_pct?: number
  battle_reward_pct?: number
}

interface SectListEntry {
  sect_id: string
  name: string
  description?: string
  level?: number
  sect_level?: number
  exp?: number
  max_members?: number
  branch_count?: number
}

const player = usePlayerStore()

const loading = ref(false)
const errorText = ref('')
const mySect = ref<SectInfo | null>(null)
const myBuffs = ref<SectBuffs | null>(null)
const sectList = ref<SectListEntry[]>([])

const inSect = computed(() => !!mySect.value)

const membershipText = computed(() => {
  const kind = myBuffs.value?.membership_kind || mySect.value?.membership_kind
  if (kind === 'branch') return '别院成员'
  if (kind === 'sect') return '宗门直系'
  return '未加入'
})

const roleText = computed(() => {
  const role = String(mySect.value?.role || '').toLowerCase()
  if (role === 'leader') return '宗主'
  if (role === 'deputy') return '副宗主'
  if (role === 'elder') return '长老'
  if (role === 'branch_leader') return '院主'
  if (role === 'branch_member') return '别院弟子'
  if (role) return role
  return '弟子'
})

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await loadSectData()
})

function sectLevel(data: SectInfo | SectListEntry | null): number {
  if (!data) return 1
  return Number(data.level ?? data.sect_level ?? 1)
}

async function loadSectData() {
  if (!player.userId) {
    errorText.value = '未识别到角色，请重新打开 MiniApp'
    return
  }

  loading.value = true
  errorText.value = ''

  try {
    const [memberResp, buffsResp, listResp] = await Promise.all([
      get<{ sect?: SectInfo }>(`/api/sect/member/${player.userId}`).catch((e: any) => {
        if (e?.status === 404) return null
        throw e
      }),
      get<{ buffs?: SectBuffs }>(`/api/sect/buffs/${player.userId}`).catch(() => null),
      get<{ sects?: SectListEntry[] }>('/api/sect/list?limit=20'),
    ])

    mySect.value = memberResp?.sect || null
    myBuffs.value = buffsResp?.buffs || null
    sectList.value = Array.isArray(listResp?.sects) ? listResp.sects : []
  } catch (e: any) {
    errorText.value = e?.body?.message || '宗门信息加载失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="sect-page">
    <div class="card card--decorated sect-header">
      <div class="sect-header__top">
        <div class="sect-title-wrap">
          <IcSect class="icon icon--main" />
          <div>
            <div class="sect-title">宗门</div>
            <div class="sect-subtitle">查看你的宗门状态与宗门列表</div>
          </div>
        </div>
        <button class="btn btn-ghost" :disabled="loading" @click="loadSectData">
          <IcRefresh class="icon" />
          刷新
        </button>
      </div>
    </div>

    <div v-if="loading" class="card loading-box">
      <div class="loading-spinner"></div>
      <p>宗门信息同步中…</p>
    </div>

    <div v-else-if="errorText" class="card error-box">
      <h3>宗门信息加载失败</h3>
      <p>{{ errorText }}</p>
      <button class="btn btn-primary" @click="loadSectData">重试</button>
    </div>

    <template v-else>
      <div v-if="inSect" class="card card--decorated my-sect fade-in">
        <div class="my-sect__head">
          <div class="my-sect__name">{{ mySect?.name }}</div>
          <div class="my-sect__tag">Lv.{{ sectLevel(mySect) }}</div>
        </div>

        <div class="my-sect__meta">
          <span>身份：{{ roleText }}</span>
          <span>归属：{{ membershipText }}</span>
          <span v-if="myBuffs?.branch_name">别院：{{ myBuffs.branch_name }}</span>
        </div>

        <p v-if="mySect?.description" class="my-sect__desc">{{ mySect.description }}</p>

        <div class="buff-grid">
          <div class="buff-item">
            <IcLeaf class="icon" />
            <span>修炼 +{{ Number(myBuffs?.cultivation_pct || 0).toFixed(0) }}%</span>
          </div>
          <div class="buff-item">
            <IcShield class="icon" />
            <span>属性 +{{ Number(myBuffs?.stat_pct || 0).toFixed(0) }}%</span>
          </div>
          <div class="buff-item">
            <IcBattle class="icon" />
            <span>战利品 +{{ Number(myBuffs?.battle_reward_pct || 0).toFixed(0) }}%</span>
          </div>
        </div>
      </div>

      <div v-else class="card my-sect-empty fade-in">
        <p>你当前尚未加入宗门</p>
        <p class="my-sect-empty__tip">可从下方列表查看现有宗门信息</p>
      </div>

      <div class="card sect-list-wrap">
        <div class="sect-list-title">
          <IcBuff class="icon" />
          宗门列表
        </div>

        <div v-if="sectList.length === 0" class="sect-list-empty">暂无宗门数据</div>

        <div v-else class="sect-list">
          <div v-for="row in sectList" :key="row.sect_id" class="sect-list-item">
            <div class="sect-list-item__head">
              <span class="sect-list-item__name">{{ row.name }}</span>
              <span class="sect-list-item__lv">Lv.{{ sectLevel(row) }}</span>
            </div>
            <div class="sect-list-item__meta">
              <span>经验 {{ Number(row.exp || 0).toLocaleString() }}</span>
              <span>上限 {{ Number(row.max_members || 0) }}</span>
              <span>别院 {{ Number(row.branch_count || 0) }}</span>
            </div>
            <p v-if="row.description" class="sect-list-item__desc">{{ row.description }}</p>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sect-page {
  padding: var(--space-lg);
  padding-bottom: 84px;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.sect-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.sect-header__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
}

.sect-title-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.sect-title {
  font-size: 1.02rem;
  font-weight: 700;
}

.sect-subtitle {
  font-size: 0.72rem;
  color: var(--ink-light);
}

.icon {
  font-size: 1rem;
}

.icon--main {
  font-size: 1.35rem;
}

.loading-box,
.error-box,
.my-sect-empty {
  min-height: 150px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-sm);
}

.my-sect-empty__tip {
  color: var(--ink-light);
  font-size: 0.78rem;
}

.my-sect {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.my-sect__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.my-sect__name {
  font-size: 0.96rem;
  font-weight: 700;
}

.my-sect__tag {
  font-size: 0.75rem;
  color: var(--gold);
}

.my-sect__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  font-size: 0.78rem;
  color: var(--ink-mid);
}

.my-sect__desc {
  font-size: 0.78rem;
  color: var(--ink-light);
}

.buff-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-xs);
}

.buff-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  font-size: 0.8rem;
  color: var(--ink-mid);
}

.sect-list-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.sect-list-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  font-size: 0.88rem;
  font-weight: 700;
}

.sect-list-empty {
  color: var(--ink-light);
  font-size: 0.8rem;
}

.sect-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.sect-list-item {
  border: 1px solid var(--paper-deeper);
  border-radius: var(--radius-sm);
  padding: var(--space-sm);
  background: #f7f1e8;
}

.sect-list-item__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-sm);
}

.sect-list-item__name {
  font-size: 0.88rem;
  font-weight: 700;
  color: var(--ink-black);
}

.sect-list-item__lv {
  font-size: 0.74rem;
  color: var(--gold);
}

.sect-list-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  margin-top: 4px;
  font-size: 0.74rem;
  color: var(--ink-mid);
}

.sect-list-item__desc {
  margin-top: 4px;
  font-size: 0.74rem;
  color: var(--ink-light);
}

@media (min-width: 760px) {
  .buff-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .sect-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
