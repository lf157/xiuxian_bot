<script setup lang="ts">
/** 背包页 – 占位 */
</script>

<template>
  <div class="placeholder-page">
    <div class="placeholder-icon">🎒</div>
    <h2>背包</h2>
    <p>敬请期待</p>
  </div>
</template>

<style scoped>
.placeholder-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: calc(100vh - 80px);
  gap: var(--space-sm);
  color: var(--color-text-dim);
}
.placeholder-icon { font-size: 3rem; }
h2 { color: var(--color-text-bright); font-size: 1.1rem; }
p { font-size: 0.85rem; }
</style>
