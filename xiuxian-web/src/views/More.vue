<script setup lang="ts">
import IcBook from '~icons/mdi/book-open-page-variant'
import IcNetwork from '~icons/mdi/account-network'
import IcCodex from '~icons/mdi/library-shelves'
import IcSect from '~icons/mdi/temple-buddhist'
import IcRank from '~icons/mdi/trophy-outline'
import IcPlay from '~icons/mdi/play-circle-outline'
import IcStop from '~icons/mdi/stop-circle-outline'
import IcRefresh from '~icons/mdi/refresh'
import IcSparkles from '~icons/mdi/sparkles'
import { get, post } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

interface QuickEntry {
  path: string
  title: string
  desc: string
  icon: any
}

interface GainPulse {
  id: number
  text: string
}

interface CultivateStatusResp {
  state?: boolean
  start_time?: number
  current_gain?: number
  hours?: number
  tip?: string
}

interface CultivateStartResp {
  success?: boolean
  start_time?: number
  gain_per_hour?: number
  message?: string
}

interface CultivateEndResp {
  success?: boolean
  gain?: number
  hours?: number
  tip?: string
  message?: string
}

const router = useRouter()
const player = usePlayerStore()

const quickEntries: QuickEntry[] = [
  {
    path: '/story',
    title: '功能剧情',
    desc: '查看章节并继续推进主线',
    icon: IcBook,
  },
  {
    path: '/relations',
    title: '人物关系图',
    desc: '查看角色关系与阵营连接',
    icon: IcNetwork,
  },
  {
    path: '/codex',
    title: '图鉴',
    desc: '收集怪物与道具的发现记录',
    icon: IcCodex,
  },
  {
    path: '/sect',
    title: '宗门',
    desc: '查看宗门状态、加成与宗门列表',
    icon: IcSect,
  },
  {
    path: '/leaderboard',
    title: '玩家排行榜',
    desc: '按所在地、境界、所属宗门查看排名',
    icon: IcRank,
  },
]

const loadingStatus = ref(false)
const acting = ref(false)
const cultivating = ref(false)
const startTime = ref(0)
const elapsed = ref(0)
const gainShown = ref(0)
const gainFloat = ref(0)
const gainPerSecond = ref(0)
const statusTip = ref('')

const gainPulses = ref<GainPulse[]>([])
let gainPulseSeq = 0

const lastSettlement = ref<{ gain: number; hours: number; tip: string }>({
  gain: 0,
  hours: 0,
  tip: '',
})
const hasSettlement = ref(false)

let tickTimer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

const cultivateStateText = computed(() => (cultivating.value ? '修炼中' : '未修炼'))

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await refreshCultivateStatus()
  startTicking()
  startPolling()
})

onUnmounted(() => {
  if (tickTimer) clearInterval(tickTimer)
  if (pollTimer) clearInterval(pollTimer)
})

function goTo(path: string) {
  router.push(path)
}

function currentTs() {
  return Math.floor(Date.now() / 1000)
}

function fmtTime(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const sec = seconds % 60
  if (h > 0) return `${h}时${m}分${sec}秒`
  if (m > 0) return `${m}分${sec}秒`
  return `${sec}秒`
}

function emitGainPulse(delta: number) {
  if (!Number.isFinite(delta) || delta <= 0) return

  if (delta <= 6) {
    for (let i = 0; i < delta; i += 1) {
      pushPulse('+1')
    }
    return
  }

  for (let i = 0; i < 6; i += 1) {
    pushPulse('+1')
  }
  pushPulse(`+${delta - 6}`)
}

function pushPulse(text: string) {
  const id = ++gainPulseSeq
  gainPulses.value.push({ id, text })
  setTimeout(() => {
    gainPulses.value = gainPulses.value.filter((p) => p.id !== id)
  }, 900)
}

function startTicking() {
  if (tickTimer) clearInterval(tickTimer)
  tick()
  tickTimer = setInterval(tick, 1000)
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(() => {
    if (!cultivating.value || acting.value) return
    refreshCultivateStatus()
  }, 4000)
}

function tick() {
  if (!cultivating.value || !startTime.value) return

  elapsed.value = Math.max(0, currentTs() - startTime.value)

  if (gainPerSecond.value <= 0) return
  gainFloat.value += gainPerSecond.value

  const whole = Math.floor(gainFloat.value)
  if (whole <= gainShown.value) return

  const delta = whole - gainShown.value
  gainShown.value = whole
  emitGainPulse(delta)
}

