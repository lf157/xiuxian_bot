<script setup lang="ts">
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import IcMap from '~icons/mdi/map'
import IcMapMarker from '~icons/mdi/map-marker-radius'
import IcRoute from '~icons/mdi/map-marker-path'
import IcFlash from '~icons/mdi/lightning-bolt'
import IcRefresh from '~icons/mdi/refresh'
import IcLock from '~icons/mdi/lock'
import { get, post } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

interface MapNode {
  id: string
  name: string
  desc: string
  region: string
  region_name: string
  world_tier: number
  spirit_density: number
  min_realm: number
  is_current: boolean
  is_adjacent: boolean
  visited: boolean
  unlocked: boolean
  unlock_reason: string
  can_travel: boolean
  travel_cost: number
  travel_time_desc: string
  travel_block_reason: string
  adjacent_ids: string[]
  actions: Array<{ action: string; label: string }>
}

const player = usePlayerStore()
const loading = ref(false)
const movingTo = ref('')
const errorText = ref('')
const world = ref<{ tier: number; name: string; desc: string } | null>(null)
const nodes = ref<MapNode[]>([])
const selectedNodeId = ref('')

const graphRoot = ref<HTMLDivElement | null>(null)
let cy: Core | null = null

const selectedNode = computed(() => {
  if (!nodes.value.length) return null
  const picked = nodes.value.find((n) => n.id === selectedNodeId.value)
  if (picked) return picked
  return nodes.value.find((n) => n.is_current) || null
})

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await loadMap()
})

onBeforeUnmount(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

watch(nodes, async () => {
  await nextTick()
  renderGraph()
})

function regionKey(node: MapNode): string {
  return node.region_name || node.region || '未知区域'
}

function buildPositions(mapNodes: MapNode[]) {
  const regionOrder = Array.from(new Set(mapNodes.map((n) => regionKey(n))))
  const regionIndex = new Map(regionOrder.map((name, idx) => [name, idx]))

  const grouped: Record<string, MapNode[]> = {}
  for (const node of mapNodes) {
    const key = regionKey(node)
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(node)
  }

  const positions: Record<string, { x: number; y: number }> = {}
  for (const [key, groupNodes] of Object.entries(grouped)) {
    groupNodes.sort((a, b) => {
      if (a.is_current) return -1
      if (b.is_current) return 1
      if (a.unlocked !== b.unlocked) return a.unlocked ? -1 : 1
      return a.name.localeCompare(b.name, 'zh-Hans-CN')
    })

    const row = regionIndex.get(key) || 0
    groupNodes.forEach((node, col) => {
      positions[node.id] = {
        x: col * 170,
        y: row * 170,
      }
    })
  }

  return positions
}

function buildGraphElements(mapNodes: MapNode[]): ElementDefinition[] {
  const positions = buildPositions(mapNodes)
  const nodeMap = new Map(mapNodes.map((n) => [n.id, n]))
  const elements: ElementDefinition[] = []
  const edgeKeys = new Set<string>()

  for (const node of mapNodes) {
    const classes = [
      node.is_current ? 'current' : '',
      node.is_adjacent ? 'adjacent' : '',
      node.unlocked ? 'unlocked' : 'locked',
      node.can_travel ? 'travelable' : '',
    ].filter(Boolean).join(' ')

    elements.push({
      data: {
        id: node.id,
        label: node.name,
        region: regionKey(node),
      },
      position: positions[node.id],
      classes,
    })

    for (const targetId of node.adjacent_ids || []) {
      if (!nodeMap.has(targetId)) continue
      const edgeKey = [node.id, targetId].sort().join('::')
      if (edgeKeys.has(edgeKey)) continue
      edgeKeys.add(edgeKey)

      const target = nodeMap.get(targetId)!
      const edgeClasses = node.is_current || target.is_current ? 'edge-active' : ''
      elements.push({
        data: {
          id: `e:${edgeKey}`,
          source: node.id,
          target: targetId,
        },
        classes: edgeClasses,
      })
    }
  }

  return elements
}

function renderGraph() {
  if (!graphRoot.value || !nodes.value.length) return

  const elements = buildGraphElements(nodes.value)

  if (cy) {
    cy.destroy()
    cy = null
  }

  cy = cytoscape({
    container: graphRoot.value,
    elements,
    layout: { name: 'preset', fit: true, padding: 30 },
    minZoom: 0.45,
    maxZoom: 2.2,
    wheelSensitivity: 0.18,
    boxSelectionEnabled: false,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': '#b8ad9e',
          'border-color': '#8a7f72',
          'border-width': 2,
          width: 20,
          height: 20,
          label: 'data(label)',
          color: '#2d2520',
          'font-size': 10,
          'font-weight': 600,
          'text-wrap': 'wrap',
          'text-max-width': 110,
          'text-valign': 'bottom',
          'text-margin-y': 8,
        },
      },
      {
        selector: 'node.current',
        style: {
          'background-color': '#c03030',
          'border-color': '#861f1f',
          width: 24,
          height: 24,
          color: '#1a1a1a',
        },
      },
      {
        selector: 'node.adjacent.unlocked',
        style: {
          'background-color': '#4a8c6f',
          'border-color': '#2e6d53',
        },
      },
      {
        selector: 'node.locked',
        style: {
          'background-color': '#d9cebf',
          'border-style': 'dashed',
          'border-color': '#9f9588',
          opacity: 0.75,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-color': '#b8860b',
          'border-width': 3,
          'overlay-color': '#b8860b',
          'overlay-opacity': 0.08,
        },
      },
      {
        selector: 'edge',
        style: {
          width: 2,
          'line-color': '#d9cebf',
          'curve-style': 'bezier',
          opacity: 0.9,
        },
      },
      {
        selector: 'edge.edge-active',
        style: {
          width: 3,
          'line-color': '#5ea882',
        },
      },
    ],
  })

  cy.on('tap', 'node', (evt) => {
    const id = String(evt.target.id())
    selectedNodeId.value = id
  })

  const defaultSelected = selectedNode.value?.id
  if (defaultSelected) {
    const ele = cy.getElementById(defaultSelected)
    if (ele.nonempty()) ele.select()
    selectedNodeId.value = defaultSelected
  }
}

