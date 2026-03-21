<script setup lang="ts">
import { usePlayerStore } from '@/stores/player'
import { get, post } from '@/api/client'

const player = usePlayerStore()

// ── State ──
const state = ref<'idle' | 'cultivating' | 'settling'>('idle')
const startTime = ref(0)
const gainPerHour = ref(0)
const boostInfo = ref('')
const elapsed = ref(0)
const currentGain = ref(0)
const anchorTime = ref(0)
const anchorGain = ref(0)
const gainBursts = ref<Array<{ id: number; text: string }>>([])
let gainBurstSeq = 0

// Settlement result
const result = ref<Record<string, any> | null>(null)
const canBreakthrough = ref(false)

// Breakthrough
const btState = ref<'idle' | 'previewing' | 'executing' | 'done'>('idle')
const btPreview = ref<Record<string, any> | null>(null)
const btResult = ref<Record<string, any> | null>(null)
const btStrategy = ref('normal')

let tickTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await checkStatus()
})

onUnmounted(() => {
  if (tickTimer) clearInterval(tickTimer)
})

// ── Check if already cultivating ──
async function checkStatus() {
  if (!player.userId) return
  try {
    const r = await get<any>(`/api/cultivate/status/${player.userId}`)
    if (r.state) {
      state.value = 'cultivating'
      startTime.value = Number(r.start_time || Math.floor(Date.now() / 1000))
      currentGain.value = Math.max(0, Number(r.current_gain || 0))
      const hours = Math.max(0, Number(r.hours || 0))
      gainPerHour.value = hours > 0
        ? Math.max(1, Math.round(currentGain.value / hours))
        : 200
      anchorGain.value = currentGain.value
      anchorTime.value = Math.floor(Date.now() / 1000)
      startTick()
    }
  } catch { /* not cultivating */ }
}

// ── Start cultivation ──
async function startCultivate() {
  if (!player.userId) return
  try {
    const r = await post<any>('/api/cultivate/start', { user_id: player.userId })
    if (r.success === false) {
      alert(r.message || '无法开始修炼')
      return
    }
    state.value = 'cultivating'
    startTime.value = Number(r.start_time || Math.floor(Date.now() / 1000))
    gainPerHour.value = Number(r.gain_per_hour || 200)
    currentGain.value = 0
    anchorGain.value = 0
    anchorTime.value = Math.floor(Date.now() / 1000)
    gainBursts.value = []
    const boosts: string[] = []
    if (r.sprint_boost_applied) boosts.push(`冲刺丹 x${r.sprint_boost_mult}`)
    if (r.sect_cultivation_bonus_pct > 0) boosts.push(`宗门加成 +${r.sect_cultivation_bonus_pct}%`)
    boostInfo.value = boosts.join(' · ')
    startTick()
  } catch (e: any) {
    alert(e.body?.message || '修炼失败')
  }
}

// ── Tick: update elapsed time and estimated gain ──
function startTick() {
  if (tickTimer) clearInterval(tickTimer)
  tick()
  tickTimer = setInterval(tick, 1000)
}

function tick() {
  if (state.value !== 'cultivating') return
  const now = Math.floor(Date.now() / 1000)
  elapsed.value = Math.max(0, now - startTime.value)
  if (anchorTime.value <= 0) {
    anchorTime.value = now
    anchorGain.value = currentGain.value
  }

  const elapsedAtAnchor = Math.max(0, anchorTime.value - startTime.value)
  const remainCapSeconds = Math.max(0, 120 * 3600 - elapsedAtAnchor)
  const deltaSeconds = Math.max(0, now - anchorTime.value)
  const effectiveDelta = Math.min(deltaSeconds, remainCapSeconds)
  const nextGain = anchorGain.value + Math.floor((gainPerHour.value * effectiveDelta) / 3600)

  if (nextGain > currentGain.value) {
    emitGainBurst(nextGain - currentGain.value)
  }
  currentGain.value = Math.max(currentGain.value, nextGain)
}

