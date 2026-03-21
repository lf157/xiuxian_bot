<script setup lang="ts">
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import IcRefresh from '~icons/mdi/refresh'
import IcNetwork from '~icons/mdi/account-network'
import IcBook from '~icons/mdi/book-open-page-variant'
import IcSword from '~icons/mdi/sword-cross'
import { get } from '@/api/client'
import { usePlayerStore } from '@/stores/player'

interface StoryChapter {
  chapter_id: string
  title: string
  volume_title: string
  current_line?: number
  total_lines?: number
}

interface CharacterNode {
  id: string
  name: string
  title: string
  camp: 'self' | 'ally' | 'neutral' | 'enemy'
  note: string
}

interface CharacterEdge {
  source: string
  target: string
  relation: string
  type: 'ally' | 'neutral' | 'enemy'
}

interface CharacterSeed {
  id: string
  name: string
  title: string
  camp: 'ally' | 'neutral' | 'enemy'
  note: string
  relationToPlayer: string
  links: Array<{ to: string; relation: string; type: 'ally' | 'neutral' | 'enemy' }>
}

const CHARACTER_SEEDS: CharacterSeed[] = [
  {
    id: 'chen_danshi',
    name: '陈丹师',
    title: '丹道宗师',
    camp: 'ally',
    note: '你在丹道上的重要引路人。',
    relationToPlayer: '师承',
    links: [{ to: 'mei', relation: '师徒', type: 'ally' }],
  },
  {
    id: 'mei',
    name: '梅师妹',
    title: '丹峰弟子',
    camp: 'ally',
    note: '与你并肩成长的同门。',
    relationToPlayer: '同门',
    links: [{ to: 'xuanji', relation: '互助', type: 'ally' }],
  },
  {
    id: 'xuanji',
    name: '星璇',
    title: '观星者',
    camp: 'neutral',
    note: '擅长推演天机，立场偏中立。',
    relationToPlayer: '情报',
    links: [{ to: 'fate_weaver', relation: '同道', type: 'neutral' }],
  },
  {
    id: 'mo_wuchang',
    name: '墨无常',
    title: '逆道长老',
    camp: 'enemy',
    note: '多次与你交锋，暗中布局。',
    relationToPlayer: '宿敌',
    links: [{ to: 'youming', relation: '盟约', type: 'enemy' }],
  },
  {
    id: 'fate_weaver',
    name: '命织者',
    title: '天机修士',
    camp: 'neutral',
    note: '掌握部分轮回线索。',
    relationToPlayer: '试探',
    links: [{ to: 'youming', relation: '交易', type: 'neutral' }],
  },
  {
    id: 'youming',
    name: '幽冥鬼帝',
    title: '冥域主宰',
    camp: 'enemy',
    note: '后期主线的重要敌对势力。',
    relationToPlayer: '对立',
    links: [],
  },
]

const player = usePlayerStore()

const loading = ref(false)
const errorText = ref('')
const chapterCount = ref(0)
const characters = ref<CharacterNode[]>([])
const relations = ref<CharacterEdge[]>([])
const selectedNodeId = ref('player')

const graphRoot = ref<HTMLDivElement | null>(null)
let cy: Core | null = null

const selectedNode = computed(() => {
  return characters.value.find((c) => c.id === selectedNodeId.value) || null
})

onMounted(async () => {
  if (!player.loaded && player.userId) {
    await player.init()
  }
  await loadRelations()
})