function resetCultivatePreview() {
  cultivating.value = false
  startTime.value = 0
  elapsed.value = 0
  gainShown.value = 0
  gainFloat.value = 0
  gainPerSecond.value = 0
  gainPulses.value = []
}

function applyStatusPayload(r: CultivateStatusResp) {
  const active = !!r?.state
  if (!active) {
    resetCultivatePreview()
    statusTip.value = '当前未修炼，可随时入定。'
    return
  }

  cultivating.value = true
  startTime.value = Number(r.start_time || currentTs())
  elapsed.value = Math.max(0, currentTs() - startTime.value)

  const serverGain = Math.max(0, Number(r.current_gain || 0))
  if (serverGain > gainShown.value) {
    emitGainPulse(serverGain - gainShown.value)
  }
  gainShown.value = serverGain
  gainFloat.value = serverGain

  const hours = Math.max(0, Number(r.hours || 0))
  if (hours > 0 && serverGain >= 0) {
    gainPerSecond.value = serverGain / Math.max(1, hours * 3600)
  } else if (!gainPerSecond.value) {
    gainPerSecond.value = 200 / 3600
  }

  statusTip.value = String(r.tip || '灵气运转稳定。')
}

async function refreshCultivateStatus() {
  if (!player.userId) return
  loadingStatus.value = true
  try {
    const r = await get<CultivateStatusResp>(`/api/cultivate/status/${player.userId}`)
    applyStatusPayload(r)
  } catch (e: any) {
    statusTip.value = e?.body?.message || '修炼状态读取失败'
  } finally {
    loadingStatus.value = false
  }
}

async function startCultivate() {
  if (!player.userId || acting.value) return
  acting.value = true
  try {
    const r = await post<CultivateStartResp>('/api/cultivate/start', { user_id: player.userId })
    if (r?.success === false) {
      alert(r.message || '无法开始修炼')
      return
    }

    hasSettlement.value = false
    cultivating.value = true
    startTime.value = Number(r.start_time || currentTs())
    elapsed.value = 0
    gainShown.value = 0
    gainFloat.value = 0
    gainPulses.value = []
    gainPerSecond.value = Math.max(0, Number(r.gain_per_hour || 200)) / 3600
    statusTip.value = '已入定，灵气正在汇聚。'
  } catch (e: any) {
    alert(e?.body?.message || '修炼失败')
  } finally {
    acting.value = false
  }
}

async function endCultivate() {
  if (!player.userId || acting.value || !cultivating.value) return
  acting.value = true
  try {
    const r = await post<CultivateEndResp>('/api/cultivate/end', { user_id: player.userId })
    if (r?.success === false) {
      alert(r.message || '出关失败')
      return
    }

    lastSettlement.value = {
      gain: Number(r.gain || 0),
      hours: Number(r.hours || 0),
      tip: String(r.tip || ''),
    }
    hasSettlement.value = true

    resetCultivatePreview()
    statusTip.value = '已出关，修为已结算。'
    await player.init(true)
  } catch (e: any) {
    alert(e?.body?.message || '出关失败')
    await refreshCultivateStatus()
  } finally {
    acting.value = false
  }
}
</script>