async function loadMap() {
  if (!player.userId) {
    errorText.value = '未识别到角色，请重新打开 MiniApp'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    const r = await get<{ world: { tier: number; name: string; desc: string }; maps: MapNode[] }>(
      `/api/travel/map/${player.userId}`,
    )
    world.value = r.world
    nodes.value = r.maps || []
    selectedNodeId.value = (r.maps || []).find((n) => n.is_current)?.id || ''
  } catch (e: any) {
    errorText.value = e?.body?.message || '地图加载失败'
  } finally {
    loading.value = false
  }
}

function statusText(node: MapNode): string {
  if (node.is_current) return '当前位置'
  if (!node.unlocked) return node.unlock_reason || '未解锁'
  if (node.can_travel) return `可前往 · 精力 -${node.travel_cost}`
  if (node.is_adjacent) return node.travel_block_reason || '暂不可前往'
  return '非相邻区域'
}

async function travelTo(node: MapNode) {
  if (!player.userId || !node.can_travel || movingTo.value) return
  movingTo.value = node.id
  try {
    const r = await post<{ success: boolean; first_visit_text?: string; to_name?: string }>(
      '/api/travel',
      { user_id: player.userId, to_map: node.id },
    )
    if (r.first_visit_text) {
      alert(r.first_visit_text)
    } else if (r.to_name) {
      alert(`已到达：${r.to_name}`)
    }
    await player.init(true)
    await loadMap()
  } catch (e: any) {
    alert(e?.body?.message || '移动失败')
  } finally {
    movingTo.value = ''
  }
}
</script>

