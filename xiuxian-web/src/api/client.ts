/** API client – wraps fetch with Telegram MiniApp auth */

const BASE = import.meta.env.VITE_API_BASE || ''
let actorUserId = ''
const PLAYER_ID_CACHE_PREFIX = 'xx_player_id_by_tg:'

/** Telegram WebApp instance (injected by TWA runtime) */
function getTwa(): any {
  return (window as any).Telegram?.WebApp
}

/** Get Telegram initData for server-side auth */
export function getTwaInitData(): string {
  return getTwa()?.initData || ''
}

export function getTwaUser(): { id: number; first_name: string } | null {
  return getTwa()?.initDataUnsafe?.user || null
}

export function setActorUserId(userId: string | number | null | undefined) {
  actorUserId = userId == null ? '' : String(userId)
}

function readCachedPlayerId(telegramId: string): string | null {
  try {
    const raw = localStorage.getItem(`${PLAYER_ID_CACHE_PREFIX}${telegramId}`)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { userId?: string; at?: number }
    const userId = String(parsed?.userId || '').trim()
    if (!userId) return null
    return userId
  } catch {
    return null
  }
}

function writeCachedPlayerId(telegramId: string, userId: string) {
  try {
    localStorage.setItem(
      `${PLAYER_ID_CACHE_PREFIX}${telegramId}`,
      JSON.stringify({ userId, at: Date.now() }),
    )
  } catch {
    // ignore cache write errors
  }
}

/** Typed fetch wrapper */
async function request<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BASE}${path}`
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  // Attach TWA auth
  const initData = getTwaInitData()
  if (initData) {
    headers['X-Telegram-Init-Data'] = initData
  }
  if (actorUserId) {
    headers['X-Actor-User-Id'] = actorUserId
  }

  const res = await fetch(url, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw Object.assign(new Error(body.message || res.statusText), {
      status: res.status,
      body,
    })
  }
  return res.json()
}

/** GET */
export function get<T = any>(path: string): Promise<T> {
  return request<T>(path)
}

/** POST */
export function post<T = any>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body != null ? JSON.stringify(body) : undefined,
  })
}

// ── Game API calls ──────────────────────────────

export interface InitData {
  user: Record<string, any>
  equipment: Record<string, any>
  skills: any[]
  story: { pending_claims: any[]; available_chapters: any[] }
  quests: any[]
}

/** Batch init – one request to get everything */
export async function fetchInit(userId: string): Promise<InitData> {
  const [stat, story] = await Promise.all([
    get(`/api/stat/${userId}`),
    get(`/api/story/volumes/${userId}`),
  ])
  return {
    user: stat.status || {},
    equipment: {},
    skills: [],
    story: {
      pending_claims: [],
      available_chapters: story.available_chapters || [],
    },
    quests: [],
  }
}

interface LookupUserResponse {
  user_id: string
}

interface RegisterResponse {
  success: boolean
  user_id?: string
}

export async function resolveOrCreatePlayerIdByTelegram(): Promise<string | null> {
  const twaUser = getTwaUser()
  if (!twaUser?.id) return null

  const platformId = String(twaUser.id)
  const cached = readCachedPlayerId(platformId)
  if (cached) return cached

  const query = new URLSearchParams({
    platform: 'telegram',
    platform_id: platformId,
  })

  try {
    const found = await get<LookupUserResponse>(`/api/user/lookup?${query.toString()}`)
    const resolved = String(found.user_id || '').trim()
    if (resolved) writeCachedPlayerId(platformId, resolved)
    return resolved || null
  } catch (err: any) {
    if (err?.status !== 404) throw err
  }

  const fallbackName = `${twaUser.first_name || '修士'}${platformId.slice(-4)}`
  const created = await post<RegisterResponse>('/api/register', {
    platform: 'telegram',
    platform_id: platformId,
    username: fallbackName,
  })
  if (!created?.success) return null
  const resolved = String(created.user_id || '').trim()
  if (resolved) writeCachedPlayerId(platformId, resolved)
  return resolved || null
}

/** Story: read next lines */
export function storyRead(userId: string, chapterId: string, count = 5) {
  return post('/api/story/read', { user_id: userId, chapter_id: chapterId, count })
}

/** Story: reset chapter */
export function storyReread(userId: string, chapterId: string) {
  return post('/api/story/reread', { user_id: userId, chapter_id: chapterId })
}

// ── Hunt / Combat ───────────────────────────────

export function hunt(userId: string, monsterId: string, useActive = true) {
  return post('/api/hunt', {
    user_id: userId,
    monster_id: monsterId,
    use_active: useActive,
  })
}

export function getHuntStatus(userId: string) {
  return get(`/api/hunt/status/${userId}`)
}

export function getMonsters(userId?: string) {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  return get(`/api/monsters${query}`)
}

export function turnStart(userId: string, monsterId: string) {
  return post('/api/hunt/turn/start', { user_id: userId, monster_id: monsterId })
}

export function turnAction(userId: string, action: string) {
  return post('/api/hunt/turn/action', { user_id: userId, action })
}

// ── Skills ──────────────────────────────────────

export function getSkills(userId: string) {
  return get(`/api/skills/${userId}`)
}

export function learnSkill(userId: string, skillId: string) {
  return post('/api/skills/learn', { user_id: userId, skill_id: skillId })
}

export function equipSkill(userId: string, skillId: string, slot: number) {
  return post('/api/skills/equip', { user_id: userId, skill_id: skillId, slot })
}

export function unequipSkill(userId: string, slot: number) {
  return post('/api/skills/unequip', { user_id: userId, slot })
}

export function upgradeSkill(userId: string, skillId: string) {
  return post('/api/skills/upgrade', { user_id: userId, skill_id: skillId })
}

// ── Shop ────────────────────────────────────────

export function getShop() {
  return get('/api/shop')
}

export function buyItem(userId: string, itemId: string, quantity = 1) {
  return post('/api/shop/buy', { user_id: userId, item_id: itemId, quantity })
}

export function useItem(userId: string, itemInstanceId: string) {
  return post('/api/item/use', { user_id: userId, item_instance_id: itemInstanceId })
}

// ── Bag ─────────────────────────────────────────

export function getItems(userId: string) {
  return get(`/api/items/${userId}`)
}

export function equipItem(userId: string, itemInstanceId: string, slot: string) {
  return post('/api/equip', { user_id: userId, item_instance_id: itemInstanceId, slot })
}

// ── PVP ─────────────────────────────────────────

export function getPvpOpponents(userId: string) {
  return get(`/api/pvp/opponents/${userId}`)
}

export function pvpChallenge(userId: string, targetId: string) {
  return post('/api/pvp/challenge', { user_id: userId, target_id: targetId })
}

// ── Alchemy ─────────────────────────────────────

export function getAlchemyRecipes() {
  return get('/api/alchemy/recipes')
}

export function brew(userId: string, recipeId: string) {
  return post('/api/alchemy/brew', { user_id: userId, recipe_id: recipeId })
}

// ── Signin ──────────────────────────────────────

export function signin(userId: string) {
  return post('/api/signin', { user_id: userId })
}

export function getSigninStatus(userId: string) {
  return get(`/api/signin/${userId}`)
}

// ── Breakthrough ────────────────────────────────

export function getBreakthroughPreview(userId: string, strategy = 'normal') {
  return get(`/api/breakthrough/preview/${userId}?strategy=${strategy}`)
}

export function breakthrough(userId: string, strategy: string, usePill = false) {
  return post('/api/breakthrough', { user_id: userId, strategy, use_pill: usePill })
}