// ── End cultivation ──
async function endCultivate() {
  if (!player.userId) return
  state.value = 'settling'
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null }
  try {
    const r = await post('/api/cultivate/end', { user_id: player.userId })
    result.value = r
    canBreakthrough.value = !!r.can_breakthrough
    // Refresh player data
    await player.init(true)
  } catch (e: any) {
    result.value = { success: false, message: e.body?.message || '结算失败' }
  }
}

// ── Back to idle ──
function reset() {
  state.value = 'idle'
  result.value = null
  btState.value = 'idle'
  btPreview.value = null
  btResult.value = null
  elapsed.value = 0
  currentGain.value = 0
  anchorTime.value = 0
  anchorGain.value = 0
  gainBursts.value = []
}

function emitGainBurst(delta: number) {
  if (!Number.isFinite(delta) || delta <= 0) return

  if (delta <= 6) {
    for (let i = 0; i < delta; i += 1) pushGainBurst('+1')
    return
  }

  for (let i = 0; i < 6; i += 1) pushGainBurst('+1')
  pushGainBurst(`+${delta - 6}`)
}

function pushGainBurst(text: string) {
  const id = ++gainBurstSeq
  gainBursts.value.push({ id, text })
  setTimeout(() => {
    gainBursts.value = gainBursts.value.filter((b) => b.id !== id)
  }, 950)
}

// ── Breakthrough preview ──
async function previewBreakthrough(strategy: string) {
  btStrategy.value = strategy
  btState.value = 'previewing'
  try {
    const r = await get(`/api/breakthrough/preview/${player.userId}?strategy=${strategy}`)
    btPreview.value = r.preview || r
  } catch {
    btPreview.value = null
    btState.value = 'idle'
  }
}

// ── Execute breakthrough ──
async function doBreakthrough() {
  btState.value = 'executing'
  try {
    const r = await post('/api/breakthrough', {
      user_id: player.userId,
      strategy: btStrategy.value,
      use_pill: true,
    })
    btResult.value = r
    btState.value = 'done'
    await player.init(true)
  } catch (e: any) {
    btResult.value = { success: false, message: e.body?.message || '突破失败' }
    btState.value = 'done'
  }
}

