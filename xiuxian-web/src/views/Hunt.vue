<script setup lang="ts">
import IcSword from '~icons/mdi/sword-cross'
import IcShield from '~icons/mdi/shield-half-full'
import IcFlask from '~icons/mdi/flask-round-bottom'
import IcRun from '~icons/mdi/run-fast'
import IcRefresh from '~icons/mdi/refresh'
import { usePlayerStore } from '@/stores/player'
import { hunt, turnStart, turnAction, getHuntStatus, getMonsters } from '@/api/client'

type Phase = 'idle' | 'loading' | 'battle' | 'result'

const player = usePlayerStore()
const router = useRouter()
const phase = ref<Phase>('idle')
const acting = ref(false)
const battleLog = ref<string[]>([])
const battleState = ref<Record<string, any>>({})
const resultData = ref<Record<string, any> | null>(null)
const logBox = ref<HTMLDivElement | null>(null)
const storyUnlocked = ref(false)
const defaultMonsterId = ref('')

async function ensureDefaultMonsterId(): Promise<string> {
  if (defaultMonsterId.value) return defaultMonsterId.value
  if (!player.userId) throw new Error('缺少角色参数')

  const r = await getMonsters(player.userId)
  const list = Array.isArray((r as any)?.monsters) ? (r as any).monsters : []
  const picked = list.find((m: any) => String(m?.monster_id || '').trim())?.monster_id
  const resolved = String(picked || '').trim()
  if (!resolved) throw new Error('未找到可狩猎目标')
  defaultMonsterId.value = resolved
  return resolved
}

onMounted(async () => {
  if (!player.loaded && player.userId) await player.init()
  await checkStatus()
})

async function checkStatus() {
  if (!player.userId) return
  try {
    const r = await getHuntStatus(player.userId)
    if (r?.in_battle) {
      battleState.value = r
      phase.value = 'battle'
    }
  } catch {}
}

async function startHunt() {
  if (!player.userId || acting.value) return
  phase.value = 'loading'
  acting.value = true
  battleLog.value = []
  try {
    const monsterId = await ensureDefaultMonsterId()
    const r = await turnStart(player.userId, monsterId)
    if (r.success === false) {
      alert(r.message || '无法狩猎')
      phase.value = 'idle'
      return
    }
    battleState.value = r
    battleLog.value.push(`遭遇 ${r.monster_name || '未知灵兽'}！`)
    phase.value = 'battle'
  } catch (e: any) {
    alert(e?.body?.message || '狩猎失败')
    phase.value = 'idle'
  } finally {
    acting.value = false
  }
}