onBeforeUnmount(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

watch([characters, relations], async () => {
  await nextTick()
  renderGraph()
})

function buildGraphByProgress(chapters: StoryChapter[]) {
  const readChapters = chapters.filter((c) => Number(c.current_line || 0) > 0).length
  chapterCount.value = chapters.length

  const unlockedCount = Math.max(3, Math.min(CHARACTER_SEEDS.length, readChapters + 3))
  const unlockedSeeds = CHARACTER_SEEDS.slice(0, unlockedCount)

  const playerNode: CharacterNode = {
    id: 'player',
    name: player.username || '你',
    title: player.realmName || '修士',
    camp: 'self',
    note: '关系图会随着剧情推进逐步扩展。',
  }

  const nodes: CharacterNode[] = [
    playerNode,
    ...unlockedSeeds.map((s) => ({
      id: s.id,
      name: s.name,
      title: s.title,
      camp: s.camp,
      note: s.note,
    })),
  ]

  const nodeSet = new Set(nodes.map((n) => n.id))

  const edges: CharacterEdge[] = unlockedSeeds.map((seed) => ({
    source: 'player',
    target: seed.id,
    relation: seed.relationToPlayer,
    type: seed.camp === 'enemy' ? 'enemy' : seed.camp === 'ally' ? 'ally' : 'neutral',
  }))

  for (const seed of unlockedSeeds) {
    for (const link of seed.links) {
      if (!nodeSet.has(link.to)) continue
      edges.push({
        source: seed.id,
        target: link.to,
        relation: link.relation,
        type: link.type,
      })
    }
  }

  characters.value = nodes
  relations.value = edges
  selectedNodeId.value = 'player'
}

function buildElements(): ElementDefinition[] {
  const elements: ElementDefinition[] = []

  for (const node of characters.value) {
    elements.push({
      data: {
        id: node.id,
        label: `${node.name}\n${node.title}`,
      },
      classes: `camp-${node.camp}`,
    })
  }

  const seen = new Set<string>()
  for (const edge of relations.value) {
    const key = [edge.source, edge.target].sort().join('::')
    if (seen.has(key)) continue
    seen.add(key)

    elements.push({
      data: {
        id: `e:${key}`,
        source: edge.source,
        target: edge.target,
        label: edge.relation,
      },
      classes: `rel-${edge.type}`,
    })
  }

  return elements
}

function renderGraph() {
  if (!graphRoot.value || !characters.value.length) return

  if (cy) {
    cy.destroy()
    cy = null
  }

  cy = cytoscape({
    container: graphRoot.value,
    elements: buildElements(),
    layout: {
      name: 'concentric',
      concentric: (node) => (node.id() === 'player' ? 100 : 10),
      levelWidth: () => 1,
      minNodeSpacing: 80,
      padding: 24,
      animate: false,
    },
    minZoom: 0.4,
    maxZoom: 2.5,
    wheelSensitivity: 0.18,
    style: [
      {
        selector: 'node',
        style: {
          width: 52,
          height: 52,
          'background-color': '#d9cebf',
          'border-color': '#8a7f72',
          'border-width': 2,
          label: 'data(label)',
          'font-size': 9,
          'font-weight': 600,
          color: '#2d2520',
          'text-wrap': 'wrap',
          'text-max-width': 84,
          'text-valign': 'bottom',
          'text-margin-y': 8,
        },
      },
      {
        selector: 'node.camp-self',
        style: {
          width: 60,
          height: 60,
          'background-color': '#b8860b',
          'border-color': '#896509',
          color: '#1a1a1a',
        },
      },
      {
        selector: 'node.camp-ally',
        style: {
          'background-color': '#5ea882',
          'border-color': '#3f785c',
        },
      },
      {
        selector: 'node.camp-neutral',
        style: {
          'background-color': '#6b8fbe',
          'border-color': '#4a6f96',
        },
      },
      {
        selector: 'node.camp-enemy',
        style: {
          'background-color': '#c03030',
          'border-color': '#851f1f',
          color: '#fff5f5',
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 4,
          'border-color': '#1a1a1a',
          'overlay-opacity': 0.08,
          'overlay-color': '#1a1a1a',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 2.5,
          'line-color': '#b8ad9e',
          'curve-style': 'bezier',
          label: 'data(label)',
          'font-size': 8,
          color: '#5c534a',
          'text-background-color': '#f5f0e8',
          'text-background-opacity': 0.8,
          'text-background-padding': 2,
          'target-arrow-shape': 'triangle',
          'target-arrow-color': '#b8ad9e',
          'arrow-scale': 0.7,
        },
      },
      {
        selector: 'edge.rel-ally',
        style: {
          'line-color': '#5ea882',
          'target-arrow-color': '#5ea882',
        },
      },
      {
        selector: 'edge.rel-neutral',
        style: {
          'line-color': '#6b8fbe',
          'target-arrow-color': '#6b8fbe',
        },
      },
      {
        selector: 'edge.rel-enemy',
        style: {
          'line-color': '#c03030',
          'target-arrow-color': '#c03030',
        },
      },
    ],
  })

  cy.on('tap', 'node', (evt) => {
    selectedNodeId.value = String(evt.target.id())
  })

  const current = cy.getElementById(selectedNodeId.value)
  if (current.nonempty()) {
    current.select()
  } else {
    const playerNode = cy.getElementById('player')
    if (playerNode.nonempty()) playerNode.select()
  }
}

async function loadRelations() {
  if (!player.userId) {
    errorText.value = '未识别到角色，请重新打开 MiniApp'
    return
  }

  loading.value = true
  errorText.value = ''

  try {
    const r = await get<{ available_chapters?: StoryChapter[] }>(`/api/story/volumes/${player.userId}`)
    buildGraphByProgress(Array.isArray(r.available_chapters) ? r.available_chapters : [])
  } catch (e: any) {
    errorText.value = e?.body?.message || '关系图加载失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="relations-page">
    <div class="card card--decorated relations-header">
      <div class="relations-header__left">
        <IcNetwork class="icon icon--main" />
        <div>
          <div class="relations-header__title">人物关系图</div>
          <div class="relations-header__desc">基于剧情进度自动展开角色关系</div>
        </div>
      </div>
      <button class="btn btn-ghost" :disabled="loading" @click="loadRelations">
        <IcRefresh class="icon" />
        刷新
      </button>

      <hr class="divider">

      <div class="relations-header__meta">
        <span><IcBook class="icon" />章节 {{ chapterCount }}</span>
        <span><IcSword class="icon" />境界 {{ player.realmName }}</span>
      </div>
    </div>

    <div v-if="loading" class="card loading-box">
      <div class="loading-spinner"></div>
      <p>关系脉络推演中…</p>
    </div>

    <div v-else-if="errorText" class="card error-box">
      <h3>关系图加载失败</h3>
      <p>{{ errorText }}</p>
      <button class="btn btn-primary" @click="loadRelations">重试</button>
    </div>

    <template v-else>
      <div class="card graph-card">
        <div ref="graphRoot" class="graph-root"></div>
      </div>

      <div v-if="selectedNode" class="card node-panel fade-in">
        <div class="node-panel__title">
          <span>{{ selectedNode.name }}</span>
          <span class="node-panel__badge" :class="`node-panel__badge--${selectedNode.camp}`">
            {{ selectedNode.title }}
          </span>
        </div>
        <p class="node-panel__note">{{ selectedNode.note }}</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.relations-page {
  padding: var(--space-lg);
  padding-bottom: 84px;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.relations-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.relations-header__left {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
}

.relations-header__title {
  font-size: 1rem;
  font-weight: 700;
}

.relations-header__desc {
  font-size: 0.72rem;
  color: var(--ink-light);
}

.relations-header__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-md);
  color: var(--ink-mid);
  font-size: 0.78rem;
}

.icon {
  font-size: 1rem;
}

.icon--main {
  font-size: 1.4rem;
}

.loading-box,
.error-box {
  min-height: 180px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-sm);
}

.graph-card {
  padding: var(--space-sm);
}

.graph-root {
  width: 100%;
  height: 420px;
  border-radius: var(--radius-sm);
  background: radial-gradient(circle at 20% 15%, rgba(212, 160, 23, 0.1), transparent 38%),
    radial-gradient(circle at 80% 85%, rgba(74, 111, 165, 0.1), transparent 32%),
    #f2ebe0;
}

.node-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.node-panel__title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
  font-size: 0.95rem;
  font-weight: 700;
}

.node-panel__badge {
  font-size: 0.7rem;
  border-radius: 999px;
  padding: 2px 8px;
  background: var(--paper-dark);
}

.node-panel__badge--self {
  color: var(--gold);
}

.node-panel__badge--ally {
  color: var(--jade);
}

.node-panel__badge--neutral {
  color: var(--azure);
}

.node-panel__badge--enemy {
  color: var(--cinnabar);
}

.node-panel__note {
  font-size: 0.78rem;
  color: var(--ink-mid);
}

@media (max-width: 760px) {
  .graph-root {
    height: 350px;
  }
}
</style>