<template>
  <div class="more-page">
    <section class="card card--decorated more-section">
      <h2 class="section-title">更多功能</h2>
      <p class="section-subtitle">剧情、关系图、图鉴、宗门与排行榜都在这里</p>

      <div class="feature-grid">
        <button
          v-for="entry in quickEntries"
          :key="entry.path"
          class="feature-item"
          @click="goTo(entry.path)"
        >
          <component :is="entry.icon" class="feature-item__icon" />
          <div class="feature-item__text">
            <div class="feature-item__title">{{ entry.title }}</div>
            <div class="feature-item__desc">{{ entry.desc }}</div>
          </div>
        </button>
      </div>
    </section>

    <section class="card card--decorated more-section cultivate-quick">
      <div class="cultivate-quick__header">
        <h2 class="section-title">修炼控制</h2>
        <button class="btn btn-ghost" :disabled="loadingStatus || acting" @click="refreshCultivateStatus">
          <IcRefresh class="inline-icon" />
          刷新
        </button>
      </div>

      <div class="cultivate-quick__state">
        <span class="state-pill" :class="cultivating ? 'state-pill--active' : ''">{{ cultivateStateText }}</span>
        <span class="state-tip">{{ statusTip }}</span>
      </div>

      <div v-if="cultivating" class="cultivate-metrics fade-in">
        <div class="metric-row">
          <span>已修炼</span>
          <span class="metric-value">{{ fmtTime(elapsed) }}</span>
        </div>
        <div class="metric-row metric-row--gain">
          <span>实时修为</span>
          <span class="metric-value text-gold">+{{ gainShown.toLocaleString() }}</span>
          <transition-group name="gain-pop" tag="div" class="gain-pop-list">
            <span v-for="p in gainPulses" :key="p.id" class="gain-pop">{{ p.text }}</span>
          </transition-group>
        </div>
      </div>

      <div class="cultivate-actions">
        <button class="btn btn-primary" :disabled="acting || cultivating" @click="startCultivate">
          <IcPlay class="inline-icon" />
          修炼开始
        </button>
        <button class="btn btn-cinnabar" :disabled="acting || !cultivating" @click="endCultivate">
          <IcStop class="inline-icon" />
          修炼停止
        </button>
      </div>

      <div v-if="hasSettlement" class="settlement fade-in">
        <div class="settlement__title">
          <IcSparkles class="inline-icon" />
          最近一次出关
        </div>
        <div class="metric-row">
          <span>获得修为</span>
          <span class="text-gold">+{{ lastSettlement.gain.toLocaleString() }}</span>
        </div>
        <div class="metric-row">
          <span>修炼时长</span>
          <span>{{ lastSettlement.hours.toFixed(2) }} 时辰</span>
        </div>
        <p v-if="lastSettlement.tip" class="settlement__tip">{{ lastSettlement.tip }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.more-page {
  padding: var(--space-lg);
  padding-bottom: 86px;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.more-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.section-title {
  font-size: 1.05rem;
}

.section-subtitle {
  margin-top: -4px;
  font-size: 0.76rem;
  color: var(--ink-light);
}

.feature-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-sm);
}

.feature-item {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  background: var(--paper-dark);
  border: 1px solid var(--paper-deeper);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  text-align: left;
  transition: background var(--duration-fast) var(--ease-out);
}

.feature-item:active {
  background: #e1d6c6;
}

.feature-item__icon {
  font-size: 1.4rem;
  color: var(--ink-dark);
  flex-shrink: 0;
}

.feature-item__title {
  font-size: 0.9rem;
  color: var(--ink-black);
  font-weight: 600;
}

.feature-item__desc {
  font-size: 0.72rem;
  color: var(--ink-light);
}

.cultivate-quick__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
}

.cultivate-quick__state {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.state-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 68px;
  height: 24px;
  border-radius: 999px;
  background: var(--paper-dark);
  color: var(--ink-mid);
  font-size: 0.75rem;
  font-weight: 600;
}

.state-pill--active {
  background: rgba(74, 140, 111, 0.18);
  color: var(--jade);
}

.state-tip {
  font-size: 0.78rem;
  color: var(--ink-light);
}

.cultivate-metrics {
  border: 1px dashed var(--paper-shadow);
  border-radius: var(--radius-sm);
  padding: var(--space-sm) var(--space-md);
}

.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 0.84rem;
}

.metric-value {
  font-family: var(--font-mono);
  font-weight: 700;
}

.metric-row--gain {
  position: relative;
}

.gain-pop-list {
  position: absolute;
  right: -2px;
  top: -10px;
  display: flex;
  gap: 4px;
  pointer-events: none;
}

.gain-pop {
  color: var(--gold);
  font-size: 0.72rem;
  font-weight: 700;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.7);
}

.gain-pop-enter-active {
  animation: gain-pop-up 0.85s ease-out;
}

.gain-pop-leave-active {
  transition: opacity 0.2s;
}

.gain-pop-leave-to {
  opacity: 0;
}

@keyframes gain-pop-up {
  0% {
    transform: translateY(8px);
    opacity: 0;
  }
  15% {
    transform: translateY(0);
    opacity: 1;
  }
  100% {
    transform: translateY(-16px);
    opacity: 0;
  }
}

.cultivate-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-sm);
}

.inline-icon {
  font-size: 1rem;
}

.settlement {
  border-top: 1px solid var(--paper-deeper);
  padding-top: var(--space-sm);
}

.settlement__title {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  font-size: 0.82rem;
  color: var(--ink-mid);
}

.settlement__tip {
  margin-top: var(--space-xs);
  font-size: 0.72rem;
  color: var(--ink-light);
}

@media (min-width: 760px) {
  .feature-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .feature-item {
    flex-direction: column;
    align-items: flex-start;
    min-height: 132px;
  }

  .feature-item__icon {
    font-size: 1.6rem;
  }
}
</style>