async function doAction(action: string) {
  if (!player.userId || acting.value) return
  acting.value = true
  try {
    const r = await turnAction(player.userId, action)
    if (r.log) {
      for (const line of r.log) battleLog.value.push(line)
    } else if (r.message) {
      battleLog.value.push(r.message)
    }
    battleState.value = r

    await nextTick()
    logBox.value?.scrollTo({ top: logBox.value.scrollHeight, behavior: 'smooth' })

    if (r.finished || r.battle_over) {
      resultData.value = r
      phase.value = 'result'
      const prev = player.newChapterCount
      await player.init(true)
      if (player.newChapterCount > prev) storyUnlocked.value = true
    }
  } catch (e: any) {
    battleLog.value.push(e?.body?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

async function quickHunt() {
  if (!player.userId || acting.value) return
  acting.value = true
  phase.value = 'loading'
  battleLog.value = []
  try {
    const monsterId = await ensureDefaultMonsterId()
    const r = await hunt(player.userId, monsterId)
    resultData.value = r
    if (r.log) battleLog.value = r.log
    phase.value = 'result'
    const prev = player.newChapterCount
    await player.init(true)
    if (player.newChapterCount > prev) storyUnlocked.value = true
  } catch (e: any) {
    alert(e?.body?.message || '快速狩猎失败')
    phase.value = 'idle'
  } finally {
    acting.value = false
  }
}

function reset() {
  phase.value = 'idle'
  battleLog.value = []
  battleState.value = {}
  resultData.value = null
  storyUnlocked.value = false
}
</script>

<template>
  <div class="hunt-page">
    <!-- idle -->
    <template v-if="phase === 'idle'">
      <div class="card card--decorated">
        <h2 class="page-title"><IcSword class="icon" /> 狩猎</h2>
        <p class="text-dim" style="font-size:.82rem;margin:var(--space-sm) 0">
          前往灵兽出没之地，以战养战，获取修为与宝物。
        </p>
        <hr class="divider">
        <div class="hunt-actions">
          <button class="btn btn-primary btn-block" @click="startHunt">⚔️ 回合战斗</button>
          <button class="btn btn-ghost btn-block" @click="quickHunt">⚡ 快速狩猎</button>
        </div>
      </div>
    </template>

    <!-- loading -->
    <template v-if="phase === 'loading'">
      <div class="hunt-loading"><div class="loading-spinner"></div><p class="text-dim">寻找灵兽中…</p></div>
    </template>

    <!-- battle -->
    <template v-if="phase === 'battle'">
      <div class="card battle-card">
        <div class="battle-header">
          <div class="battle-entity">
            <span class="battle-name">{{ player.username }}</span>
            <div class="progress-bar"><div class="progress-bar__fill" :style="{ width: player.hpPercent+'%', background:'var(--cinnabar)' }"></div></div>
            <span class="battle-hp">{{ player.hp }}/{{ player.maxHp }}</span>
          </div>
          <span class="battle-vs">VS</span>
          <div class="battle-entity">
            <span class="battle-name">{{ battleState.monster_name || '灵兽' }}</span>
            <div class="progress-bar"><div class="progress-bar__fill" :style="{ width: (battleState.monster_hp_pct||100)+'%', background:'var(--cinnabar)' }"></div></div>
            <span class="battle-hp">{{ battleState.monster_hp || '?' }}</span>
          </div>
        </div>

        <div ref="logBox" class="battle-log">
          <p v-for="(line, i) in battleLog" :key="i" class="battle-log__line fade-in">{{ line }}</p>
        </div>

        <div class="battle-actions">
          <button class="btn btn-primary" :disabled="acting" @click="doAction('attack')"><IcSword class="icon" /> 攻击</button>
          <button class="btn btn-ghost" :disabled="acting" @click="doAction('defend')"><IcShield class="icon" /> 防御</button>
          <button class="btn btn-ghost" :disabled="acting" @click="doAction('skill')"><IcFlask class="icon" /> 技能</button>
          <button class="btn btn-ghost" :disabled="acting" @click="doAction('flee')"><IcRun class="icon" /> 逃跑</button>
        </div>
      </div>
    </template>

    <!-- result -->
    <template v-if="phase === 'result'">
      <div class="card card--decorated result-card fade-in">
        <h2 class="page-title">{{ resultData?.victory ? '⚔️ 胜利！' : '💨 战斗结束' }}</h2>
        <hr class="divider">
        <div v-if="resultData?.exp_gained" class="result-row"><span>获得修为</span><span class="text-gold">+{{ resultData.exp_gained }}</span></div>
        <div v-if="resultData?.copper_gained" class="result-row"><span>获得铜币</span><span class="text-gold">+{{ resultData.copper_gained }}</span></div>
        <div v-if="resultData?.drops?.length" class="result-drops">
          <span>掉落物品：</span>
          <span v-for="d in resultData.drops" :key="d.item_id" class="result-drop">{{ d.name || d.item_id }} x{{ d.quantity }}</span>
        </div>
        <div v-if="battleLog.length" class="battle-log battle-log--small">
          <p v-for="(line, i) in battleLog" :key="i">{{ line }}</p>
        </div>
        <div v-if="storyUnlocked" class="story-unlock fade-in" @click="router.push('/story')">
          <span>📖</span>
          <span class="story-unlock__text">新剧情已解锁，点击查看</span>
          <span>→</span>
        </div>
        <button class="btn btn-primary btn-block" style="margin-top:var(--space-md)" @click="reset"><IcRefresh class="icon" /> 继续狩猎</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hunt-page { padding: var(--space-lg); padding-bottom: 86px; }
.page-title { font-size: 1.05rem; display: flex; align-items: center; gap: var(--space-sm); }
.icon { width: 1rem; height: 1rem; }
.hunt-loading { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 50vh; gap: var(--space-sm); }
.hunt-actions { display: flex; flex-direction: column; gap: var(--space-sm); }

.battle-card { padding: var(--space-md); }
.battle-header { display: flex; align-items: center; gap: var(--space-sm); margin-bottom: var(--space-md); }
.battle-entity { flex: 1; }
.battle-name { font-size: .8rem; font-weight: 600; color: var(--ink-dark); }
.battle-hp { font-size: .65rem; color: var(--ink-light); font-family: var(--font-mono); }
.battle-vs { font-size: .75rem; color: var(--ink-faint); font-weight: 700; }

.battle-log { max-height: 200px; overflow-y: auto; margin-bottom: var(--space-md); padding: var(--space-sm); background: var(--paper-dark); border-radius: var(--radius-sm); font-size: .78rem; color: var(--ink-mid); line-height: 1.6; }
.battle-log--small { max-height: 120px; margin-top: var(--space-sm); }
.battle-log__line { margin-bottom: 2px; }
.battle-actions { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-xs); }

.result-card { }
.result-row { display: flex; justify-content: space-between; font-size: .88rem; padding: 4px 0; }
.result-drops { font-size: .82rem; margin-top: var(--space-sm); }
.result-drop { display: inline-block; background: var(--paper-dark); padding: 2px 8px; border-radius: var(--radius-sm); margin: 2px 4px 2px 0; font-size: .75rem; }

.story-unlock {
  display: flex; align-items: center; gap: var(--space-sm);
  margin-top: var(--space-sm); padding: var(--space-sm) var(--space-md);
  background: rgba(184, 134, 11, 0.08); border: 1px solid var(--gold);
  border-radius: var(--radius-sm); cursor: pointer;
}
.story-unlock:active { background: rgba(184, 134, 11, 0.15); }
.story-unlock__text { flex: 1; font-size: .82rem; font-weight: 600; color: var(--gold); }
</style>
