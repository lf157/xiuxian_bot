/** Player state store */

import { defineStore } from 'pinia'
import { fetchInit, setActorUserId, type InitData } from '@/api/client'

const PLAYER_SNAPSHOT_CACHE_KEY = 'xx_player_snapshot_v1'
const PLAYER_CACHE_TTL_MS = Number(import.meta.env.VITE_PLAYER_CACHE_TTL_MS || 180000)

interface PlayerState {
  userId: string
  username: string
  realmNameServer: string
  rank: number
  exp: number
  copper: number
  gold: number
  hp: number
  maxHp: number
  mp: number
  maxMp: number
  attack: number
  defense: number
  element: string
  currentMap: string
  lastSyncAt: number
  loaded: boolean
  loading: boolean
  raw: Record<string, any>
}

export const usePlayerStore = defineStore('player', {
  state: (): PlayerState => ({
    userId: '',
    username: '',
    realmNameServer: '',
    rank: 1,
    exp: 0,
    copper: 0,
    gold: 0,
    hp: 100,
    maxHp: 100,
    mp: 50,
    maxMp: 50,
    attack: 10,
    defense: 5,
    element: '',
    currentMap: 'canglan_city',
    lastSyncAt: 0,
    loaded: false,
    loading: false,
    raw: {},
  }),

  getters: {
    hpPercent: (s) => (s.maxHp > 0 ? Math.round((s.hp / s.maxHp) * 100) : 0),
    mpPercent: (s) => (s.maxMp > 0 ? Math.round((s.mp / s.maxMp) * 100) : 0),
    realmName: (s) => s.realmNameServer || REALM_NAMES[s.rank] || `???`,
  },

  actions: {
    setUserId(id: string) {
      this.userId = id
      setActorUserId(id)
    },

    async init(force = false) {
      if (!this.userId || this.loading) return

      if (!force && this.hydrateFromCache()) {
        return
      }

      this.loading = true
      try {
        const data: InitData = await fetchInit(this.userId)
        this.applyUserData(data.user)
        this.loaded = true
        this.persistCache()
      } finally {
        this.loading = false
      }
    },

    applyUserData(u: Record<string, any>) {
      this.raw = u
      this.username = u.in_game_username || u.username || ''
      this.realmNameServer = u.realm_name || ''
      this.rank = u.rank || 1
      this.exp = u.exp || 0
      this.copper = u.copper || 0
      this.gold = u.gold || 0
      this.hp = u.hp || 100
      this.maxHp = u.max_hp || 100
      this.mp = u.mp || 50
      this.maxMp = u.max_mp || 50
      this.attack = u.attack || 10
      this.defense = u.defense || 5
      this.element = u.element || ''
      this.currentMap = u.current_map || 'canglan_city'
      this.lastSyncAt = Date.now()
    },

    hydrateFromCache() {
      try {
        const raw = localStorage.getItem(PLAYER_SNAPSHOT_CACHE_KEY)
        if (!raw) return false
        const parsed = JSON.parse(raw) as {
          userId?: string
          savedAt?: number
          user?: Record<string, any>
        }
        const cachedUserId = String(parsed?.userId || '').trim()
        const savedAt = Number(parsed?.savedAt || 0)
        const user = parsed?.user
        if (!cachedUserId || cachedUserId !== this.userId || !user || !savedAt) return false
        if (Date.now() - savedAt > PLAYER_CACHE_TTL_MS) return false

        this.applyUserData(user)
        this.lastSyncAt = savedAt
        this.loaded = true
        return true
      } catch {
        return false
      }
    },

    persistCache() {
      if (!this.userId || !this.raw || Object.keys(this.raw).length === 0) return
      try {
        localStorage.setItem(
          PLAYER_SNAPSHOT_CACHE_KEY,
          JSON.stringify({
            userId: this.userId,
            savedAt: this.lastSyncAt || Date.now(),
            user: this.raw,
          }),
        )
      } catch {
        // ignore cache write failures
      }
    },
  },
})

const REALM_NAMES: Record<number, string> = {
  1: '凡人',
  2: '练气一层', 3: '练气二层', 4: '练气三层',
  5: '练气四层', 6: '练气五层', 7: '练气六层',
  8: '筑基初期', 9: '筑基中期', 10: '筑基后期', 11: '筑基圆满',
  12: '金丹初期', 13: '金丹中期', 14: '金丹后期', 15: '金丹圆满',
  16: '元婴初期', 17: '元婴中期', 18: '元婴后期', 19: '元婴圆满',
  20: '化神初期', 21: '化神中期', 22: '化神后期', 23: '化神圆满',
  24: '合体初期', 25: '合体中期', 26: '合体后期', 27: '合体圆满',
  28: '渡劫初期', 29: '渡劫中期', 30: '渡劫后期', 31: '渡劫圆满',
  32: '大乘初期', 33: '大乘中期', 34: '大乘后期', 35: '大乘圆满',
  36: '渡仙劫',
}
