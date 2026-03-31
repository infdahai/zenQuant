<script setup lang="ts">
defineProps({
  title: { type: String, default: "" },
});
</script>

<template>
  <article class="panel">
    <div v-if="title" class="panel-title">{{ title }}</div>
    <slot />
  </article>
</template>

<style scoped>
.panel {
  background: #111820;
  border: 1px solid rgba(55, 90, 120, 0.2);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
}

.panel-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #6b7f94;
  margin-bottom: 10px;
}
</style>