<template>
  <div class="map-page">
    <div v-if="loading" class="map-loading card">
      <div class="loading-spinner"></div>
      <p>山河图卷展开中…</p>
    </div>

    <div v-else-if="errorText" class="map-error card">
      <h3>地图加载失败</h3>
      <p>{{ errorText }}</p>
      <button class="btn btn-primary" @click="loadMap">
        <IcRefresh class="icon" />
        重试
      </button>
    </div>

    <template v-else>
      <div class="card card--decorated map-header">
        <div class="map-header__left">
          <IcMap class="icon icon--main" />
          <div>
            <div class="map-header__title">大地图 · {{ world?.name || '未知世界' }}</div>
            <div class="map-header__desc">{{ world?.desc || '' }}</div>
          </div>
        </div>
        <button class="btn btn-ghost map-refresh" @click="loadMap">
          <IcRefresh class="icon" />
        </button>
        <hr class="divider">
        <div class="map-header__meta">
          <span><IcMapMarker class="icon" />{{ selectedNode?.name || player.currentMap }}</span>
          <span><IcFlash class="icon" />精力 {{ player.raw.stamina ?? '-' }}</span>
          <span>🔮{{ player.realmName }}</span>
        </div>
      </div>

      <div class="card map-graph-card">
        <div ref="graphRoot" class="map-graph"></div>
      </div>

      <div v-if="selectedNode" class="card node-panel">
        <div class="node-panel__title">
          <span>{{ selectedNode.name }}</span>
          <span v-if="selectedNode.is_current">📍</span>
          <span v-else-if="!selectedNode.unlocked"><IcLock class="icon" /></span>
          <span v-else-if="selectedNode.is_adjacent"><IcRoute class="icon" /></span>
        </div>
        <p class="node-panel__desc">{{ selectedNode.desc }}</p>
        <div class="node-panel__meta">
          <span>灵气 × {{ selectedNode.spirit_density.toFixed(1) }}</span>
          <span>{{ statusText(selectedNode) }}</span>
        </div>
        <button
          class="btn btn-primary btn-block"
          :disabled="!selectedNode.can_travel || selectedNode.is_current || movingTo === selectedNode.id"
          @click="travelTo(selectedNode)"
        >
          <IcRoute class="icon" />
          {{ movingTo === selectedNode.id ? '前往中...' : selectedNode.is_current ? '已在此地' : '前往此地' }}
        </button>
      </div>

      <div class="card map-legend">
        <span><span class="dot dot--current"></span>当前位置</span>
        <span><span class="dot dot--adjacent"></span>相邻可达</span>
        <span><span class="dot dot--locked"></span>未解锁</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.map-page { padding: var(--space-lg); padding-bottom: 90px; display: flex; flex-direction: column; gap: var(--space-md); }
.map-loading, .map-error { min-height: 42vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--space-sm); text-align: center; color: var(--ink-light); }

.icon { width: 1rem; height: 1rem; vertical-align: -0.15em; }
.icon--main { width: 1.4rem; height: 1.4rem; color: var(--ink-dark); }

.map-header { padding: var(--space-md); }
.map-header__left { display: flex; align-items: center; gap: var(--space-sm); }
.map-header__title { font-size: 1rem; font-weight: 700; color: var(--ink-black); }
.map-header__desc { color: var(--ink-mid); font-size: 0.8rem; margin-top: 2px; }
.map-refresh { position: absolute; right: var(--space-md); top: var(--space-md); padding: 6px 10px; }
.map-header__meta { display: flex; gap: var(--space-md); flex-wrap: wrap; color: var(--ink-mid); font-size: 0.76rem; }
.map-header__meta span { display: inline-flex; align-items: center; gap: 4px; }

.map-graph-card { padding: 0; overflow: hidden; }
.map-graph {
  width: 100%;
  height: 52vh;
  min-height: 360px;
  background:
    radial-gradient(ellipse at 20% 20%, rgba(184,134,11,0.08), transparent 45%),
    radial-gradient(ellipse at 80% 90%, rgba(74,140,111,0.08), transparent 40%),
    linear-gradient(180deg, #f8f3eb 0%, #efe6d8 100%);
}

.node-panel { padding: var(--space-md); }
.node-panel__title { display: flex; align-items: center; justify-content: space-between; font-size: 0.95rem; font-weight: 700; color: var(--ink-dark); }
.node-panel__desc { margin-top: var(--space-xs); color: var(--ink-mid); font-size: 0.78rem; line-height: 1.5; }
.node-panel__meta { margin: var(--space-sm) 0; display: flex; justify-content: space-between; gap: var(--space-sm); color: var(--ink-light); font-size: 0.72rem; }

.map-legend { display: flex; justify-content: space-between; gap: var(--space-sm); padding: var(--space-sm) var(--space-md); color: var(--ink-mid); font-size: 0.72rem; }
.map-legend span { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; border: 1px solid rgba(0,0,0,0.15); }
.dot--current { background: var(--cinnabar); }
.dot--adjacent { background: var(--jade); }
.dot--locked { background: #d9cebf; }

@media (max-width: 720px) {
  .map-graph { height: 46vh; min-height: 320px; }
}
</style>
