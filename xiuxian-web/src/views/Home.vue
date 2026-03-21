<script setup lang="ts">
import { usePlayerStore } from '@/stores/player'

const player = usePlayerStore()
</script>

<template>
  <div class="home">
    <div v-if="player.loading" class="home__loading"><div class="loading-spinner"></div></div>

    <template v-else-if="player.loaded">
      <!-- 道号卡 -->
      <div class="card card--decorated dao-card">
        <div class="dao-card__top">
          <div class="dao-card__seal qi-glow">{{ player.username.charAt(0) || '仙' }}</div>
          <div class="dao-card__info">
            <div class="dao-card__name">{{ player.username }}</div>
            <div class="dao-card__realm">{{ player.realmName }}</div>
            <div class="dao-card__uid">角色ID：{{ player.userId }}</div>
          </div>
          <div class="dao-card__element">{{ player.element || '无' }}灵根</div>
        </div>

        <hr class="divider">

        <!-- 气血 / 灵力 -->
        <div class="bars">
          <div class="bar-row">
            <span class="bar-label">气血</span>
            <div class="progress-bar"><div class="progress-bar__fill" :style="{ width: player.hpPercent+'%', background: 'var(--cinnabar)' }"></div></div>
            <span class="bar-value">{{ player.hp }}/{{ player.maxHp }}</span>
          </div>
          <div class="bar-row">
            <span class="bar-label">灵力</span>
            <div class="progress-bar"><div class="progress-bar__fill" :style="{ width: player.mpPercent+'%', background: 'var(--azure)' }"></div></div>
            <span class="bar-value">{{ player.mp }}/{{ player.maxMp }}</span>
          </div>
        </div>

        <hr class="divider">

        <!-- 四维属性 -->
        <div class="stats">
          <div class="stat"><span class="stat__label">攻</span><span class="stat__val">{{ player.attack }}</span></div>
          <div class="stat"><span class="stat__label">防</span><span class="stat__val">{{ player.defense }}</span></div>
          <div class="stat"><span class="stat__label">修为</span><span class="stat__val">{{ player.exp.toLocaleString() }}</span></div>
          <div class="stat"><span class="stat__label">下品灵石</span><span class="stat__val text-gold">{{ player.copper.toLocaleString() }}</span></div>
        </div>

        <div class="wallet-row">
          <span>中品灵石</span>
          <span class="text-gold">{{ player.gold.toLocaleString() }}</span>
        </div>
      </div>

      <!-- 快捷入口 -->
      <div class="quick">
        <router-link to="/cultivate" class="quick__btn">
          <span class="quick__icon">🧘</span><span>闭关修炼</span>
        </router-link>
        <router-link to="/story" class="quick__btn">
          <span class="quick__icon">📜</span><span>仙卷剧情</span>
        </router-link>
      </div>
    </template>

    <div v-else class="home__empty">气机紊乱，请刷新重试</div>
  </div>
</template>

<style scoped>
.home { padding: var(--space-lg); padding-bottom: 80px; }
.home__loading, .home__empty { display:flex;justify-content:center;align-items:center;min-height:60vh;color:var(--ink-light); }

/* 道号卡 */
.dao-card__top { display:flex;align-items:center;gap:var(--space-md); }
.dao-card__seal {
  width:50px;height:50px;border-radius:50%;flex-shrink:0;
  background: linear-gradient(135deg, var(--cinnabar), #8b2020);
  display:flex;align-items:center;justify-content:center;
  font-size:1.3rem;font-weight:700;color:#f5e6c8;
  border: 2px solid rgba(184,134,11,0.4);
}
.dao-card__info { flex:1; }
.dao-card__name { font-size:1.15rem;font-weight:700;color:var(--ink-black); }
.dao-card__realm { font-size:0.8rem;color:var(--gold);font-weight:600; }
.dao-card__uid { font-size:0.65rem;color:var(--ink-light);margin-top:2px; }
.dao-card__element { font-size:0.7rem;color:var(--ink-light);background:var(--paper-dark);padding:2px 8px;border-radius:var(--radius-sm); }

/* 气血灵力条 */
.bars { display:flex;flex-direction:column;gap:var(--space-sm); }
.bar-row { display:flex;align-items:center;gap:var(--space-sm); }
.bar-label { font-size:0.7rem;color:var(--ink-light);width:28px;text-align:right;flex-shrink:0; }
.bar-value { font-size:0.65rem;color:var(--ink-light);width:64px;text-align:right;flex-shrink:0;font-family:var(--font-mono); }
.bar-row .progress-bar { flex:1; }

/* 四维 */
.stats { display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-sm); }
.stat { display:flex;flex-direction:column;align-items:center;gap:2px; }
.stat__label { font-size:0.65rem;color:var(--ink-light); }
.stat__val { font-size:0.9rem;font-weight:700;color:var(--ink-dark); }
.wallet-row {
  margin-top: var(--space-sm);
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-size:0.8rem;
  color:var(--ink-mid);
}

/* 快捷入口 */
.quick { display:flex;gap:var(--space-sm);margin-top:var(--space-lg); }
.quick__btn {
  flex:1;display:flex;align-items:center;justify-content:center;gap:var(--space-sm);
  padding:var(--space-md);border-radius:var(--radius-md);
  background:var(--paper-dark);border:1px solid var(--paper-deeper);
  color:var(--ink-dark);font-weight:600;font-size:0.85rem;
  transition: all var(--duration-fast);
  text-decoration:none;
}
.quick__btn:active { transform:scale(0.97);background:var(--paper-deeper); }
.quick__icon { font-size:1.2rem; }
</style>