// ── Format helpers ──
function fmtTime(s: number) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}时${m}分`
  if (m > 0) return `${m}分${sec}秒`
  return `${sec}秒`
}
</script>

<template>
  <div class="cult-page">

    <!-- ═══ 闲置状态 ═══ -->
    <template v-if="state === 'idle' && !result">
      <div class="cult-idle">
        <div class="cult-idle__art">
          <div class="mountain"></div>
          <div class="cloud cloud--1"></div>
          <div class="cloud cloud--2"></div>
        </div>

        <div class="card card--decorated">
          <h2 class="cult-title">闭关修炼</h2>
          <p class="cult-desc">
            盘膝入定，引天地灵气入体。<br>
            修炼时间越长，所得修为越丰。
          </p>
          <hr class="divider">
          <div class="cult-info">
            <div class="cult-info__row">
              <span>当前境界</span>
              <span class="text-gold">{{ player.realmName }}</span>
            </div>
            <div class="cult-info__row">
              <span>当前修为</span>
              <span>{{ player.exp.toLocaleString() }}</span>
            </div>
          </div>
          <button class="btn btn-primary btn-block" style="margin-top:var(--space-lg)" @click="startCultivate">
            入定修炼
          </button>
        </div>
      </div>
    </template>

    <!-- ═══ 修炼中 ═══ -->
    <template v-if="state === 'cultivating'">
      <div class="cult-active">
        <!-- 打坐动画 -->
        <div class="cult-scene">
          <div class="cult-figure">🧘</div>
          <div class="qi-particle qi-particle--1"></div>
          <div class="qi-particle qi-particle--2"></div>
          <div class="qi-particle qi-particle--3"></div>
          <div class="ripple ripple--1"></div>
          <div class="ripple ripple--2"></div>
          <transition-group name="gain-float" tag="div" class="gain-float-list">
            <span v-for="pulse in gainBursts" :key="pulse.id" class="gain-float-item">{{ pulse.text }}</span>
          </transition-group>
        </div>

        <div class="card card--decorated">
          <h2 class="cult-title">闭关中</h2>
          <p v-if="boostInfo" class="cult-boost">{{ boostInfo }}</p>

          <div class="cult-meter">
            <div class="cult-meter__label">已修炼</div>
            <div class="cult-meter__time">{{ fmtTime(elapsed) }}</div>
          </div>

          <div class="cult-meter">
            <div class="cult-meter__label">实时修为</div>
            <div class="cult-meter__value text-gold">+{{ currentGain.toLocaleString() }}</div>
          </div>

          <div class="cult-meter">
            <div class="cult-meter__label">效率</div>
            <div class="cult-meter__value">{{ gainPerHour }} / 时辰</div>
          </div>

          <hr class="divider">

          <button class="btn btn-cinnabar btn-block" @click="endCultivate">
            出关结算
          </button>
          <p class="cult-tip">修炼时间越长收获越多，上限五日</p>
        </div>
      </div>
    </template>

    <!-- ═══ 结算中 ═══ -->
    <template v-if="state === 'settling' && !result">
      <div class="cult-settling">
        <div class="loading-spinner" style="margin:auto"></div>
        <p style="text-align:center;margin-top:var(--space-md);color:var(--ink-light)">收功凝气中…</p>
      </div>
    </template>

    <!-- ═══ 结算结果 ═══ -->
    <template v-if="result">
      <div class="cult-result fade-in">
        <div class="card card--decorated">
          <h2 class="cult-title">出关</h2>
          <hr class="divider">

          <div class="result-row result-row--main">
            <span>获得修为</span>
            <span class="text-gold result-big">+{{ (result.gain || 0).toLocaleString() }}</span>
          </div>

          <div v-if="result.spirit_stone_low_reward" class="result-row">
            <span>灵石</span>
            <span class="text-gold">+{{ result.spirit_stone_low_reward }}</span>
          </div>

          <div class="result-row">
            <span>修炼时长</span>
            <span>{{ result.hours ? result.hours.toFixed(1) + '时辰' : '-' }}</span>
          </div>

          <div class="result-row">
            <span>总修为</span>
            <span>{{ (result.total_exp || player.exp).toLocaleString() }}</span>
          </div>

          <p v-if="result.tip" class="cult-tip">{{ result.tip }}</p>

          <hr class="divider">

          <!-- 突破入口 -->
          <template v-if="canBreakthrough && btState === 'idle'">
            <p class="text-cinnabar" style="text-align:center;font-weight:600;margin-bottom:var(--space-sm)">
              灵气充盈，可尝试突破！
            </p>
            <div class="bt-strategies">
              <button class="btn btn-ghost btn-block" @click="previewBreakthrough('normal')">普通突破</button>
              <button class="btn btn-ghost btn-block" @click="previewBreakthrough('steady')">稳妥突破</button>
              <button class="btn btn-ghost btn-block" @click="previewBreakthrough('protect')">护脉突破</button>
              <button class="btn btn-ghost btn-block" @click="previewBreakthrough('desperate')">生死突破</button>
            </div>
          </template>

          <!-- 突破预览 -->
          <template v-if="btState === 'previewing' && btPreview">
            <div class="bt-preview fade-in">
              <h3>{{ btPreview.strategy_name || btStrategy }}</h3>
              <div class="result-row">
                <span>成功率</span>
                <span :class="(btPreview.success_rate_pct||0) >= 50 ? 'text-gold' : 'text-cinnabar'">
                  {{ btPreview.success_rate_pct || 0 }}%
                </span>
              </div>
              <div class="result-row"><span>消耗灵石</span><span>{{ btPreview.cost_copper || 0 }}</span></div>
              <div class="result-row"><span>心魔值</span><span>{{ btPreview.pity || 0 }} / {{ btPreview.pity_threshold || 50 }}</span></div>
              <div v-if="btPreview.rate_parts" class="bt-parts">
                <span v-for="p in btPreview.rate_parts" :key="p" class="bt-part">{{ p }}</span>
              </div>
              <div class="bt-actions">
                <button class="btn btn-ghost" @click="btState='idle'; btPreview=null">返回</button>
                <button class="btn btn-cinnabar" @click="doBreakthrough">冲关！</button>
              </div>
            </div>
          </template>

          <!-- 突破执行中 -->
          <template v-if="btState === 'executing'">
            <div style="text-align:center;padding:var(--space-lg)">
              <div class="loading-spinner" style="margin:auto"></div>
              <p class="text-dim" style="margin-top:var(--space-sm)">渡劫中…</p>
            </div>
          </template>

          <!-- 突破结果 -->
          <template v-if="btState === 'done' && btResult">
            <div class="bt-result fade-in" :class="btResult.success ? 'bt-result--success' : 'bt-result--fail'">
              <div class="bt-result__icon">{{ btResult.success ? '⚡' : '💨' }}</div>
              <div class="bt-result__title">{{ btResult.success ? '突破成功！' : '突破失败' }}</div>
              <div v-if="btResult.new_realm" class="bt-result__realm">{{ btResult.new_realm }}</div>
              <p v-if="btResult.message" class="bt-result__msg">{{ btResult.message }}</p>
              <div v-if="btResult.exp_lost" class="result-row"><span>修为损失</span><span class="text-cinnabar">-{{ btResult.exp_lost }}</span></div>
              <div v-if="btResult.gold_reward" class="result-row"><span>灵石奖励</span><span class="text-gold">+{{ btResult.gold_reward }}</span></div>
            </div>
          </template>

          <button class="btn btn-primary btn-block" style="margin-top:var(--space-lg)" @click="reset">返回</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cult-page { padding: var(--space-lg); padding-bottom: 80px; min-height: calc(100dvh - 60px); }

/* ── 闲置 ── */
.cult-idle__art {
  position: relative; height: 120px; margin-bottom: var(--space-lg); overflow: hidden;
}
.mountain {
  position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
  width: 0; height: 0;
  border-left: 80px solid transparent; border-right: 80px solid transparent;
  border-bottom: 90px solid var(--paper-deeper);
  opacity: 0.5;
}
.cloud {
  position: absolute; width: 60px; height: 20px;
  background: var(--paper-dark); border-radius: 10px; opacity: 0.6;
}
.cloud--1 { top: 20px; left: 15%; animation: cloud-drift 12s ease-in-out infinite; }
.cloud--2 { top: 40px; right: 10%; animation: cloud-drift 15s ease-in-out infinite reverse; }
@keyframes cloud-drift { 0%,100%{transform:translateX(0)} 50%{transform:translateX(20px)} }

.cult-title { font-size: 1.1rem; text-align: center; margin-bottom: var(--space-sm); }
.cult-desc { font-size: 0.8rem; color: var(--ink-light); text-align: center; line-height: 1.8; }
.cult-info { display: flex; flex-direction: column; gap: var(--space-xs); }
.cult-info__row { display: flex; justify-content: space-between; font-size: 0.85rem; }

/* ── 修炼中 ── */
.cult-active { display: flex; flex-direction: column; gap: var(--space-lg); }

.cult-scene {
  position: relative; height: 160px; display: flex; align-items: center; justify-content: center;
}
.cult-figure {
  font-size: 3rem;
  animation: cultivate-float 4s ease-in-out infinite;
  filter: drop-shadow(0 4px 12px rgba(123,94,167,0.3));
  z-index: 2;
}
.qi-particle {
  position: absolute; width: 6px; height: 6px; border-radius: 50%;
  background: var(--purple-qi); opacity: 0.7;
}
.qi-particle--1 { animation: qi-orbit 4s linear infinite; }
.qi-particle--2 { animation: qi-orbit 5.5s linear infinite reverse; width:4px;height:4px;background:var(--gold); }
.qi-particle--3 { animation: qi-orbit 7s linear infinite; width:5px;height:5px;background:var(--azure); }

.ripple {
  position: absolute; width: 60px; height: 60px; border-radius: 50%;
  border: 1px solid var(--purple-qi); opacity: 0;
}
.ripple--1 { animation: ink-ripple 3s ease-out infinite; }
.ripple--2 { animation: ink-ripple 3s ease-out 1.5s infinite; }

.gain-float-list {
  position: absolute;
  top: 14px;
  right: 22%;
  display: flex;
  gap: 4px;
  pointer-events: none;
}

.gain-float-item {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--gold);
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.8);
}

.gain-float-enter-active {
  animation: gain-float-up 0.92s ease-out;
}

.gain-float-leave-active {
  transition: opacity 0.2s;
}

.gain-float-leave-to {
  opacity: 0;
}

@keyframes gain-float-up {
  0% {
    transform: translateY(8px);
    opacity: 0;
  }
  16% {
    transform: translateY(0);
    opacity: 1;
  }
  100% {
    transform: translateY(-20px);
    opacity: 0;
  }
}

.cult-boost {
  text-align: center; font-size: 0.75rem; color: var(--jade);
  background: rgba(74,140,111,0.08); padding: 4px 8px; border-radius: var(--radius-sm);
  margin-bottom: var(--space-sm);
}

.cult-meter {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: var(--space-xs) 0;
}
.cult-meter__label { font-size: 0.8rem; color: var(--ink-light); }
.cult-meter__time { font-size: 1.4rem; font-weight: 700; color: var(--ink-dark); font-family: var(--font-mono); }
.cult-meter__value { font-size: 1.1rem; font-weight: 700; }

.cult-tip { font-size: 0.7rem; color: var(--ink-light); text-align: center; margin-top: var(--space-sm); }

/* ── 结算 ── */
.cult-settling { display:flex;flex-direction:column;justify-content:center;min-height:50vh; }

.result-row {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: var(--space-xs) 0; font-size: 0.85rem;
}
.result-row--main { font-size: 1rem; }
.result-big { font-size: 1.3rem; font-weight: 700; }

/* ── 突破 ── */
.bt-strategies { display: flex; flex-direction: column; gap: var(--space-xs); }
.bt-preview h3 { font-size: 0.95rem; text-align: center; margin-bottom: var(--space-sm); }
.bt-parts { display: flex; flex-wrap: wrap; gap: 4px; margin: var(--space-sm) 0; }
.bt-part {
  font-size: 0.65rem; padding: 2px 6px; border-radius: 2px;
  background: var(--paper-dark); color: var(--ink-mid);
}
.bt-actions { display: flex; gap: var(--space-sm); margin-top: var(--space-md); }
.bt-actions .btn { flex: 1; }

.bt-result { text-align: center; padding: var(--space-lg) 0; }
.bt-result__icon { font-size: 2.5rem; margin-bottom: var(--space-sm); }
.bt-result__title { font-size: 1.1rem; font-weight: 700; }
.bt-result--success .bt-result__title { color: var(--gold); }
.bt-result--fail .bt-result__title { color: var(--ink-light); }
.bt-result__realm { font-size: 1.3rem; font-weight: 700; color: var(--cinnabar); margin: var(--space-xs) 0; }
.bt-result__msg { font-size: 0.8rem; color: var(--ink-light); margin: var(--space-sm) 0; }
</style>
